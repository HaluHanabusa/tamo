"""Claude Code アダプタ。

ソース: ~/.claude/projects/<プロジェクトパスをサニタイズしたdir>/<sessionId>.jsonl
  - 1行=1レコードの追記型JSONL。バイトオフセットのカーソルで増分読取できる。
  - 行の type: "user" / "assistant" / "summary" / "system" など（将来増えても寛容に扱う）
  - user行の content には tool_result（ツール実行結果）や貼り付け画像
    {"type":"image","source":{"type":"base64","media_type":...,"data":...}} が入る。
  - フック(Stop/SessionEnd等)の stdin JSON には transcript_path が入るため、
    push型の即時取込は collect_file() を直接呼べばよい。

備考: transcript の細部スキーマは公表仕様ではなくバージョンで変わり得るため、
既知フィールドのみ拾い、未知はrawに残す方針（ドリフト耐性）。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..schema import make_event
from ..util import jloads
from . import Adapter, item, register

# tool_use の input からファイルパスを拾う対象（スナップショット折り畳み最適化のヒント）
_FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookRead", "NotebookEdit", "create_file", "str_replace", "view"}


def _content_blocks(raw_content, tool_index: dict) -> tuple[list[dict], str]:
    """message.content を CES blocks へ。戻り値 (blocks, primary_kind)。"""
    blocks: list[dict] = []
    kind = "message"
    if isinstance(raw_content, str):
        return [{"type": "text", "text": raw_content}], kind
    for it in raw_content or []:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t == "text":
            blocks.append({"type": "text", "text": it.get("text", "")})
        elif t == "thinking":
            continue  # 思考ログは引き継ぎ対象外（rawには残る）
        elif t == "tool_use":
            name = it.get("name")
            inp = it.get("input", {}) or {}
            tid = it.get("id")
            if tid:
                fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
                tool_index[tid] = {"name": name, "file_path": fp if name in _FILE_TOOLS else None}
            blocks.append({"type": "tool_use", "id": tid, "name": name, "input": inp})
            kind = "tool_use"
        elif t == "tool_result":
            tid = it.get("tool_use_id")
            inner = it.get("content")
            texts: list[str] = []
            if isinstance(inner, str):
                texts.append(inner)
            else:
                for c in inner or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(c.get("text", ""))
                    elif isinstance(c, dict) and c.get("type") == "image":
                        src = c.get("source", {})
                        if src.get("type") == "base64":
                            blocks.append({"type": "image_b64", "media_type": src.get("media_type"), "data": src.get("data", "")})
            blocks.append({"type": "tool_result", "tool_use_id": tid, "text": "\n".join(texts)})
            kind = "tool_result"
        elif t == "image":
            src = it.get("source", {})
            if src.get("type") == "base64":
                blocks.append({"type": "image_b64", "media_type": src.get("media_type"), "data": src.get("data", "")})
    return blocks, kind


def parse_line(line: bytes, *, session_fallback: str, seq: int, locator: str, tool_index: dict) -> list[dict]:
    obj, err = jloads(line)
    if err or not isinstance(obj, dict):
        raise ValueError(err or "not a JSON object")
    t = obj.get("type")
    ts = obj.get("timestamp")
    sess = obj.get("sessionId") or session_fallback
    session_key = f"claude_code:{sess}"
    hints = {k: v for k, v in {
        "cwd": obj.get("cwd"), "git_branch": obj.get("gitBranch"), "cc_version": obj.get("version"),
    }.items() if v}

    if t in ("user", "assistant"):
        msg = obj.get("message") or {}
        if msg.get("model"):
            hints["model"] = msg["model"]
        blocks, kind = _content_blocks(msg.get("content"), tool_index)
        if not blocks:
            return []
        actor = "assistant" if t == "assistant" else ("tool" if kind == "tool_result" else "user")
        # tool_result にファイルパスのヒントを付与（tool_use との対応付け）
        for b in blocks:
            if b.get("type") == "tool_result" and b.get("tool_use_id") in tool_index:
                ti = tool_index[b["tool_use_id"]]
                hints.setdefault("tool_name", ti.get("name"))
                if ti.get("file_path"):
                    hints["file_path"] = ti["file_path"]
        return [make_event(
            source_kind="claude_code", session_key=session_key, seq=seq, actor=actor, kind=kind,
            content=blocks, locator=locator, ts=ts, native_id=obj.get("uuid"), hints=hints,
        )]
    if t == "summary":
        return [make_event(
            source_kind="claude_code", session_key=session_key, seq=seq, actor="system", kind="meta",
            content=[{"type": "text", "text": f"[summary] {obj.get('summary', '')}"}],
            locator=locator, ts=ts, native_id=obj.get("leafUuid"), hints=hints,
        )]
    # system / その他: メタとして短く保持（rawは全文残る）
    brief = json.dumps({k: obj.get(k) for k in ("type", "subtype", "content") if k in obj}, ensure_ascii=False)[:300]
    return [make_event(
        source_kind="claude_code", session_key=session_key, seq=seq, actor="system", kind="meta",
        content=[{"type": "text", "text": brief}], locator=locator, ts=ts, native_id=obj.get("uuid"), hints=hints,
    )]


def collect_file(path: Path, file_cursor: dict) -> tuple[dict, list[dict]]:
    """1つのtranscriptファイルを増分読取。file_cursor={"off":int,"seq":int}"""
    off = int(file_cursor.get("off", 0))
    seq = int(file_cursor.get("seq", 0))
    size = path.stat().st_size
    if size < off:  # 置換/ローテーション → 先頭から（event_idの冪等性で重複はしない）
        off, seq = 0, 0
    items: list[dict] = []
    tool_index: dict = {}
    session_fallback = path.stem
    with path.open("rb") as f:
        f.seek(off)
        for line in f:
            if not line.endswith(b"\n") and f.tell() >= size:
                # 書き込み途中の最終行は次回に回す
                break
            locator = f"{path}::{seq}"
            stripped = line.strip()
            if stripped:
                try:
                    events = parse_line(stripped, session_fallback=session_fallback, seq=seq, locator=locator, tool_index=tool_index)
                    items.append(item(locator, line, events))
                except Exception as e:  # noqa: BLE001
                    items.append(item(locator, line, [], error=str(e)))
            off = f.tell()
            seq += 1
    return {"off": off, "seq": seq}, items


@register
class ClaudeCodeAdapter(Adapter):
    kind = "claude_code"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        root = Path(self.cfg.get("root", Path.home() / ".claude" / "projects")).expanduser()
        files_cur: dict = dict(cursor.get("files", {}))
        all_items: list[dict] = []
        if root.exists():
            for p in sorted(root.rglob("*.jsonl")):
                fc = files_cur.get(str(p), {})
                if p.stat().st_size == fc.get("off") and fc:
                    continue  # 変化なし
                new_fc, items = collect_file(p, fc)
                files_cur[str(p)] = new_fc
                all_items.extend(items)
        return {"files": files_cur}, all_items
