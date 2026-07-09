"""tamo.mcp_server — 収集済みコンテキストを各エージェントへ配るMCPサーバ。

依存: `pip install "mcp[cli]"`（収集系は依存ゼロのまま。配布側だけ任意依存）
起動: `python -m tamo.mcp_server`（stdio）

Claude Code への登録例:
  claude mcp add tamo -- python -m tamo.mcp_server
Cursor / Codex CLI / その他MCPクライアントにも同じstdioコマンドで登録できる。
これで「収集はtamo、配布はMCP」の役割で、どのエージェントからでも
過去コンテキストを検索・パック取得できる。
"""
from __future__ import annotations

import base64
import json

from .i18n import t

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit(t("mcpパッケージが必要です: pip install 'mcp[cli]'")) from e

from .optimize import build_pack
from .store import Store
from .util import tamo_home

# ツール説明はLLM向けの固定英語（表示言語に依存させない）。日本語検索が効く旨は説明側に明記する
mcp = FastMCP("tamo", instructions=(
    "tamo is a local vault of conversation context harvested deterministically from "
    "multiple AI agents (Claude Code / Cursor / Codex CLI / browser chats). "
    "For 'what happened with X?' questions call `recall` first; use search_context / "
    "get_context_pack for narrower lookups."
))


def _store() -> Store:
    return Store(tamo_home())


@mcp.tool()
def search_context(query: str, limit: int = 8, source: str = "") -> str:
    """Full-text search across all harvested agent conversations (CJK substring
    matching works; attachment contents are indexed too). Each hit is
    {event_id, session_key, ts, actor, snippet}.
    Note: for "what happened with X?" investigations, `recall(query)` is usually
    better — it stitches matches + surrounding outcomes + attachment evidence in
    one call. Use search_context when you only want the hit list, or to dig
    further from a recall result."""
    s = _store()
    try:
        return json.dumps(s.search(query, limit, source=source or None), ensure_ascii=False, indent=1)
    finally:
        s.close()


@mcp.tool()
def recall(query: str, budget_tokens: int = 3500, max_hits: int = 4, source: str = "") -> str:
    """One-shot answer to "what happened with X?" — call this first.
    Deterministically stitches each matching session into a 3-part digest:
    ★ matched lines / a key-point scan of the whole session (catches distant
    conclusions) / how it ended (small sessions are returned in full).
    Legend: ★ = matched line, 📎 = attachment evidence, ⟨e:xxxx⟩ = provenance id.
    If the user names a surface ("I asked Gemini", "in Claude web"), pass
    `source` — substring match on source_kind: "gemini"→gemini_web,
    "chatgpt"→chatgpt_web, "claude_web" (claude.ai), "claude_code" (CLI/IDE),
    "claude" matches both, "cursor", etc. Only reach for get_session /
    get_blob_text when this is not enough."""
    from .recall import recall as _recall

    s = _store()
    try:
        return _recall(s, query, budget_tokens=budget_tokens, max_hits=max_hits,
                       source=source or None)
    finally:
        s.close()


@mcp.tool()
def get_context(event_id: str, before: int = 3, after: int = 6) -> str:
    """Read the surroundings of a matched event (grep -C for conversations).
    event_id accepts a search_context hit or the 8-char short form from ⟨e:xxxx⟩.
    The target event is flagged "_hit": true; neighbors come in original session
    order. Bodies are slimmed to 1500 chars to keep your context lean."""
    from .schema import blocks_text
    from .util import strip_noise, truncate

    s = _store()
    try:
        evs = s.events_around(event_id, before=before, after=after)
        if not evs:
            return json.dumps({"error": "event not found", "event_id": event_id}, ensure_ascii=False)
        slim = [{"event_id": e["event_id"], "ts": e.get("ts"), "actor": e.get("actor"),
                 "kind": e.get("kind"), **({"_hit": True} if e.get("_hit") else {}),
                 "text": truncate(strip_noise(blocks_text(e.get("content", []))), 1500)}
                for e in evs]
        return json.dumps({"session_key": evs[0].get("session_key"), "events": slim},
                          ensure_ascii=False)
    finally:
        s.close()


@mcp.tool()
def get_context_pack(budget_tokens: int = 6000, query: str = "", session_key: str = "", days: int = 14) -> str:
    """Get a handoff pack (Markdown) optimized into the given token budget.
    Meant to be pasted at the top of a fresh session; `query` narrows the topic."""
    s = _store()
    try:
        events = s.iter_session_events(session_key) if session_key else s.recent_events(days=days)
        md, _ = build_pack(events, budget_tokens=budget_tokens, query=query)
        return md
    finally:
        s.close()


@mcp.tool()
def list_sessions(limit: int = 20, source: str = "") -> str:
    """List harvested sessions, newest first. `source` filters by substring of
    source_kind ("gemini"→gemini_web, "claude_code", … — same semantics as
    recall/search_context); empty string means all sources."""
    s = _store()
    try:
        return json.dumps(s.list_sessions(limit, source=source or None), ensure_ascii=False, indent=1)
    finally:
        s.close()


