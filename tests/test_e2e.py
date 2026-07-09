"""E2E: probe→collect→検索→pack の初回価値到達フローと冪等性。"""
from __future__ import annotations

import json
import sqlite3

from conftest import run_tamo


def _stats() -> dict:
    r = run_tamo("stats")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_first_run_journey(fixture_env, tmp_path):
    home, state = fixture_env

    # probe --write: 検出してsources.toml生成（UTF-8であること）
    r = run_tamo("probe", "--home", str(home), "--write")
    assert r.returncode == 0, r.stderr
    st = (state / "sources.toml").read_bytes()
    st.decode("utf-8")  # cp932で書かれていたら以後のcollectが全滅する（過去バグ）

    # collect初回: 全ソースからイベントが入り、quarantineはゼロ
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr
    s = _stats()
    assert s["events"] >= 15, s
    assert s["quarantine"] == 0, s
    assert {"claude_code", "cursor_ide", "codex_cli", "aider", "chatgpt_web"} <= set(s["per_source"]), s

    # 冪等: 2回目collectで件数が増えない
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr
    assert _stats()["events"] == s["events"]

    # スキーマ版数が刻印されている
    con = sqlite3.connect(state / "tamo.db")
    assert con.execute("PRAGMA user_version").fetchone()[0] >= 1
    con.close()

    # search: ヒット / 0件時は無言でなくメッセージ
    r = run_tamo("search", "スナップショット")
    assert r.returncode == 0 and "e:" in r.stdout, r.stdout + r.stderr
    r = run_tamo("search", "zzz存在しない語zzz")
    assert r.returncode == 0 and "該当なし" in r.stderr

    # pack --out: UTF-8で書かれ ⟨e:xxxx⟩ 出所IDを含む
    out = tmp_path / "pack.md"
    r = run_tamo("pack", "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "⟨e:" in out.read_text(encoding="utf-8")


def test_collect_before_probe_hints(tamo_home):
    """probe前のcollectは0件+誘導メッセージ（黙って0を出さない）。"""
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr
    assert "probe --write" in r.stderr


def test_quarantine_command_flow(tamo_home):
    """壊れたinbox → quarantineに原文隔離 → list/show/clearで運用できる。"""
    inbox = tamo_home / "inbox"
    inbox.mkdir()
    (inbox / "broken.json").write_text("{invalid json", encoding="utf-8")
    assert run_tamo("collect").returncode == 0

    r = run_tamo("quarantine")
    assert r.returncode == 0 and "#" in r.stdout, r.stdout
    qid = r.stdout.split("#")[1].split()[0]
    r = run_tamo("quarantine", "show", "--id", qid)
    assert r.returncode == 0 and "invalid json" in r.stdout  # 原文が見える

    assert run_tamo("quarantine", "clear").returncode == 2  # --yes必須
    r = run_tamo("quarantine", "clear", "--yes")
    assert r.returncode == 0 and "cleared" in r.stdout
