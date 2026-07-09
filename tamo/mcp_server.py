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

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit("mcpパッケージが必要です: pip install 'mcp[cli]'") from e

from .optimize import build_pack
from .store import Store
from .util import tamo_home

mcp = FastMCP("tamo", instructions=(
    "tamoはローカルの複数AIエージェント（Claude Code / Cursor / Codex CLI / ブラウザ等）から"
    "決定論的に収集した会話コンテキストの保管庫。まず search_context か get_context_pack を使うこと。"
))


def _store() -> Store:
    return Store(tamo_home())


@mcp.tool()
def search_context(query: str, limit: int = 8, source: str = "") -> str:
    """過去の全エージェント会話を全文検索する（日本語部分一致・添付の中身も対象）。
    返り値の各hitは {event_id, session_key, ts, actor, snippet}。
    注: 「あの件どうなってた？」系の調査は、まず recall(query) の方が速い
    （検索+前後の顛末+添付根拠を1コールで合成して返す）。
    search_contextはヒット一覧だけ欲しい時・recallの結果から深掘りする時に使う。"""
    s = _store()
    try:
        return json.dumps(s.search(query, limit, source=source or None), ensure_ascii=False, indent=1)
    finally:
        s.close()


@mcp.tool()
def recall(query: str, budget_tokens: int = 3500, max_hits: int = 4, source: str = "") -> str:
    """「あの件どうなってた？」への一発ツール（最初にこれを呼ぶ）。
    ヒットした各セッションを「★一致箇所 / セッション全体の要点走査(遠い結論も拾う) /
    終盤(どう終わったか)」の3部ダイジェストに決定論合成して1コールで返す
    （小さいセッションは全文）。★=一致行、📎=添付根拠、⟨e:xxxx⟩=出所ID。
    「Geminiで聞いてた」「Claude(Web)で話してた」のような面指定があれば source を渡す —
    source_kindの部分一致: "gemini"→gemini_web / "chatgpt"→chatgpt_web /
    "claude_web"(=claude.ai) / "claude_code"(=CLI/IDE) / "claude"は両方 / "cursor" 等。
    これで足りない時だけ get_session / get_blob_text で深掘りする。"""
    from .recall import recall as _recall

    s = _store()
    try:
        return _recall(s, query, budget_tokens=budget_tokens, max_hits=max_hits,
                       source=source or None)
    finally:
        s.close()


@mcp.tool()
def get_context(event_id: str, before: int = 3, after: int = 6) -> str:
    """検索で当てたイベントの「前後」を読む（会話版 grep -C）。
    event_id は search_context の hit や ⟨e:xxxx⟩ の8桁短縮でOK。
    対象イベントに "_hit": true が付き、前後は同一セッションの元の順序。
    本文は1500字にスリム化済み（受け手の文脈を圧迫しない）。"""
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
    """トークン予算内に最適化された引き継ぎパック(Markdown)を得る。
    新しいセッションの冒頭に貼る用途を想定。queryで話題を絞れる。"""
    s = _store()
    try:
        events = s.iter_session_events(session_key) if session_key else s.recent_events(days=days)
        md, _ = build_pack(events, budget_tokens=budget_tokens, query=query)
        return md
    finally:
        s.close()


@mcp.tool()
def list_sessions(limit: int = 20, source: str = "") -> str:
    """収集済みセッションの一覧（新しい順）。
    source はsource_kindの部分一致絞り込み（"gemini"→gemini_web / "claude_code" 等、
    recall/search_contextと同じ意味）。空文字なら全ソース。"""
    s = _store()
    try:
        return json.dumps(s.list_sessions(limit, source=source or None), ensure_ascii=False, indent=1)
    finally:
        s.close()


@mcp.tool()
def get_session(session_key: str, max_events: int = 200,
                since_event_id: str = "", since_ts: str = "",
                compact: bool = True, max_chars: int = 60000) -> str:
    """セッションの正規化イベント(CES)を取得。引き継ぎの続き取得に対応:
    - session_key に "latest" / "latest:<source_kind>"（例 latest:claude_code）を指定すると
      最終活動が最新のセッションに解決される
    - since_event_id: そのイベントの次から（packや検索結果の ⟨e:xxxx⟩ 8桁でも可）。
      見つからない場合は events は空（全量の誤送を防ぐ安全側）
    - since_ts: そのISO時刻より後だけ / max_events: 末尾N件（既定200）
    - compact(既定True): 各イベントを {event_id, ts, actor, kind, text(〜1500字)} にスリム化し、
      応答全体も max_chars(既定60k字) に収める（古い側から間引き）— 受け手の
      コンテキストウィンドウを圧迫しないための既定。生のCES全文が要るときだけ compact=false
    返り値: {session_key, total, returned, truncated, events}"""
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
    """添付ファイル(PDF/docx/xlsx/pptx/テキスト)から決定論抽出済みのテキストを取得する。
    会話中の [blob ... sha=...] 参照の中身を読みたいときはまずこれ（base64より軽い）。"""
    s = _store()
    try:
        r = s.get_blob_text(sha256)
        return json.dumps(r or {"error": "not found"}, ensure_ascii=False)
    finally:
        s.close()


@mcp.tool()
def get_blob_base64(sha256: str) -> str:
    """CASから添付ファイル/画像をbase64で取得（5MBまで）。会話中の [blob sha=...] 参照を解決する。"""
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
                body = ("unauthorized: X-Tamo-Token ヘッダ（`tamo token` の値）が必要です。"
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
        raise SystemExit("mcp SDKが古くhttp認証を適用できません: pip install -U 'mcp[cli]'")
    import uvicorn

    mcp.settings.host = host
    mcp.settings.port = port
    print(f"[tamo] MCP: http://{host}:{port}/mcp （X-Tamo-Token 認証・値は `tamo token`）",
          file=sys.stderr)
    uvicorn.run(_TokenGate(app_factory(), inbox_token()), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="tamo MCP server")
    ap.add_argument("--http", action="store_true", help="streamable-httpで常駐（既定はstdio）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    a = ap.parse_args()
    run("streamable-http" if a.http else "stdio", a.host, a.port)
