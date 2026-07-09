"""汎用 JSONL アダプタ — 専用アダプタが無いエージェント向けの受け皿。

sources.toml 例:
  [[source]]
  kind = "generic_jsonl"
  key  = "goose:default"
  glob = "~/.local/share/goose/sessions/*.jsonl"
  role_field = "role"          # 省略時 role / type を順に試す
  text_field = "content"       # 省略時 content / text / message.content を順に試す
  ts_field   = "created"       # 省略可
"""
from __future__ import annotations

import glob as globmod
from pathlib import Path

from ..schema import text_event
from ..util import jloads
from . import Adapter, item, register

_ROLE_NORM = {"user": "user", "human": "user", "assistant": "assistant", "ai": "assistant",
              "model": "assistant", "system": "system", "tool": "tool"}


def _dig(obj: dict, dotted: str):
    cur = obj
    for k in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


@register
class GenericJsonlAdapter(Adapter):
    kind = "generic_jsonl"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        pattern = str(Path(self.cfg["glob"]).expanduser())
        role_fields = [self.cfg.get("role_field")] if self.cfg.get("role_field") else ["role", "type"]
        text_fields = [self.cfg.get("text_field")] if self.cfg.get("text_field") else ["content", "text", "message.content"]
        ts_field = self.cfg.get("ts_field")
        files_cur: dict = dict(cursor.get("files", {}))
        items: list[dict] = []
        for ps in sorted(globmod.glob(pattern, recursive=True)):
            p = Path(ps)
            fc = files_cur.get(str(p), {})
            off, seq = int(fc.get("off", 0)), int(fc.get("seq", 0))
            size = p.stat().st_size
            if size < off:
                off, seq = 0, 0
            if size == off:
                continue
            skey = f"{self.key}:{p.stem}"
            with p.open("rb") as f:
                f.seek(off)
                for line in f:
                    if not line.endswith(b"\n") and f.tell() >= size:
                        break
                    s = line.strip()
                    locator = f"{p}::{seq}"
                    if s:
                        obj, err = jloads(s)
                        if err or not isinstance(obj, dict):
                            items.append(item(locator, line, [], error=err or "not object"))
                        else:
                            role = next((obj.get(rf) for rf in role_fields if obj.get(rf) is not None), None)
                            actor = _ROLE_NORM.get(str(role).lower(), None)
                            text = next((v for tf in text_fields if isinstance(v := _dig(obj, tf), str) and v.strip()), None)
                            evs = []
                            if actor and text:
                                ts = obj.get(ts_field) if ts_field else None
                                evs = [text_event("generic_jsonl", skey, seq, actor, text, locator, ts=str(ts) if ts else None)]
                            items.append(item(locator, line, evs))
                    off = f.tell()
                    seq += 1
            files_cur[str(p)] = {"off": off, "seq": seq}
        return {"files": files_cur}, items
