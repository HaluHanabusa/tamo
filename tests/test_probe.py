"""probe: WSL経由/ネイティブWindowsの走査、sources.toml退避。"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from conftest import run_tamo
from tamo.probe import run_probe


def _make_cursor_db(gs_dir) -> None:
    gs_dir.mkdir(parents=True)
    con = sqlite3.connect(gs_dir / "state.vscdb")
    con.execute("CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO cursorDiskKV VALUES('bubbleId:c1:b1', ?)",
                (json.dumps({"type": 1, "text": "hi"}),))
    con.commit()
    con.close()


def test_wsl_win_users_scan(tmp_path, monkeypatch):
    """WSL2の /mnt/c/Users 相当をTAMO_WIN_ROOTで模擬 → win:ユーザーのCursorを検出。"""
    winroot = tmp_path / "winusers"
    user = winroot / "hana"
    (user / "AppData").mkdir(parents=True)
    _make_cursor_db(user / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage")
    monkeypatch.setenv("TAMO_WIN_ROOT", str(winroot))

    res = run_probe(tmp_path / "linuxhome")
    keys = {s["key"] for s in res["sources"]}
    assert "cursor_ide:win:hana" in keys, keys


@pytest.mark.skipif(os.name != "nt", reason="ネイティブWindows限定（%APPDATA%走査）")
def test_native_windows_appdata_scan(tmp_path, monkeypatch):
    """ネイティブWindowsでは自分のホームの AppData 配下も走査する。"""
    monkeypatch.delenv("TAMO_WIN_ROOT", raising=False)
    monkeypatch.setenv("TAMO_WIN_ROOT", str(tmp_path / "no-such-dir"))  # /mnt/c側は無し
    home = tmp_path / "winhome"
    _make_cursor_db(home / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage")
    (home / ".claude" / "projects" / "p").mkdir(parents=True)
    (home / ".claude" / "projects" / "p" / "s.jsonl").write_text("{}\n", encoding="utf-8")

    res = run_probe(home)
    by_kind = {s["kind"]: s for s in res["sources"]}
    assert "cursor_ide" in by_kind and "win:" in by_kind["cursor_ide"]["key"]
    assert by_kind["claude_code"]["key"] == "claude_code:home"


def test_probe_write_backs_up_hand_edits(fixture_env):
    """--writeは手編集済みsources.tomlを.bakへ退避し差分を見せる（無警告上書きしない）。"""
    home, state = fixture_env
    assert run_tamo("probe", "--home", str(home), "--write").returncode == 0
    sp = state / "sources.toml"
    sp.write_text(sp.read_text(encoding="utf-8") + "\n# 手編集の行\n", encoding="utf-8")

    r = run_tamo("probe", "--home", str(home), "--write")
    assert r.returncode == 0, r.stderr
    bak = state / "sources.toml.bak"
    assert bak.exists() and "手編集の行" in bak.read_text(encoding="utf-8")
    assert "変更点" in r.stdout
