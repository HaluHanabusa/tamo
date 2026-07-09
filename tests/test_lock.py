"""単一書込ロック: 残留ロックの自動回収と、生存プロセスの尊重。"""
from __future__ import annotations

import os

from conftest import run_tamo


def test_stale_lock_is_reclaimed(tamo_home):
    """クラッシュ残留（持ち主不在）のロックは自動回収してcollectが成功する。"""
    (tamo_home / ".lock").write_text("999999999", encoding="utf-8")  # 存在しないPID
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr
    assert "残留ロック" in r.stderr
    assert not (tamo_home / ".lock").exists()  # 後始末される


def test_live_lock_is_respected(tamo_home):
    """持ち主が生きているロックは奪わない（単一書込者の保証）。"""
    (tamo_home / ".lock").write_text(str(os.getpid()), encoding="utf-8")  # このテスト自身=生存中
    r = run_tamo("collect")
    assert r.returncode == 2
    assert "別のtamoが実行中" in r.stderr
    assert (tamo_home / ".lock").exists()  # 他人のロックを消さない
    (tamo_home / ".lock").unlink()
