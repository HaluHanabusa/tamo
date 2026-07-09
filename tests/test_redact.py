"""redact: 既知プレフィックス型の検出・部分マスク・決定論・誤検知なし。

注意: 見本は全て形式だけ模した架空値だが、GitHubのpush protectionや
secretスキャナが「本物」と誤検知してpushを弾くため、ファイル上は
必ず文字列連結で分割して書くこと（実行時に結合されて照合は成立する）。
"""
from __future__ import annotations

import pytest

from tamo.redact import redact_text

SECRET_SAMPLES = [
    ("anthropic/openai", "sk-ant-" + "api03-AAAABBBBccccDDDD1234"),
    ("stripe", "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"),
    ("stripe-webhook", "whsec_" + "abcdef1234567890ABCDEF"),
    ("aws", "AKIA" + "IOSFODNN7EXAMPLE"),
    ("github-classic", "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"),
    ("github-fine-grained", "github_pat_" + "11ABCDEFG0_abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJ"),
    ("gitlab", "glpat-" + "XyZ123abcDEF456ghi7"),
    ("npm", "npm_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"),
    ("pypi", "pypi-" + "AgEIcHlwaS5vcmcCJGFiY2RlZi0xMjM0LTU2NzgtOWFiYy1kZWYwMTIzNDU2Nzg"),
    ("huggingface", "hf_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"),
    ("sendgrid", "SG." + "abcdefghijklmnop1234" + "." + "qrstuvwxyzABCDEF5678_ghij"),
    ("slack-token", "xoxb-" + "1234567890-abcdefghij"),
    ("slack-webhook", "https://hooks.slack.com/" + "services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"),
    ("discord-webhook", "https://discord.com/api/webhooks/" + "123456789012345678/aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"),
    ("google-api", "AIza" + "SyA1234567890abcdefghijklmnopqrstuv"),
    ("google-oauth-refresh", "1//" + "0gABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".SflKxwRJSMeKKF2QT4fwpM"),
    ("bearer", "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz012345"),
    ("basic", "Authorization: Basic " + "dXNlcjpwYXNzd29yZDEyMw=="),
    ("pem", "-----BEGIN RSA PRIVATE KEY-----" + "\nMIIEow\n" + "-----END RSA PRIVATE KEY-----"),
    ("assignment", "OPENAI_API_KEY = " + "'abcd1234efgh5678'"),
]


@pytest.mark.parametrize("name,sample", SECRET_SAMPLES, ids=[n for n, _ in SECRET_SAMPLES])
def test_secret_is_masked(name, sample):
    masked, counts = redact_text(sample)
    assert "[REDACTED:" in masked, f"{name}: {masked!r}"
    assert counts
    assert redact_text(sample)[0] == masked  # 決定論


def test_url_credential_masks_password_only():
    m, c = redact_text("DATABASE_URL is postgres://admin:" + "S3cretPw@db.internal:5432/app")
    assert "S3cretPw" not in m
    assert "admin" in m and "db.internal" in m  # ホスト・ユーザーは文脈として残す
    assert c.get("url-credential") == 1


BENIGN = [
    "この関数は sk-learn ではなく scikit-learn を使う",
    "料金は $19.99 です。https://example.com/path を参照",
    "SELECT * FROM users WHERE id = 12345678",
    "commit 6aa0d63f の変更を確認",
    "テスト対象: http://127.0.0.1:8787/inbox に投函",
    "進捗は 1//2 くらい",
    "Basic な使い方は README を参照",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_untouched(text):
    masked, counts = redact_text(text)
    assert masked == text and not counts
