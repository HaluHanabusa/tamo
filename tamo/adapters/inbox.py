"""inbox アダプタ — ローカルにファイルを残さないサービス（ChatGPT/Claude.ai/Gemini等の
Web UI）向けの共通受け口。

取り込み形式（拡張側が生成するJSON、1ファイル=1会話 or 差分）:
{
  "schema": "tamo.inbox.v1",
  "source": "chatgpt_web",            # 任意のソース名
  "session": "conv-uuid-or-hash",
  "title": "任意",
  "messages": [
    {"role": "user"|"assistant", "text": "...", "ts": "ISO8601",
     "attachments": [{"name":"a.png","mime":"image/png","data_b64":"..."}]}
  ]
}

投入経路は2つ:
  1) ~/.tamo/inbox/*.json に置く（`tamo watch` が拾い、処理後 done/ へ移動）
  2) `tamo watch --http` のHTTP口（127.0.0.1限定・トークン必須）へPOST
     → サーバは検証してinboxへ書くだけ。取り込みロジックはこのアダプタに一本化。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..schema import make_event
from ..util import human_size, jloads, media_kind_ja, sha256_bytes, tamo_home
from . import Adapter, item, register


@register
class InboxAdapter(Adapter):
    kind = "inbox"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:
        root = Path(self.cfg.get("dir", tamo_home() / "inbox")).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        done = root / "done"
        done.mkdir(exist_ok=True)
        items: list[dict] = []
        self._pending_moves: list[tuple[Path, Path]] = []
        for p in sorted(root.glob("*.json")):
            raw = p.read_bytes()
            locator = f"inbox::{p.name}"
            obj, err = jloads(raw)
            if err or not isinstance(obj, dict):
                items.append(item(locator, raw, [], error=err or "not object"))
            else:
                try:
                    items.append(item(locator, raw, self._parse(obj, locator)))
                except Exception as e:  # noqa: BLE001
                    items.append(item(locator, raw, [], error=str(e)))
            # done/への移動はcommit後のfinalize()で行う。ここで動かすとcommit前の
            # クラッシュでイベント未保存のままファイルが再読対象外になり、黙って失われる
            self._pending_moves.append((p, done / p.name))
        return cursor, items

    def finalize(self) -> None:
        import os

        for src, dst in getattr(self, "_pending_moves", []):
            try:
                os.replace(src, dst)  # 既存の同名done/も上書き（Windowsのrename衝突でwatchを殺さない）
            except OSError:
                pass  # 消せない場合は残す — locator一意+event_id冪等なので再取込されても安全
        self._pending_moves = []

    def _parse(self, obj: dict, locator: str) -> list[dict]:
        # source_kind は「輸送経路(inbox)」でなく「発生面(claude_web/gemini_web/…)」を持つ。
        # 面での絞り込み(search/recall/list_sessionsのsource)と per_source 集計の正がここ。
        src = str(obj.get("source", "web")).strip().lower() or "web"
        sess = str(obj.get("session", "unknown"))
        skey = f"{src}:{sess}"
        title = obj.get("title")
        cap_ts = obj.get("captured_at")  # ブラウザ拡張が付ける取得時刻。ts無しメッセージの時間軸フォールバック
        events: list[dict] = []
        # 拡張が残した注記（アダプタfallback理由・上限切詰め等）はmetaイベントとして保全する
        # — 「情報を黙って落とさない」原則。同文はnative_idの内容ハッシュで冪等
        note = obj.get("note")
        if isinstance(note, str) and note.strip():
            ntxt = note.strip()[:2000]
            events.append(make_event(
                source_kind=src, session_key=skey, seq=-1, actor="system", kind="meta",
                content=[{"type": "text", "text": f"[scoop note] {ntxt}"}],
                locator=f"{locator}#note", ts=cap_ts,
                native_id=f"note:{sha256_bytes(ntxt.encode())[:16]}",
            ))
        seen_h: dict[str, int] = {}  # 内容ハッシュの出現カウンタ（同文再発言の識別用）
        for i, m in enumerate(obj.get("messages") or []):
            role = "user" if str(m.get("role")).lower() in ("user", "human") else "assistant"
            # 位置(i)でなく内容ハッシュ+出現順をIDにする:
            # ブラウザ再掬いでウィンドウがズレても同じ発言は同じevent_idになり、差分だけが増える
            mh = sha256_bytes(json.dumps(m, sort_keys=True, ensure_ascii=False).encode())[:16]
            occ = seen_h.get(mh, 0)
            seen_h[mh] = occ + 1
            nid = f"m:{mh}:{occ}"
            content: list[dict] = []
            if isinstance(m.get("text"), str) and m["text"].strip():
                content.append({"type": "text", "text": m["text"]})
            for a in m.get("attachments") or []:
                if a.get("data_b64"):
                    content.append({"type": "file_b64", "mime": a.get("mime"), "name": a.get("name"), "data": a["data_b64"]})
                elif a.get("url"):
                    kind = media_kind_ja(a.get("mime"), a.get("name"))
                    content.append({"type": "text", "text": f"[添付({kind}) url: {a['url']} name={a.get('name')}]"})
                elif a.get("name") or a.get("mime"):
                    # バイト列が取れなくても種別・名前・サイズは文脈として必ず残す
                    kind = media_kind_ja(a.get("mime"), a.get("name"))
                    size = human_size(a.get("size") or a.get("bytes"))
                    content.append({"type": "text",
                                    "text": f"[添付({kind} 未取得) {a.get('name') or '?'} {a.get('mime') or ''} {size}]"})
            if not content:
                continue
            hints = {"title": title} if (title and i == 0) else {}
            events.append(make_event(
                source_kind=src, session_key=skey, seq=i, actor=role, kind="message",
                content=content, locator=f"{locator}#m{i}", ts=m.get("ts") or cap_ts,
                native_id=nid, hints=hints,
            ))
        return events