@mcp.tool()
def get_session(session_key: str, max_events: int = 200,
                since_event_id: str = "", since_ts: str = "",
                compact: bool = True, max_chars: int = 60000) -> str:
    """Get a session's normalized events (CES), with resume/handoff support:
    - session_key accepts "latest" / "latest:<source_kind>" (e.g. latest:claude_code)
      to resolve the most recently active session
    - since_event_id: continue from just after that event (accepts the 8-char
      short id from packs / search results). Unknown ids return empty events
      (fail-safe against dumping everything)
    - since_ts: only events after that ISO timestamp / max_events: last N (default 200)
    - compact (default True): slims each event to {event_id, ts, actor, kind,
      text (~1500 chars)} and caps the whole response at max_chars (default 60k,
      trimming oldest first) so your context window is not flooded. Set
      compact=false only when you need raw CES.
    Returns: {session_key, total, returned, truncated, events}"""
    from .schema import blocks_text
    from .util import strip_noise, truncate

    s = _store()
    try:
        key = session_key
        if key == "latest" or key.startswith("latest:"):
            src = key.split(":", 1)[1] if ":" in key else None
            key = s.latest_session_key(src)
            if not key:
                return json.dumps({"error": "no sessions", "requested": session_key}, ensure_ascii=False)
        total = s.con.execute("SELECT COUNT(*) FROM events WHERE session_key=?", (key,)).fetchone()[0]
        evs = s.iter_session_events(key, since_event_id=since_event_id or None,
                                    since_ts=since_ts or None, tail=max_events)
        if compact:
            evs = [{"event_id": e["event_id"], "ts": e.get("ts"), "actor": e.get("actor"),
                    "kind": e.get("kind"),
                    "text": truncate(strip_noise(blocks_text(e.get("content", []))), 1500)}
                   for e in evs]
        truncated = False
        while evs and len(json.dumps(evs, ensure_ascii=False)) > max_chars:
            evs.pop(0)  # 予算超過は古い側から間引く（続き取得の意味を保つ）
            truncated = True
        return json.dumps({"session_key": key, "total": total, "returned": len(evs),
                           "truncated": truncated, "events": evs}, ensure_ascii=False)
    finally:
        s.close()


@mcp.tool()
def get_blob_text(sha256: str) -> str:
    """Get the deterministically-extracted text of an attachment (PDF/docx/xlsx/
    pptx/plain text). Reach for this first when resolving a [blob … sha=…]
    reference in a conversation — much lighter than base64."""
    s = _store()
    try:
        r = s.get_blob_text(sha256)
        return json.dumps(r or {"error": "not found"}, ensure_ascii=False)
    finally:
        s.close()


@mcp.tool()
def get_blob_base64(sha256: str) -> str:
    """Fetch the original attachment/image bytes from CAS as base64 (up to 5MB).
    Resolves [blob sha=…] references in conversations."""
    s = _store()
    try:
        p = s.cas.get_path(sha256)
        if not p:
            return json.dumps({"error": "not found"})
        data = p.read_bytes()
        if len(data) > 5 * 1024 * 1024:
            return json.dumps({"error": "too large", "bytes": len(data), "path": str(p)})
        row = s.con.execute("SELECT mime FROM blobs WHERE sha256=?", (sha256,)).fetchone()
        return json.dumps({"mime": row[0] if row else "application/octet-stream",
                           "bytes": len(data), "b64": base64.b64encode(data).decode()})
    finally:
        s.close()


class _TokenGate:
    """streamable-http用の最小認証ASGIラッパ。

    inboxの書込には最初からトークンが要るのに、収集した全会話+blob原物を返す
    読出（このMCP）が素通しでは非対称すぎる — 127.0.0.1バインドは「LANに出さない」
    であって「ローカルの任意プロセスを信用する」ではない。
    受理: `X-Tamo-Token: <token>` または `Authorization: Bearer <token>`（inbox.tokenと同一）。
    """

    def __init__(self, app, token: str):
        self.app, self._token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            import secrets

            hdrs = {k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers", [])}
            supplied = hdrs.get("x-tamo-token", "")
            auth = hdrs.get("authorization", "")
            if not supplied and auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
            if not secrets.compare_digest(supplied.encode(), self._token):
                body = t("unauthorized: X-Tamo-Token ヘッダ（`tamo token` の値）が必要です。"
                         "登録例: claude mcp add --transport http tamo http://127.0.0.1:8788/mcp"
                         " --header \"X-Tamo-Token: $(tamo token)\"").encode()
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def run(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8788) -> None:
    """MCPサーバを起動する。stdio(既定, クライアントが子プロセスとして起動)か、
    streamable-http(常駐サービス`tamo serve`用: 1プロセスを複数クライアントで共有)。
    httpはトークン必須（stdioはクライアント子プロセス=ユーザー本人なので不要）。"""
    if transport == "stdio":
        mcp.run()
        return
    import sys

    from .config import inbox_token

    app_factory = getattr(mcp, "streamable_http_app", None)
    if app_factory is None:
        # 認証を差し込めない旧SDKで、全会話を無認証公開する方が害が大きい → 起動を拒否
        raise SystemExit(t("mcp SDKが古くhttp認証を適用できません: pip install -U 'mcp[cli]'"))
    import uvicorn

    mcp.settings.host = host
    mcp.settings.port = port
    print(t("[tamo] MCP: http://{host}:{port}/mcp （X-Tamo-Token 認証・値は `tamo token`）",
            host=host, port=port), file=sys.stderr)
    uvicorn.run(_TokenGate(app_factory(), inbox_token()), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="tamo MCP server")
    ap.add_argument("--http", action="store_true", help="streamable-httpで常駐（既定はstdio）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    a = ap.parse_args()
    run("streamable-http" if a.http else "stdio", a.host, a.port)
