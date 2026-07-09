"""Store: 冪等・source絞り込み・quarantine・prune・スキーマ版数ガード。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tamo.schema import make_event
from tamo.store import SCHEMA_VERSION, Store


def _ev(sk: str = "t:s1", seq: int = 0, text: str = "hello", ts: str | None = None,
        source: str = "t") -> dict:
    return make_event(source_kind=source, session_key=sk, seq=seq, actor="user",
                      kind="message", content=[{"type": "text", "text": text}],
                      locator=f"test::{sk}#{seq}", ts=ts, native_id=f"n{seq}")


def test_upsert_idempotent(tamo_home):
    s = Store(tamo_home)
    try:
        assert s.upsert_event(_ev()) is True
        assert s.upsert_event(_ev()) is False  # 同一内容 → 同一event_id → 弾かれる
        assert s.con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        s.close()


def test_list_sessions_source_filter(tamo_home):
    """MCPの list_sessions(source=) が使う経路（過去にシグネチャ不一致で100%クラッシュ）。"""
    s = Store(tamo_home)
    try:
        s.upsert_event(_ev(sk="claude_code:a", source="claude_code"))
        s.upsert_event(_ev(sk="gemini_web:b", source="gemini_web", seq=1))
        s.commit()
        assert len(s.list_sessions(10)) == 2
        got = s.list_sessions(10, source="claude")
        assert len(got) == 1 and got[0]["source_kind"] == "claude_code"
        assert s.list_sessions(10, source="") == s.list_sessions(10)  # 空文字=全件
    finally:
        s.close()


def test_quarantine_dedup_and_api(tamo_home):
    s = Store(tamo_home)
    try:
        s.put_quarantine("t", "loc::1", b"payload", "same error")
        s.put_quarantine("t", "loc::1", b"payload", "same error")  # rescan再走を模擬
        s.put_quarantine("t", "loc::1", b"payload", "different error")
        s.commit()
        rows = s.quarantine_list(10)
        assert len(rows) == 2  # locator+errorの組で一意
        got = s.quarantine_get(rows[0]["id"])
        assert got and got["payload"] == "payload"
        assert s.quarantine_clear() == 2
    finally:
        s.close()


def test_prune_activity_time_based(tamo_home):
    """pruneは活動時刻基準・ts不明は保持・dry-runは消さない。"""
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    s = Store(tamo_home)
    try:
        s.upsert_event(_ev(sk="t:old", seq=0, text="古い", ts=old))
        s.upsert_event(_ev(sk="t:new", seq=1, text="新しい", ts=new))
        s.upsert_event(_ev(sk="t:nots", seq=2, text="ts不明"))  # ts=None
        s.commit()

        r = s.prune(7, dry_run=True)
        assert r["events"] == 1 and r["dry_run"] is True
        assert s.con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3  # dry-runは消さない

        r = s.prune(7)
        assert r["events"] == 1
        left = {row[0] for row in s.con.execute("SELECT session_key FROM events")}
        assert left == {"t:new", "t:nots"}  # 古いものだけ消え、ts不明は安全側で残る
    finally:
        s.close()


def test_schema_version_stamp_and_downgrade_guard(tamo_home):
    s = Store(tamo_home)
    s.close()
    con = sqlite3.connect(tamo_home / "tamo.db")
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION + 99}")  # 未来のDBを模擬
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="新しいtamo"):
        Store(tamo_home)
