"""OpenAI Codex CLI アダプタ。

ソース: ~/.codex/sessions/**/*.jsonl（rollout形式・追記型）
行形式はバージョン差が大きいため寛容にパースする:
  - {"type":"message","role":...,"content":[{"type":"input_text|output_text|text","text":...}]}
  - {"timestamp":..., "payload": {...上記...}} のエンベロープ
  - {"type":"function_call", "name", "arguments"} / {"type":"function_call_output", "output"}
"""
from __future__ import annotations

from pathlib import Path

from ..schema import make_event
from ..util import jloads
from . import Adapter, item, register


def _texts(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            out.append(c["text"])
    return "\n".join(out)


def _parse(obj: dict, session_key: str, seq: int, locator: str) -> list[dict]:
    ts = obj.get("timestamp")
    if isinstance(obj.get("payload"), dict):
        inner = obj["payload"]
    else:
        inner = obj
    t = inner.get("type")
    role = inner.get("role")
    if t == "message" or role in ("user", "assistant", "system"):
        actor = "user" if role == "user" else ("system" if role == "system" else "assistant")
        text = _texts(inner.get("content"))
        if not text.strip():
            return []
        return [make_event(source_kind="codex_cli", session_key=session_key, seq=seq, actor=actor,
                           kind="message", content=[{"type": "text", "text": text}], locator=locator, ts=ts)]
    if t == "function_call":
        return [make_event(source_kind="codex_cli", session_key=session_key, seq=seq, actor="assistant",
                           kind="tool_use",
                           content=[{"type": "tool_use", "id": inner.get("call_id"), "name": inner.get("name"),
                                     "input": {"arguments": inner.get("arguments")}}],
                           locator=locator, ts=ts)]
    if t == "function_call_output":
        return [make_event(source_kind="codex_cli", session_key=session_key, seq=seq, actor="tool",
                           kind="tool_result",
                           content=[{"type": "tool_result", "tool_use_id": inner.get("call_id"),
                                     "text": str(inner.get("output", ""))[:20000]}],
                           locator=locator, ts=ts)]
    return []


@register
class CodexAdapter(Adapter):
    kind = "codex_cli"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        root = Path(self.cfg.get("root", Path.home() / ".codex" / "sessions")).expanduser()
        files_cur: dict = dict(cursor.get("files", {}))
        items: list[dict] = []
        if root.exists():
            for p in sorted(root.rglob("*.jsonl")):
                fc = files_cur.get(str(p), {})
                off, seq = int(fc.get("off", 0)), int(fc.get("seq", 0))
                size = p.stat().st_size
                if size < off:
                    off, seq = 0, 0
                if size == off:
                    continue
                session_key = f"codex:{p.stem}"
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
                                items.append(item(locator, line, _parse(obj, session_key, seq, locator)))
                        off = f.tell()
                        seq += 1
                files_cur[str(p)] = {"off": off, "seq": seq}
        return {"files": files_cur}, items
