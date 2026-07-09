"""Cursor IDE アダプタ。

ソース: <UserData>/User/globalStorage/state.vscdb (SQLite)
  Windows:  %APPDATA%\\Cursor\\User\\globalStorage\\state.vscdb
            (WSL2からは /mnt/c/Users/<user>/AppData/Roaming/Cursor/... で見える)
  Linux:    ~/.config/Cursor/User/globalStorage/state.vscdb

  table cursorDiskKV(key, value):
    composerData:<composerId>       … 会話(composer)のメタ/旧形式では全文
    bubbleId:<composerId>:<bubbleId>… 1メッセージ（新しめの形式）

注意（実運用で最重要）:
  - CursorはDBを開きっぱなしにする → 必ずスナップショットコピーしてから読む。
  - bubbleのJSON構造はバージョンで変わる（text/richText、type:1|2 等）。
    ここは「知っているキーだけ拾う寛容パーサ + 失敗はquarantine」で受ける。
  - rowidカーソルで増分読取するが、既存行のin-place更新は検知できないため、
    `tamo collect --rescan cursor_ide` で全再走査できる（event_idの冪等性で安全）。
"""
from __future__ import annotations

import sqlite3

from pathlib import Path

from ..schema import make_event
from ..util import jloads
from . import Adapter, item, register, snapshot_sqlite

_ROLE_MAP = {1: "user", 2: "assistant", "1": "user", "2": "assistant",
             "user": "user", "human": "user", "ai": "assistant", "assistant": "assistant", "bot": "assistant"}


def _bubble_text(j: dict) -> str:
    if isinstance(j.get("text"), str) and j["text"].strip():
        return j["text"]
    rt = j.get("richText")
    if isinstance(rt, str) and rt.strip():
        return rt
    parts: list[str] = []
    for c in j.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            parts.append(c["text"])
    return "\n".join(parts)


@register
class CursorAdapter(Adapter):
    kind = "cursor_ide"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        db = Path(self.cfg["db"]).expanduser()
        last_rowid = int(cursor.get("rowid", 0))
        if not db.exists():
            return cursor, []
        items: list[dict] = []
        max_rowid = last_rowid
        with snapshot_sqlite(db) as snap:
            con = sqlite3.connect(snap)
            con.text_factory = bytes
            try:
                tables = {r[0].decode() if isinstance(r[0], bytes) else r[0]
                          for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "cursorDiskKV" not in tables:
                    return cursor, [item(f"{db}::no-cursorDiskKV", b"", [], error=f"tables={sorted(tables)}")]
                rows = con.execute(
                    "SELECT rowid, key, value FROM cursorDiskKV WHERE rowid > ? ORDER BY rowid", (last_rowid,)
                )
                for rowid, key_b, val_b in rows:
                    max_rowid = max(max_rowid, rowid)
                    key = key_b.decode("utf-8", "replace") if isinstance(key_b, bytes) else str(key_b)
                    if val_b is None:
                        continue  # Cursorは削除済み/未確定のcomposerでNULL値を残す（実機で確認）。データが無いので保全対象も無い
                    raw = val_b if isinstance(val_b, bytes) else str(val_b).encode()
                    if not raw.strip():
                        continue
                    locator = f"{db}::rowid={rowid}::{key}"
                    try:
                        events = self._parse_kv(key, raw, rowid, locator)
                        items.append(item(locator, raw, events))
                    except Exception as e:  # noqa: BLE001
                        items.append(item(locator, raw, [], error=f"{key}: {e}"))
            finally:
                con.close()
        return {"rowid": max_rowid}, items

    def _parse_kv(self, key: str, raw: bytes, rowid: int, locator: str) -> list[dict]:
        if key.startswith("composerData:"):
            cid = key.split(":", 1)[1]
            j, err = jloads(raw)
            if err:
                raise ValueError(err)
            title = j.get("name") or j.get("title") or j.get("composerTitle")
            events = [make_event(
                source_kind="cursor_ide", session_key=f"cursor:{cid}", seq=0, actor="system", kind="meta",
                content=[{"type": "text", "text": f"[composer] {title or cid}"}],
                locator=locator, native_id=key, hints={"title": title} if title else {},
            )]
            # 旧形式: conversation配列に全文が入っているケース
            for i, m in enumerate(j.get("conversation") or [], start=1):
                if not isinstance(m, dict):
                    continue
                actor = _ROLE_MAP.get(m.get("type"), _ROLE_MAP.get(m.get("role"), "assistant"))
                text = _bubble_text(m)
                if not text.strip():
                    continue
                events.append(make_event(
                    source_kind="cursor_ide", session_key=f"cursor:{cid}", seq=i, actor=actor, kind="message",
                    content=[{"type": "text", "text": text}], locator=f"{locator}#conv{i}",
                    native_id=f"{key}#conv{i}",
                ))
            return events
        if key.startswith("bubbleId:"):
            parts = key.split(":", 2)
            cid = parts[1] if len(parts) >= 2 else "unknown"
            j, err = jloads(raw)
            if err:
                raise ValueError(err)
            actor = _ROLE_MAP.get(j.get("type"), _ROLE_MAP.get(j.get("role"), "assistant"))
            text = _bubble_text(j)
            if not text.strip():
                return []
            hints = {}
            if isinstance(j.get("createdAt"), str):
                hints["created_at"] = j["createdAt"]
            return [make_event(
                source_kind="cursor_ide", session_key=f"cursor:{cid}", seq=rowid, actor=actor, kind="message",
                content=[{"type": "text", "text": text}], locator=locator, native_id=key, hints=hints,
            )]
        return []  # 会話以外のキーは対象外（rawには残す価値も薄いので保存しない→items自体は返る）
