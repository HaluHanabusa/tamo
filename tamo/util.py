"""tamo.util — 依存ゼロの共通ユーティリティ。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def tamo_home() -> Path:
    p = Path(os.environ.get("TAMO_HOME", str(Path.home() / ".tamo")))
    p.mkdir(parents=True, exist_ok=True)
    try:  # 平文の会話・コード・添付を含むため所有者のみに制限（NFR）
        p.chmod(0o700)
    except OSError:
        pass
    return p


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8", "replace"))


def sha8(s: str) -> str:
    return sha256_text(s)[:8]


def jloads(s: str | bytes):
    """寛容なJSONロード。(obj, error) を返す。"""
    try:
        return json.loads(s), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def truncate(s: str, n: int, marker: str = " …[truncated]") -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - len(marker))] + marker


# ---------------------------------------------------------------- noise strip
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S)
_BLANKS_RE = re.compile(r"\n{3,}")


def strip_noise(text: str) -> str:
    """決定論的なノイズ除去（可逆性はraw層で担保するのでここは実利優先）。
    - ANSIエスケープ
    - <system-reminder>ブロック
    - \r で上書きされるプログレス表示（最後のセグメントのみ残す）
    - 3連以上の空行
    """
    if not text:
        return text
    text = _ANSI_RE.sub("", text)
    text = _SYSREM_RE.sub("", text)
    if "\r" in text:
        lines = []
        for ln in text.split("\n"):
            if "\r" in ln:
                ln = ln.split("\r")[-1]
            lines.append(ln)
        text = "\n".join(lines)
    text = _BLANKS_RE.sub("\n\n", text)
    return text


# ------------------------------------------------------------ token estimate
_ASCII_TOK = re.compile(r"[A-Za-z0-9_]+")
_NONWORD = re.compile(r"[^\sA-Za-z0-9_]")


def estimate_tokens(s: str) -> int:
    """雑だが方向は合うトークン概算: ASCII語は1語=1、CJK等の非語文字は1文字=1。"""
    if not s:
        return 0
    return len(_ASCII_TOK.findall(s)) + len(_NONWORD.findall(s))


# ------------------------------------------------------------------ tokenize
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]+")


def tokenize(s: str) -> list[str]:
    """TF-IDF用の簡易トークナイザ: ASCII単語 + CJK文字バイグラム。"""
    toks = [t.lower() for t in _ASCII_TOK.findall(s)]
    for run in _CJK_RUN.findall(s):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i : i + 2] for i in range(len(run) - 1))
    return toks


def read_text_guess(b: bytes) -> str:
    for enc in ("utf-8", "utf-16", "cp932"):
        try:
            return b.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return b.decode("utf-8", "replace")


def media_kind_ja(mime: str | None, name: str | None = None) -> str:
    """mime(+拡張子)から日本語の種別語を返す。「添付が読めなくても何をしたかは伝わる」ための語彙。"""
    m = (mime or "").lower()
    n = (name or "").lower()
    if m.startswith("image/"):
        return "画像"
    if m.startswith("video/") or n.endswith((".mp4", ".mov", ".webm", ".avi", ".mkv")):
        return "動画"
    if m.startswith("audio/") or n.endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a")):
        return "音声"
    if m == "application/pdf" or n.endswith(".pdf"):
        return "PDF"
    if "wordprocessingml" in m or n.endswith((".docx", ".doc")):
        return "Word文書"
    if "spreadsheetml" in m or n.endswith((".xlsx", ".xls", ".csv")):
        return "表計算"
    if "presentationml" in m or n.endswith((".pptx", ".ppt")):
        return "スライド"
    if m.startswith("text/") or m.endswith(("json", "xml")) or n.endswith((".md", ".txt", ".log")):
        return "テキスト"
    if m in ("application/zip", "application/x-7z-compressed", "application/gzip") or n.endswith((".zip", ".7z", ".tar.gz")):
        return "圧縮"
    return "ファイル"


def human_size(nbytes) -> str:
    if not nbytes:
        return "?B"
    x = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f}{unit}" if unit == "B" else f"{x:.1f}{unit}"
        x /= 1024
    return f"{x:.1f}GB"
