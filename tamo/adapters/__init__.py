"""tamo.adapters — ソース別アダプタ。

アダプタ契約 (contract):
  collect(cursor: dict) -> (new_cursor: dict, items: list[Item])
  Item = {
    "locator": str,            # raw上の一意な位置（冪等性の鍵）
    "payload": bytes,          # 原文そのまま（raw層に保存される）
    "events":  list[CES event],# パース結果（空でもよい）
    "error":   str | None,     # パース失敗時は隔離行きの理由
  }

原則:
  - ソースには一切書き込まない（SQLiteはスナップショットコピーして読む）
  - パースは常に「寛容」。未知フィールドは無視し、失敗しても raw は残す
  - スキーマドリフト検知のため fingerprint() でキー構造を報告できる
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path


class Adapter:
    kind = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @property
    def key(self) -> str:
        return self.cfg.get("key") or f"{self.kind}:default"

    def collect(self, cursor: dict) -> tuple[dict, list[dict]]:  # pragma: no cover
        raise NotImplementedError


def item(locator: str, payload: bytes | str, events: list[dict], error: str | None = None) -> dict:
    if isinstance(payload, str):
        payload = payload.encode("utf-8", "replace")
    return {"locator": locator, "payload": payload, "events": events, "error": error}


def snapshot_sqlite(src: Path) -> Path:
    """ライブなSQLite(WAL含む)を壊さず読むため、テンポラリへ丸ごとコピーして開く。
    Cursor/VS Code系はDBを開きっぱなしにするため、直接openはロック/破損リスクがある。
    WSL2から /mnt/c 越しに読む場合も、この方式なら9pのロック問題を踏まない。
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="tamo_snap_"))
    dst = tmpdir / src.name
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        side = Path(str(src) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(dst) + suffix))
    # WALをメインDBへ取り込む
    con = sqlite3.connect(dst)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    con.close()
    return dst


def fingerprint_json_keys(obj, depth: int = 2) -> list[str]:
    """スキーマドリフト検知用: JSONのキー構造を平坦化して返す。"""
    out: set[str] = set()

    def walk(o, prefix: str, d: int):
        if d < 0:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                out.add(f"{prefix}{k}")
                walk(v, f"{prefix}{k}.", d - 1)
        elif isinstance(o, list) and o:
            walk(o[0], prefix + "[]", d - 1)

    walk(obj, "", depth)
    return sorted(out)


REGISTRY: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    REGISTRY[cls.kind] = cls
    return cls


def load_all() -> None:
    from . import aider, claude_code, codex_cli, cursor_ide, generic_jsonl, inbox  # noqa: F401
