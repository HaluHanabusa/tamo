"""tamo.schema — 正準イベントスキーマ (CES: Canonical Event Schema) v1。

すべてのアダプタはソース固有形式をこの1形式に正規化する。
raw（原文）は store.raw_records に必ず保存されるため、CESは「検索・引き継ぎに
都合のよいビュー」であり、失われた情報はraw層から常に復元できる。

Event(dict):
  ces: 1
  event_id: str          # 決定論的ID（再収集しても同一）
  session_key: str       # 例 "claude_code:<uuid>", "cursor:<composerId>"
  source_kind: str
  seq: int               # ソース内の出現順
  ts: str|None           # ISO8601
  actor: "user"|"assistant"|"tool"|"system"
  kind: "message"|"tool_use"|"tool_result"|"meta"
  content: [block...]
  hints: {cwd?, model?, file_path?, tool_name?, title?, ...}
  native_id: str|None
  locator: str           # raw上の位置（path::line 等）

block:
  {"type":"text","text":...}
  {"type":"tool_use","id":...,"name":...,"input":{...}}
  {"type":"tool_result","tool_use_id":...,"text":...}
  {"type":"blob","sha256":...,"mime":...,"bytes":...,"name":...}   # CAS参照
  {"type":"image_b64","media_type":...,"data":...}                 # 中間表現(取込時にblobへ変換)
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .util import truncate

CES_VERSION = 1

ACTORS = {"user", "assistant", "tool", "system"}
KINDS = {"message", "tool_use", "tool_result", "meta"}


def blocks_text(content: list[dict], limit_per_block: int = 20000) -> str:
    """検索・pack用の平文表現。"""
    parts: list[str] = []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text", ""))
        elif t == "tool_use":
            parts.append(f"[tool_use {b.get('name')}] " + truncate(json.dumps(b.get("input", {}), ensure_ascii=False), 400))
        elif t == "tool_result":
            parts.append(truncate(b.get("text", ""), limit_per_block))
        elif t == "blob":
            from .util import human_size, media_kind_ja

            kind = media_kind_ja(b.get("mime"), b.get("name"))
            parts.append(
                f"[添付({kind}) {b.get('name') or ''} {b.get('mime')} "
                f"{human_size(b.get('bytes'))} sha={str(b.get('sha256'))[:12]}]"
            )
        elif t == "image_b64":
            parts.append(f"[image {b.get('media_type')} (未変換)]")
    return "\n".join(p for p in parts if p)


def _content_fingerprint(content: list[dict]) -> str:
    h = hashlib.sha256()
    for b in content or []:
        t = b.get("type", "?")
        h.update(t.encode())
        if t == "text":
            h.update(b.get("text", "").encode("utf-8", "replace"))
        elif t == "tool_use":
            h.update(json.dumps(b.get("input", {}), sort_keys=True, ensure_ascii=False).encode())
            h.update(str(b.get("name")).encode())
        elif t == "tool_result":
            h.update(b.get("text", "").encode("utf-8", "replace"))
        elif t == "blob":
            h.update(str(b.get("sha256")).encode())
        elif t == "image_b64":
            h.update(str(b.get("data", ""))[:64].encode())
    return h.hexdigest()


def make_event(
    *,
    source_kind: str,
    session_key: str,
    seq: int,
    actor: str,
    kind: str,
    content: list[dict],
    locator: str,
    ts: str | None = None,
    native_id: str | None = None,
    hints: dict[str, Any] | None = None,
) -> dict:
    assert actor in ACTORS, actor
    assert kind in KINDS, kind
    fp = _content_fingerprint(content)
    eid = hashlib.sha256(
        "|".join([source_kind, session_key, str(native_id or locator), kind, fp]).encode()
    ).hexdigest()[:32]
    return {
        "ces": CES_VERSION,
        "event_id": eid,
        "session_key": session_key,
        "source_kind": source_kind,
        "seq": seq,
        "ts": ts,
        "actor": actor,
        "kind": kind,
        "content": content,
        "hints": hints or {},
        "native_id": native_id,
        "locator": locator,
    }


def text_event(source_kind, session_key, seq, actor, text, locator, **kw) -> dict:
    return make_event(
        source_kind=source_kind,
        session_key=session_key,
        seq=seq,
        actor=actor,
        kind="message",
        content=[{"type": "text", "text": text}],
        locator=locator,
        **kw,
    )
