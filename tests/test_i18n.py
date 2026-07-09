"""i18n: 英語辞書の網羅(ASTスキャン)・placeholder一致・言語切替。

設計(docs/ARCHITECTURE.md参照): msgid=ソース中の日本語。翻訳対象はUIメッセージのみで、
pack/recall/mirror/rules本文や保存データの語彙は決定論成果物なので対象外。
"""
from __future__ import annotations

import ast
from pathlib import Path
from string import Formatter

import pytest

from conftest import REPO, run_tamo
from tamo.i18n import _EN, t

PKG = REPO / "tamo"


def _collect_msgids() -> list[tuple[str, str]]:
    """tamo/*.py の全 t() 呼び出しからmsgidを抽出（モジュール定数渡しも解決）。"""
    out: list[tuple[str, str]] = []
    for py in sorted(PKG.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        consts = {n.targets[0].id: n.value.value
                  for n in tree.body
                  if isinstance(n, ast.Assign) and len(n.targets) == 1
                  and isinstance(n.targets[0], ast.Name)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "t" and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.append((py.name, a.value))
                elif isinstance(a, ast.Name) and a.id in consts:
                    out.append((py.name, consts[a.id]))
                else:  # f-string等をt()に渡すとキーが揺れて翻訳が壊れる — 禁止
                    pytest.fail(f"{py.name}: t() の第1引数が文字列リテラル/モジュール定数でない: {ast.dump(a)[:120]}")
    return out


MSGIDS = _collect_msgids()


def test_en_dictionary_covers_all_msgids():
    """未訳キーはenユーザーに日本語が出る。辞書の抜けをビルド時に止める。"""
    missing = sorted({m for _, m in MSGIDS if m not in _EN})
    assert not missing, f"_EN に未訳が {len(missing)} 件:\n" + "\n---\n".join(missing[:5])


def test_en_dictionary_has_no_orphans():
    """コードから消えたmsgidが辞書に残り続けるのを防ぐ（メンテ腐敗の検知）。"""
    used = {m for _, m in MSGIDS}
    orphans = sorted(k for k in _EN if k not in used)
    assert not orphans, f"_EN に未使用キーが {len(orphans)} 件:\n" + "\n---\n".join(orphans[:5])


def _fields(s: str) -> set[str]:
    return {fname for _, fname, _, _ in Formatter().parse(s) if fname}


@pytest.mark.parametrize("msgid", sorted(_EN), ids=lambda m: m[:40])
def test_placeholders_match_and_format(msgid):
    en = _EN[msgid]
    assert _fields(msgid) == _fields(en), f"placeholder不一致: {msgid!r}"
    dummy = {f.split(".")[0].split("[")[0]: 1.0 for f in _fields(en)}
    en.format(**dummy)  # 書式エラーはここで即死する
    msgid.format(**dummy)


def test_language_switch(monkeypatch):
    monkeypatch.setenv("TAMO_LANG", "ja")
    assert t("中止しました") == "中止しました"
    monkeypatch.setenv("TAMO_LANG", "en")
    assert t("中止しました") == "Aborted"
    assert "PID 42" in t("[tamo] 残留ロックを回収しました（PID {pid} は動いていません）: {lock}",
                         pid=42, lock="x")


def test_cli_speaks_english(tamo_home):
    """en環境ではCLIの主要導線が英語で応答する（subprocessでの実地確認）。"""
    r = run_tamo("search", "zzz-no-such-term", env={"TAMO_LANG": "en"})
    assert r.returncode == 0 and "no matches" in r.stderr, r.stderr
    r = run_tamo("--help", env={"TAMO_LANG": "en"})
    assert "cross-agent context harvester" in r.stdout
    r = run_tamo("quarantine", env={"TAMO_LANG": "en"})
    assert "No quarantined data" in r.stdout


def test_cli_speaks_japanese(tamo_home):
    r = run_tamo("search", "zzz-no-such-term", env={"TAMO_LANG": "ja"})
    assert "該当なし" in r.stderr
