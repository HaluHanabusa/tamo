"""inboxアダプタ: commit後move・同名衝突耐性・note保全・メタのみ添付。"""
from __future__ import annotations

import json
import sqlite3

from conftest import run_tamo


def _drop(tamo_home, name: str, payload: dict) -> None:
    inbox = tamo_home / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


PAYLOAD = {
    "schema": "tamo.inbox.v1", "source": "test_web", "session": "s1", "title": "検証",
    "note": "[上限切詰め] 古い5件を省略",
    "messages": [
        {"role": "user", "text": "inbox検証メッセージ",
         "attachments": [{"name": "big.mp4", "mime": "video/mp4", "size": 52428800}]},  # メタのみ添付
        {"role": "assistant", "text": "了解"},
    ],
}


def test_move_after_commit_and_note_meta(tamo_home):
    _drop(tamo_home, "v1.json", PAYLOAD)
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr

    # 処理成功後にdone/へ移動（commit前に動かすとクラッシュでデータを失う）
    assert not (tamo_home / "inbox" / "v1.json").exists()
    assert (tamo_home / "inbox" / "done" / "v1.json").exists()

    con = sqlite3.connect(tamo_home / "tamo.db")
    try:
        # 拡張のnoteはmetaイベントとして保全される（黙って落とさない）
        n = con.execute("SELECT COUNT(*) FROM events WHERE kind='meta' AND text LIKE '%scoop note%'").fetchone()[0]
        assert n == 1
        # メタのみ添付は「種別語 未取得 サイズ」で本文に残り検索資産になる
        row = con.execute("SELECT text FROM events WHERE text LIKE '%未取得%'").fetchone()
        assert row and "動画" in row[0] and "50.0MB" in row[0], row
    finally:
        con.close()


def test_same_name_redrop_does_not_crash(tamo_home):
    """done/に同名がある状態での再投函（旧実装はFileExistsErrorでwatchデーモンごと死んだ）。"""
    _drop(tamo_home, "v1.json", PAYLOAD)
    assert run_tamo("collect").returncode == 0
    _drop(tamo_home, "v1.json", PAYLOAD)  # 同名を再投函
    r = run_tamo("collect")
    assert r.returncode == 0, r.stderr
    assert not (tamo_home / "inbox" / "v1.json").exists()  # 上書き移動される


def test_redrop_is_idempotent(tamo_home):
    _drop(tamo_home, "a.json", PAYLOAD)
    assert run_tamo("collect").returncode == 0
    con = sqlite3.connect(tamo_home / "tamo.db")
    n1 = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    con.close()

    _drop(tamo_home, "b.json", PAYLOAD)  # 別ファイル名で同内容（ブラウザ再掬い相当）
    assert run_tamo("collect").returncode == 0
    con = sqlite3.connect(tamo_home / "tamo.db")
    n2 = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    con.close()
    assert n2 == n1  # 内容ハッシュIDなので既知の発言は増えない
