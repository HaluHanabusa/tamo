"""MCPサーバ: ツール実呼び出しとHTTP認証ゲート（mcp extras導入時のみ実行）。"""
from __future__ import annotations

import asyncio
import json

import pytest

mcp_mod = pytest.importorskip("mcp", reason="pip install 'mcp[cli]' で有効化")


@pytest.fixture()
def seeded(tamo_home):
    from tamo.schema import make_event
    from tamo.store import Store

    s = Store(tamo_home)
    try:
        s.upsert_event(make_event(
            source_kind="claude_code", session_key="claude_code:s1", seq=0, actor="user",
            kind="message", content=[{"type": "text", "text": "MCP検証: 決定論で収集する"}],
            locator="t::0", ts="2026-07-08T00:00:00Z", native_id="n0"))
        s.commit()
    finally:
        s.close()
    return tamo_home


def test_list_sessions_tool_with_source(seeded):
    """過去に source= のシグネチャ不一致で100%クラッシュしていたツール。"""
    from tamo import mcp_server

    rows = json.loads(mcp_server.list_sessions(5, source="claude"))
    assert rows and rows[0]["source_kind"] == "claude_code"
    assert json.loads(mcp_server.list_sessions(5))  # source無しも動く


def test_recall_and_get_session_latest(seeded):
    from tamo import mcp_server

    assert "tamo recall" in mcp_server.recall("決定論")
    got = json.loads(mcp_server.get_session("latest:claude_code"))
    assert got["session_key"] == "claude_code:s1"


def test_token_gate():
    """streamable-http認証: 誤トークン=401(app未到達) / 正トークン・Bearer=通過 / lifespan素通し。"""
    from tamo.mcp_server import _TokenGate

    async def scenario():
        calls, sent = [], []

        async def app(scope, receive, send):
            calls.append(scope.get("path", scope["type"]))
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(msg):
            sent.append(msg)

        async def recv():
            return {"type": "http.request"}

        gate = _TokenGate(app, "SECRET")
        await gate({"type": "http", "path": "/mcp", "headers": [(b"x-tamo-token", b"WRONG")]}, recv, send)
        assert sent[0]["status"] == 401 and not calls
        sent.clear()
        await gate({"type": "http", "path": "/mcp", "headers": [(b"x-tamo-token", b"SECRET")]}, recv, send)
        assert calls == ["/mcp"]
        await gate({"type": "http", "path": "/mcp", "headers": [(b"authorization", b"Bearer SECRET")]}, recv, send)
        assert calls == ["/mcp", "/mcp"]
        await gate({"type": "lifespan"}, recv, send)
        assert calls[-1] == "lifespan"

    asyncio.run(scenario())
