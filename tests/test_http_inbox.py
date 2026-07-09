"""HTTP inbox: 認可(タイミングセーフ)・healthのトークン任意検査・エラー本文・投函。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tamo.config import inbox_token
from tamo.http_inbox import start_background


@pytest.fixture()
def server(tamo_home):
    srv = start_background(0)  # 空きポートに任せる
    port = srv.server_address[1]
    yield port, inbox_token()
    srv.shutdown()


def _req(port: int, path: str, method: str = "GET", token: str | None = None,
         body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data)
    if token is not None:
        req.add_header("X-Tamo-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_health_optional_token_check(server):
    port, tok = server
    assert _req(port, "/health")[0] == 200            # トークン無し=到達性のみ
    assert _req(port, "/health", token=tok)[0] == 200  # 正トークン
    code, body = _req(port, "/health", token="WRONG")  # 誤りは403（拡張の接続確認が偽OKを出さない）
    assert code == 403 and "再ペアリング" in body


def test_inbox_auth_and_error_body(server, tamo_home):
    port, tok = server
    code, body = _req(port, "/inbox", "POST", token="WRONG", body={})
    assert code == 403
    assert "tamo token" in body  # 対処ガイドを本文で返す（拡張がそのまま表示する）

    code, _ = _req(port, "/inbox", "POST", token=tok,
                   body={"schema": "tamo.inbox.v1", "source": "t", "session": "s",
                         "messages": [{"role": "user", "text": "hi"}]})
    assert code == 204
    assert list((tamo_home / "inbox").glob("*.json"))  # 検証して書くだけ（パースはアダプタ）


def test_pair_returns_token_for_localhost(server):
    port, tok = server
    code, body = _req(port, "/pair")
    assert code == 200 and body.strip() == tok


def test_recall_requires_token(server):
    port, tok = server
    assert _req(port, "/recall?q=x")[0] == 403
    assert _req(port, "/recall?q=x", token=tok)[0] == 200
