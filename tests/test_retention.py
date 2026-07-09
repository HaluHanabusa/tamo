"""保持期間まわり: 無期限既定の防波堤（日次サイズ警告）の動作。"""
from __future__ import annotations

from conftest import run_tamo
from tamo.cli import warn_db_size
from tamo.store import Store


def test_warn_db_size_thresholds(tamo_home, capsys):
    assert warn_db_size(0, usage_mb=99999.0) is False          # 0 = 無効化
    assert warn_db_size(2048, usage_mb=100.0) is False          # 閾値未満は沈黙
    assert warn_db_size(2048, usage_mb=3000.0) is True          # 超過で警告
    err = capsys.readouterr().err
    assert "3.0GB" in err and "warn_db_mb" in err and "prune" in err  # 対処まで案内する


def test_stats_warns_when_over_threshold(tamo_home):
    # 2MBの生データを入れて、閾値1MBの settings.toml で stats を実行
    s = Store(tamo_home)
    try:
        s.put_raw("t", "big::1", b"x" * (2 * 1024 * 1024))
        s.commit()
    finally:
        s.close()
    (tamo_home / "settings.toml").write_text("[retention]\nwarn_db_mb = 1\n", encoding="utf-8")

    r = run_tamo("stats")
    assert r.returncode == 0, r.stderr
    assert "警告閾値 1MB" in r.stderr, r.stderr


def test_stats_silent_under_default_threshold(tamo_home):
    r = run_tamo("stats")  # 空DBは既定閾値(2048MB)に遠く及ばない
    assert r.returncode == 0
    assert "警告閾値" not in r.stderr
