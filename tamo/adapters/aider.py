"""aider アダプタ。

ソース: 各リポジトリ直下の .aider.chat.history.md（追記型Markdown）
  "# aider chat started at ..." がセッション境界、"#### " 行がユーザー発話。
"""
from __future__ import annotations

from pathlib import Path

from ..schema import text_event
from ..util import sha8
from . import Adapter, item, register


@register
class AiderAdapter(Adapter):
    kind = "aider"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        paths = self.cfg.get("paths") or []
        files_cur: dict = dict(cursor.get("files", {}))
        items: list[dict] = []
        for ps in paths:
            p = Path(ps).expanduser()
            if not p.exists():
                continue
            fc = files_cur.get(str(p), {})
            off, seq = int(fc.get("off", 0)), int(fc.get("seq", 0))
            size = p.stat().st_size
            if size < off:
                off, seq = 0, 0
            if size == off:
                continue
            base_key = f"aider:{sha8(str(p))}"
            session_no = int(fc.get("session_no", 0))
            with p.open("rb") as f:
                f.seek(off)
                chunk = f.read().decode("utf-8", "replace")
                off = f.tell()

            user_buf: list[str] = []
            asst_buf: list[str] = []

            def flush(buf: list[str], actor: str):
                nonlocal seq
                text = "\n".join(buf).strip()
                buf.clear()
                if not text:
                    return
                locator = f"{p}::{seq}"
                ev = text_event("aider", f"{base_key}#{session_no}", seq, actor, text, locator)
                items.append(item(locator, text, [ev]))
                seq += 1

            for line in chunk.splitlines():
                if line.startswith("# aider chat started"):
                    flush(asst_buf, "assistant")
                    session_no += 1
                elif line.startswith("#### "):
                    flush(asst_buf, "assistant")
                    user_buf.append(line[5:])
                else:
                    flush(user_buf, "user")
                    asst_buf.append(line)
            flush(user_buf, "user")
            flush(asst_buf, "assistant")
            files_cur[str(p)] = {"off": off, "seq": seq, "session_no": session_no}
        return {"files": files_cur}, items
