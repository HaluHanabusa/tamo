"""tamo.redact — 秘密情報の決定論的マスキング。

用途: `tamo mirror --redact` / `tamo export --redact` で、会話履歴を
リポジトリにコミットしたりチームに共有する前にAPIキー等を除去する。
（SpecStoryは履歴のsecretスキャンをエージェントスキルで提供している。
tamoは共有・コミットの手前で決定論に落とす方針）

方針:
  - 誤検知よりも取りこぼしを嫌う場面で使うため、既知プレフィックス型は積極的に、
    汎用パターンは「key=value風の行」に限定して保守的にマスクする
  - 置換は [REDACTED:種類] 形式。何を何件消したかを返し、呼び出し側が表示する
"""
from __future__ import annotations

import re

# (ラベル, パターン) — 既知プレフィックス型は文字列単体でマスク
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic/openai-key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    ("private-key-block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    # key=value / key: value 型（変数名に secret 系の語を含む行のみ）
    ("assignment", re.compile(
        r"(?im)^(?P<head>\s*(?:export\s+)?[\w.\-]*(?:api[_-]?key|apikey|token|secret|passwd|password)[\w.\-]*\s*[=:]\s*)"
        r"(?P<q>['\"]?)(?P<val>[^'\"\s]{8,})(?P=q)")),
]


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """(マスク済みテキスト, {種類: 件数}) を返す。決定論（同入力→同出力）。"""
    counts: dict[str, int] = {}
    if not text:
        return text, counts
    for label, pat in _PATTERNS:
        if label == "assignment":
            def _sub(m: re.Match) -> str:
                counts[label] = counts.get(label, 0) + 1
                return f"{m.group('head')}[REDACTED:{label}]"
            text, _ = pat.subn(_sub, text)
        else:
            text, n = pat.subn(f"[REDACTED:{label}]", text)
            if n:
                counts[label] = counts.get(label, 0) + n
    return text, counts


def merge_counts(total: dict[str, int], add: dict[str, int]) -> None:
    for k, v in add.items():
        total[k] = total.get(k, 0) + v
