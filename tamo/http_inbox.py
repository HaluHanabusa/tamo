"""tamo.http_inbox — ブラウザ拡張向けの最小HTTP受け口。

セキュリティ方針:
  - 127.0.0.1 バインドのみ（LAN公開しない）
  - X-Tamo-Token 必須（~/.tamo/inbox.token、初回自動生成）
  - サーバは検証して ~/.tamo/inbox/ にJSONを書くだけ。
    パース/取込は inbox アダプタに一本化（攻撃面を最小化）。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import inbox_token
from .util import sha8, tamo_home

_MAX_BODY = 50 * 1024 * 1024  # 50MB（添付b64込み）


def make_server(port: int = 8787) -> ThreadingHTTPServer:
    token = inbox_token()
    inbox_dir = tamo_home() / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静かに
            pass

        def _deny(self, code: int, msg: str):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(msg.encode())

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            elif self.path.startswith("/recall"):
                # ブラウザ拡張の「文脈を取ってコピー」用。トークン必須(投函と同じ認可)
                if self.headers.get("X-Tamo-Token") != inbox_token():
                    return self._deny(403, "bad token")
                from urllib.parse import parse_qs, urlparse

                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get("q") or [""])[0]
                src = (qs.get("source") or [""])[0] or None
                if not q.strip():
                    return self._deny(400, "q required")
                from .recall import recall as _r  # 遅延import(循環回避)
                from .store import Store
                from .util import tamo_home

                store = Store(tamo_home())
                try:
                    md = _r(store, q, budget_tokens=3500, source=src)
                finally:
                    store.close()
                body = md.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/pair":
                # 拡張の自動ペアリング用。Hostヘッダを検査して127.0.0.1/localhost以外を拒否
                # （DNS rebindingで外部ページがlocalhostに化けて読む攻撃の定石対策。
                #   通常のWebページはCORSでもレスポンスを読めない）
                host = (self.headers.get("Host") or "").split(":")[0]
                if host not in ("127.0.0.1", "localhost", "[::1]"):
                    return self._deny(403, "forbidden host")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(inbox_token().encode())
            else:
                self._deny(404, "not found")

        def do_POST(self):
            if self.path != "/inbox":
                return self._deny(404, "not found")
            if self.headers.get("X-Tamo-Token") != token:
                return self._deny(403, "bad token")
            length = int(self.headers.get("Content-Length") or 0)
            if not (0 < length <= _MAX_BODY):
                return self._deny(413, "bad length")
            body = self.rfile.read(length)
            try:
                json.loads(body)  # JSONであることだけ検証
            except Exception:  # noqa: BLE001
                return self._deny(400, "not json")
            name = f"{int(time.time() * 1000)}_{sha8(body.decode('utf-8', 'replace'))}.json"
            Path(inbox_dir / name).write_bytes(body)
            self.send_response(204)
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def start_background(port: int = 8787) -> ThreadingHTTPServer:
    srv = make_server(port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv
