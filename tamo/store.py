"""tamo.store — 単一SQLiteによる永続層。

設計原則:
  1. raw絶対保存 — パース可否に関わらず原文を raw_records / quarantine に保存する。
     （OmniBrain ADR-024「生会話の保全」と同じ思想。最適化層は常に再構築可能。）
  2. 冪等 — event_id は決定論的に生成されるため、再収集しても重複しない。
  3. 読み取り専用 — ソース側ファイルには一切書き込まない。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .cas import CAS, extract_blobs
from .schema import blocks_text
from .util import now_iso, sha256_bytes, strip_noise, truncate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_records(
  id INTEGER PRIMARY KEY,
  source_kind TEXT NOT NULL,
  locator TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  payload BLOB NOT NULL,
  ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY,
  session_key TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  seq INTEGER,
  ts TEXT,
  actor TEXT,
  kind TEXT,
  text TEXT,
  ces TEXT NOT NULL,
  raw_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_key, seq);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS sessions(
  session_key TEXT PRIMARY KEY,
  source_kind TEXT,
  first_ts TEXT,
  last_ts TEXT,
  n_events INTEGER DEFAULT 0,
  title TEXT
);
CREATE TABLE IF NOT EXISTS blobs(
  sha256 TEXT PRIMARY KEY,
  mime TEXT, bytes INTEGER, path TEXT, name TEXT, meta TEXT, first_seen TEXT
);
CREATE TABLE IF NOT EXISTS blob_refs(
  sha256 TEXT, event_id TEXT, PRIMARY KEY(sha256, event_id)
);
CREATE TABLE IF NOT EXISTS blob_texts(
  sha256 TEXT PRIMARY KEY, extractor TEXT, chars INTEGER, text TEXT
);
CREATE TABLE IF NOT EXISTS cursors(
  source_key TEXT PRIMARY KEY, cursor TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS quarantine(
  id INTEGER PRIMARY KEY, source_kind TEXT, locator TEXT, payload BLOB, error TEXT, ts TEXT
);
"""


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "tamo.db"
        self.con = sqlite3.connect(self.db_path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")  # WAL併用時の定石(電源断でも整合、fsync半減)
        self.con.execute("PRAGMA busy_timeout=5000")   # serve(書込)とMCP(読取)の同時アクセスで即failしない
        self.con.executescript(_SCHEMA)
        self.cas = CAS(self.home / "cas")
        self.fts = self._init_fts()

    def _init_fts(self) -> bool:
        try:
            self.con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(text, event_id UNINDEXED, tokenize='trigram')"
            )
            return True
        except sqlite3.OperationalError:
            try:
                self.con.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(text, event_id UNINDEXED)"
                )
                return True
            except sqlite3.OperationalError:
                return False

    # ------------------------------------------------------------------ raw
    def put_raw(self, source_kind: str, locator: str, payload: bytes) -> tuple[int, bool]:
        cur = self.con.execute(
            "INSERT OR IGNORE INTO raw_records(source_kind, locator, sha256, payload, ingested_at) VALUES(?,?,?,?,?)",
            (source_kind, locator, sha256_bytes(payload), payload, now_iso()),
        )
        if cur.rowcount:
            return cur.lastrowid, True
        row = self.con.execute("SELECT id FROM raw_records WHERE locator=?", (locator,)).fetchone()
        return row[0], False

    def put_quarantine(self, source_kind: str, locator: str, payload: bytes, error: str) -> None:
        self.con.execute(
            "INSERT INTO quarantine(source_kind, locator, payload, error, ts) VALUES(?,?,?,?,?)",
            (source_kind, locator, payload, error, now_iso()),
        )

    # --------------------------------------------------------------- events
    def upsert_event(self, ev: dict, raw_id: int | None = None) -> bool:
        """CESイベントを保存。blob抽出→添付テキスト抽出→ノイズ除去→FTS登録まで一括。"""
        ev, blobs = extract_blobs(ev, self.cas)
        att_texts: list[str] = []
        for m in blobs:
            self.con.execute(
                "INSERT OR IGNORE INTO blobs(sha256, mime, bytes, path, name, meta, first_seen) VALUES(?,?,?,?,?,?,?)",
                (m["sha256"], m["mime"], m["bytes"], m["path"], m.get("name"), json.dumps(m, ensure_ascii=False), m["first_seen"]),
            )
            self.con.execute(
                "INSERT OR IGNORE INTO blob_refs(sha256, event_id) VALUES(?,?)", (m["sha256"], ev["event_id"])
            )
            bt = self._blob_text(m)
            if bt:
                att_texts.append(f"[添付 {m.get('name') or m['sha256'][:12]}]\n{bt[:4000]}")
        text = strip_noise(blocks_text(ev["content"]))
        cur = self.con.execute(
            "INSERT OR IGNORE INTO events(event_id, session_key, source_kind, seq, ts, actor, kind, text, ces, raw_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ev["event_id"], ev["session_key"], ev["source_kind"], ev.get("seq"), ev.get("ts"),
                ev.get("actor"), ev.get("kind"), text, json.dumps(ev, ensure_ascii=False), raw_id,
            ),
        )
        inserted = bool(cur.rowcount)
        if inserted:
            if self.fts:
                fts_text = text if not att_texts else text + "\n" + "\n".join(att_texts)
                self.con.execute("INSERT INTO events_fts(text, event_id) VALUES(?,?)", (fts_text, ev["event_id"]))
            self._touch_session(ev, text)
        return inserted

    def _blob_text(self, meta: dict) -> str:
        """blobの抽出テキストを返す（初見なら抽出してblob_textsへキャッシュ、決定論）。"""
        sha = meta["sha256"]
        row = self.con.execute("SELECT text FROM blob_texts WHERE sha256=?", (sha,)).fetchone()
        if row is not None:
            return row[0] or ""
        text, extractor = "", ""
        try:
            from .textract import MAX_BYTES, extract_text

            if meta.get("bytes", 0) <= MAX_BYTES:
                data = Path(meta["path"]).read_bytes()
                text, extractor = extract_text(data, meta.get("mime") or "", meta.get("name"))
        except Exception:  # noqa: BLE001
            text, extractor = "", ""
        self.con.execute(
            "INSERT OR IGNORE INTO blob_texts(sha256, extractor, chars, text) VALUES(?,?,?,?)",
            (sha, extractor, len(text), text),
        )
        return text

    def get_blob_text(self, sha256: str) -> dict | None:
        row = self.con.execute(
            "SELECT b.mime, b.bytes, b.name, t.extractor, t.text FROM blobs b"
            " LEFT JOIN blob_texts t ON t.sha256 = b.sha256 WHERE b.sha256=?", (sha256,)
        ).fetchone()
        if row is None:
            return None
        return {"sha256": sha256, "mime": row[0], "bytes": row[1], "name": row[2],
                "extractor": row[3] or "", "text": row[4] or ""}

    def prune(self, days: int, dry_run: bool = False) -> dict:
        """N日より古いデータを削除する（NFR: 保持期間の実装）。

        設計方針（Claude Codeのcleanup事故からの教訓）:
          - 判定は「イベントの活動時刻(ts)」。ファイルmtimeは一切見ない
          - ts不明(NULL)のイベントは安全側に倒して残す
          - dry_runで件数を必ず事前確認できる。実行時も削除内容を返して無言では消さない
          - 共有blobは参照が残る限り消さない。孤児になったCAS実体のみ物理削除
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        old = [r[0] for r in self.con.execute(
            "SELECT event_id FROM events WHERE ts IS NOT NULL AND ts < ?", (cutoff,))]
        n_raw = self.con.execute(
            "SELECT COUNT(*) FROM raw_records WHERE ingested_at < ? AND id NOT IN"
            " (SELECT raw_id FROM events WHERE raw_id IS NOT NULL AND (ts IS NULL OR ts >= ?))",
            (cutoff, cutoff)).fetchone()[0]
        n_q = self.con.execute("SELECT COUNT(*) FROM quarantine WHERE ts < ?", (cutoff,)).fetchone()[0]
        report = {"cutoff": cutoff, "events": len(old), "raw_records": n_raw,
                  "quarantine": n_q, "blobs_gc": 0, "sessions_removed": 0, "dry_run": dry_run}
        if dry_run or not (old or n_raw or n_q):
            return report

        sess_keys = {r[0] for r in self.con.execute(
            "SELECT DISTINCT session_key FROM events WHERE ts IS NOT NULL AND ts < ?", (cutoff,))}
        for i in range(0, len(old), 500):
            chunk = old[i:i + 500]
            ph = ",".join("?" * len(chunk))
            if self.fts:
                self.con.execute(f"DELETE FROM events_fts WHERE event_id IN ({ph})", chunk)
            self.con.execute(f"DELETE FROM blob_refs WHERE event_id IN ({ph})", chunk)
            self.con.execute(f"DELETE FROM events WHERE event_id IN ({ph})", chunk)
        self.con.execute(
            "DELETE FROM raw_records WHERE ingested_at < ? AND id NOT IN"
            " (SELECT raw_id FROM events WHERE raw_id IS NOT NULL)", (cutoff,))
        self.con.execute("DELETE FROM quarantine WHERE ts < ?", (cutoff,))
        # 孤児blobのGC（CAS実体も物理削除）
        for sha, path in self.con.execute(
                "SELECT sha256, path FROM blobs WHERE sha256 NOT IN (SELECT DISTINCT sha256 FROM blob_refs)").fetchall():
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            self.con.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
            self.con.execute("DELETE FROM blob_texts WHERE sha256=?", (sha,))
            report["blobs_gc"] += 1
        # セッション再集計（0件になったものは削除）
        for sk in sess_keys:
            row = self.con.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM events WHERE session_key=?", (sk,)).fetchone()
            if row[0] == 0:
                self.con.execute("DELETE FROM sessions WHERE session_key=?", (sk,))
                report["sessions_removed"] += 1
            else:
                self.con.execute(
                    "UPDATE sessions SET n_events=?, first_ts=?, last_ts=? WHERE session_key=?",
                    (row[0], row[1], row[2], sk))
        self.commit()
        return report

    def reindex_blob_texts(self) -> dict:
        """未抽出/空だったblobを再抽出し、参照イベント紐づけでFTSにも遡及登録する。
        （blob_texts導入前のDBや、抽出器改善後のやり直しに使う）"""
        from .textract import MAX_BYTES, extract_text

        rows = self.con.execute(
            "SELECT b.sha256, b.mime, b.bytes, b.path, b.name FROM blobs b"
            " LEFT JOIN blob_texts t ON t.sha256 = b.sha256"
            " WHERE t.sha256 IS NULL OR t.chars = 0"
        ).fetchall()
        done = 0
        for sha, mime, nbytes, path, name in rows:
            text, extractor = "", ""
            try:
                if (nbytes or 0) <= MAX_BYTES and Path(path).exists():
                    text, extractor = extract_text(Path(path).read_bytes(), mime or "", name)
            except Exception:  # noqa: BLE001
                pass
            self.con.execute(
                "INSERT INTO blob_texts(sha256, extractor, chars, text) VALUES(?,?,?,?)"
                " ON CONFLICT(sha256) DO UPDATE SET extractor=excluded.extractor, chars=excluded.chars, text=excluded.text",
                (sha, extractor, len(text), text),
            )
            if text and self.fts:
                for (eid,) in self.con.execute("SELECT event_id FROM blob_refs WHERE sha256=?", (sha,)):
                    self.con.execute(
                        "INSERT INTO events_fts(text, event_id) VALUES(?,?)",
                        (f"[添付 {name or sha[:12]}]\n{text[:4000]}", eid),
                    )
            done += 1 if text else 0
        self.commit()
        return {"scanned": len(rows), "extracted": done}

    def _touch_session(self, ev: dict, text: str) -> None:
        sk = ev["session_key"]
        row = self.con.execute("SELECT n_events, title, first_ts FROM sessions WHERE session_key=?", (sk,)).fetchone()
        title_hint = ev.get("hints", {}).get("title")
        if row is None:
            title = title_hint or (truncate(text.strip().splitlines()[0], 60) if text.strip() and ev.get("actor") == "user" else None)
            self.con.execute(
                "INSERT INTO sessions(session_key, source_kind, first_ts, last_ts, n_events, title) VALUES(?,?,?,?,1,?)",
                (sk, ev["source_kind"], ev.get("ts"), ev.get("ts"), title),
            )
        else:
            n, title, first_ts = row
            if not title and ev.get("actor") == "user" and text.strip():
                title = truncate(text.strip().splitlines()[0], 60)
            if title_hint:
                title = title_hint
            self.con.execute(
                "UPDATE sessions SET n_events=?, last_ts=COALESCE(?, last_ts), first_ts=COALESCE(first_ts, ?), title=? WHERE session_key=?",
                (n + 1, ev.get("ts"), ev.get("ts"), title, sk),
            )

    # -------------------------------------------------------------- cursors
    def get_cursor(self, source_key: str) -> dict:
        row = self.con.execute("SELECT cursor FROM cursors WHERE source_key=?", (source_key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_cursor(self, source_key: str, cursor: dict) -> None:
        self.con.execute(
            "INSERT INTO cursors(source_key, cursor, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(source_key) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
            (source_key, json.dumps(cursor, ensure_ascii=False), now_iso()),
        )

    # ---------------------------------------------------------------- query
    def search(self, q: str, limit: int = 10, source: str | None = None) -> list[dict]:
        """全文検索。trigramは3文字未満の語を索引できないため、
        長い語(≥3)でFTS AND検索 → 短い語(1-2字)はFTS行テキスト（添付抽出分も含む）で後段フィルタ。
        source: source_kindの部分一致絞り込み（例 "gemini"→gemini_web, "claude"→claude_web+claude_code,
        "chatgpt"/"gpt"→chatgpt_web, "code"→claude_code）。"""
        terms = [t for t in q.split() if t]
        src_like = f"%{source.strip().lower()}%" if source and source.strip() else None
        long_terms = [t for t in terms if len(t) >= 3]
        short_terms = [t for t in terms if len(t) < 3]
        rows: list[tuple] = []
        if self.fts and long_terms:
            match = " AND ".join('"' + t.replace('"', " ") + '"' for t in long_terms)
            try:
                cond = " AND e.source_kind LIKE ?" if src_like else ""
                params = [match] + ([src_like] if src_like else []) + [limit * 4 if (short_terms or src_like) else limit]
                fetched = self.con.execute(
                    "SELECT e.event_id, e.session_key, e.ts, e.actor,"
                    " snippet(events_fts, 0, '[', ']', '…', 16), events_fts.text"
                    " FROM events_fts JOIN events e ON e.event_id = events_fts.event_id"
                    f" WHERE events_fts MATCH ?{cond} ORDER BY rank LIMIT ?",
                    params,
                ).fetchall()
                for r in fetched:
                    if all(st in (r[5] or "") for st in short_terms):
                        rows.append(r[:5])
                    if len(rows) >= limit:
                        break
            except sqlite3.OperationalError:
                rows = []
        if not rows and terms:
            conds = " AND ".join("text LIKE ?" for _ in terms)
            if src_like:
                conds += " AND source_kind LIKE ?"
            rows = self.con.execute(
                f"SELECT event_id, session_key, ts, actor,"
                f" substr(text, max(1, instr(text, ?) - 40), 120)"
                f" FROM events WHERE {conds} ORDER BY ts DESC LIMIT ?",
                [terms[0], *[f"%{t}%" for t in terms], *([src_like] if src_like else []), limit],
            ).fetchall()
        return [
            {"event_id": r[0], "session_key": r[1], "ts": r[2], "actor": r[3], "snippet": r[4]}
            for r in rows
        ]

    def iter_session_events(self, session_key: str, since_event_id: str | None = None,
                            since_ts: str | None = None, tail: int | None = None) -> list[dict]:
        """セッションのCESイベントを元の順序で返す。

        再開(handoff)用スライス:
          - since_event_id: そのイベントの「次」から返す（packの ⟨e:xxxx⟩ を続きの起点にできる。
            短縮8桁でも可）。見つからない場合は空リスト（全量を誤送しない安全側）
          - since_ts: そのISO時刻より後（ts不明のmeta等は除外）
          - tail: 最後のN件だけ
        """
        if tail and tail > 0 and not since_event_id and not since_ts:
            rows = self.con.execute(
                "SELECT ces FROM events WHERE session_key=? ORDER BY seq DESC, ts DESC LIMIT ?",
                (session_key, tail)).fetchall()
            return [json.loads(r[0]) for r in reversed(rows)]
        rows = self.con.execute(
            "SELECT ces FROM events WHERE session_key=? ORDER BY seq, ts", (session_key,)
        ).fetchall()
        evs = [json.loads(r[0]) for r in rows]
        if since_event_id:
            idx = next((i for i, e in enumerate(evs)
                        if e["event_id"] == since_event_id or e["event_id"].startswith(since_event_id)), None)
            evs = evs[idx + 1:] if idx is not None else []
        if since_ts:
            evs = [e for e in evs if e.get("ts") and str(e["ts"]) > since_ts]
        if tail and tail > 0:
            evs = evs[-tail:]
        return evs

    def events_around(self, event_id: str, before: int = 3, after: int = 6) -> list[dict]:
        """指定イベント（8桁短縮可）の前後を、同一セッションの元の順序で返す。
        「検索で当てた箇所の顛末を読む」ためのgrep -C相当。対象イベントには _hit=True が付く。"""
        row = self.con.execute(
            "SELECT session_key, seq, ts, event_id FROM events WHERE event_id=? OR event_id LIKE ?",
            (event_id, event_id + "%")).fetchone()
        if not row:
            return []
        sk, seq, ts, full_id = row
        prev = self.con.execute(
            "SELECT ces FROM events WHERE session_key=? AND (seq < ? OR (seq = ? AND ts < ?))"
            " ORDER BY seq DESC, ts DESC LIMIT ?", (sk, seq, seq, ts or "", before)).fetchall()
        nxt = self.con.execute(
            "SELECT ces FROM events WHERE session_key=? AND (seq > ? OR (seq = ? AND ts > ?))"
            " ORDER BY seq, ts LIMIT ?", (sk, seq, seq, ts or "", after)).fetchall()
        hit = json.loads(self.con.execute(
            "SELECT ces FROM events WHERE event_id=?", (full_id,)).fetchone()[0])
        hit["_hit"] = True
        return [json.loads(r[0]) for r in reversed(prev)] + [hit] + [json.loads(r[0]) for r in nxt]

    def latest_session_key(self, source_kind: str | None = None) -> str | None:
        """最終活動が最も新しいセッションのキー（source_kindで絞り込み可）。"""
        if source_kind:
            row = self.con.execute(  # 部分一致（"gemini"でgemini_webに当たる）
                "SELECT session_key FROM sessions WHERE source_kind LIKE ? ORDER BY last_ts DESC LIMIT 1",
                (f"%{source_kind.strip().lower()}%",)).fetchone()
        else:
            row = self.con.execute(
                "SELECT session_key FROM sessions ORDER BY last_ts DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def recent_events(self, days: int | None = None, limit: int = 5000) -> list[dict]:
        if days:
            rows = self.con.execute(
                "SELECT ces FROM events WHERE ts IS NULL OR ts >= datetime('now', ?) ORDER BY ts, seq LIMIT ?",
                (f"-{days} days", limit),
            ).fetchall()
        else:
            rows = self.con.execute("SELECT ces FROM events ORDER BY ts, seq LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def list_sessions(self, limit: int = 30) -> list[dict]:
        rows = self.con.execute(
            "SELECT session_key, source_kind, first_ts, last_ts, n_events, title FROM sessions ORDER BY last_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        keys = ["session_key", "source_kind", "first_ts", "last_ts", "n_events", "title"]
        return [dict(zip(keys, r)) for r in rows]

    def stats(self) -> dict:
        g = lambda q: self.con.execute(q).fetchone()[0]  # noqa: E731
        per_source = self.con.execute(
            "SELECT source_kind, COUNT(*) FROM events GROUP BY source_kind ORDER BY 2 DESC"
        ).fetchall()
        return {
            "events": g("SELECT COUNT(*) FROM events"),
            "sessions": g("SELECT COUNT(*) FROM sessions"),
            "raw_records": g("SELECT COUNT(*) FROM raw_records"),
            "raw_bytes": g("SELECT COALESCE(SUM(LENGTH(payload)),0) FROM raw_records"),
            "blobs": g("SELECT COUNT(*) FROM blobs"),
            "blob_bytes": g("SELECT COALESCE(SUM(bytes),0) FROM blobs"),
            "blob_texts": g("SELECT COUNT(*) FROM blob_texts WHERE chars > 0"),
            "db_bytes": sum(p.stat().st_size for p in [self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")] if p.exists()),
            "oldest_event": g("SELECT MIN(ts) FROM events WHERE ts IS NOT NULL"),
            "quarantine": g("SELECT COUNT(*) FROM quarantine"),
            "per_source": {k: v for k, v in per_source},
            "fts": self.fts,
        }

    def commit(self) -> None:
        self.con.commit()

    def close(self) -> None:
        self.con.commit()
        self.con.close()
