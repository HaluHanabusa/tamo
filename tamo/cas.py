"""tamo.cas — Content-Addressed Storage。

画像・動画・添付ファイルは sha256 で一意化して `cas/ab/cd/<sha>.<ext>` に保存。
同一バイナリは何度会話に現れても1回しか保存されない。
会話イベント側は {"type":"blob", sha256, mime, bytes, name} 参照に置換される。
（Claude Code の transcript は貼り付け画像を base64 で丸ごと抱えるため、
 この置換だけでログサイズが桁で縮む。）
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

from .util import now_iso, sha256_bytes

_MAGIC: list[tuple[bytes, str, str]] = [
    (b"\x89PNG", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF8", ".gif", "image/gif"),
    (b"%PDF", ".pdf", "application/pdf"),
]


_OFFICE = {
    "docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}

_GENERIC_HINTS = {"", "application/octet-stream", "application/zip", "binary/octet-stream"}


def sniff(data: bytes, mime_hint: str | None = None) -> tuple[str, str]:
    """(ext, mime) を返す。マジックナンバー優先（送信側のmime申告は信用しすぎない）。
    PKヘッダはZIP内部構造まで見て docx/xlsx/pptx を判別する。"""
    if data[:4] == b"PK\x03\x04":
        from .textract import office_kind

        k = office_kind(data)
        if k:
            return _OFFICE[k]
        return ".zip", "application/zip"
    for magic, ext, mime in _MAGIC:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if data[:4] == b"\x1aE\xdf\xa3":  # EBML (webm/mkv)
        return ".webm", "video/webm"
    if data[:4] == b"OggS":
        return ".ogg", "audio/ogg"
    if data[:4] == b"fLaC":
        return ".flac", "audio/flac"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3", "audio/mpeg"
    if len(data) > 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"qt  ", b"M4V "):
            return ".mov", "video/quicktime"
        if brand.startswith(b"M4A"):
            return ".m4a", "audio/mp4"
        return ".mp4", "video/mp4"
    hint = (mime_hint or "").split(";")[0].strip().lower()
    if hint and hint not in _GENERIC_HINTS:
        ext = mimetypes.guess_extension(hint) or ".bin"
        return ext, hint
    return ".bin", "application/octet-stream"


def image_dims(data: bytes, mime: str) -> tuple[int, int] | None:
    """Pillowがあれば寸法を取る（任意依存・決定論）。"""
    try:
        import io

        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


class CAS:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha: str, ext: str) -> Path:
        d = self.root / sha[:2] / sha[2:4]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{sha}{ext}"

    def put(self, data: bytes, mime: str | None = None, name: str | None = None) -> dict:
        sha = sha256_bytes(data)
        ext, mime2 = sniff(data, mime)
        p = self.path_for(sha, ext)
        if not p.exists():
            p.write_bytes(data)
        meta = {
            "sha256": sha,
            "mime": mime2,
            "bytes": len(data),
            "path": str(p),
            "name": name,
            "first_seen": now_iso(),
        }
        dims = image_dims(data, mime2) if mime2.startswith("image/") else None
        if dims:
            meta["width"], meta["height"] = dims
        return meta

    def get_path(self, sha: str) -> Path | None:
        d = self.root / sha[:2] / sha[2:4]
        if not d.exists():
            return None
        for p in d.glob(sha + ".*"):
            return p
        return None


# base64 data URI（本文中に埋まっているケース）: ある程度大きいものだけ対象
_DATAURI_RE = re.compile(r"data:([\w.+/-]+);base64,([A-Za-z0-9+/=\n\r]{256,})")


def extract_blobs(ev: dict, cas: CAS) -> tuple[dict, list[dict]]:
    """イベント内の中間表現 image_b64 / file_b64 / 本文中data-URI をCASへ吸い出し、
    blob参照に置換する。返り値は (置換後イベント, 追加blobメタ一覧)。決定論・LLM不使用。
    """
    blobs: list[dict] = []
    new_content: list[dict] = []
    for b in ev.get("content", []):
        t = b.get("type")
        if t in ("image_b64", "file_b64"):
            try:
                data = base64.b64decode(b.get("data", ""), validate=False)
            except Exception:  # noqa: BLE001
                new_content.append({"type": "text", "text": "[破損したbase64ブロック]"})
                continue
            meta = cas.put(data, b.get("media_type") or b.get("mime"), b.get("name"))
            blobs.append(meta)
            new_content.append(
                {"type": "blob", "sha256": meta["sha256"], "mime": meta["mime"], "bytes": meta["bytes"], "name": meta.get("name")}
            )
        elif t in ("text", "tool_result"):
            key = "text"
            txt = b.get(key, "")
            if "base64," in txt:
                def _repl(m: re.Match) -> str:
                    try:
                        data = base64.b64decode(m.group(2), validate=False)
                    except Exception:  # noqa: BLE001
                        return m.group(0)[:64] + "…"
                    meta = cas.put(data, m.group(1))
                    blobs.append(meta)
                    return f"[blob {meta['mime']} {meta['bytes']}B sha={meta['sha256'][:12]}]"

                txt = _DATAURI_RE.sub(_repl, txt)
            nb = dict(b)
            nb[key] = txt
            new_content.append(nb)
        else:
            new_content.append(b)
    ev2 = dict(ev)
    ev2["content"] = new_content
    return ev2, blobs
