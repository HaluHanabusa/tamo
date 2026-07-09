"""E2E検証用のフィクスチャ環境を生成する。

/tmp/tamo_demo/home 配下に、実物と同じレイアウトで5ソース分の擬似データを作る:
  - ~/.claude/projects/…/sess.jsonl   (画像b64・ファイル2版・エラー→修正・決定/TODOを含む)
  - ~/.config/Cursor/…/state.vscdb    (cursorDiskKV: composerData + bubbleId)
  - ~/.codex/sessions/…/rollout.jsonl
  - ~/proj/.aider.chat.history.md
  - $TAMO_HOME/inbox/…json            (ChatGPT Web想定・添付txt付き)
"""
from __future__ import annotations

import base64
import json
import sqlite3
import sys
from pathlib import Path

HOME = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tamo_demo/home")
TAMO = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/tamo_demo/state")

PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
           "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

FILE_V1 = "\n".join([f"# app.py v1 line {i}: placeholder logic for collector pipeline" for i in range(40)])
FILE_V2 = FILE_V1.replace("line 5:", "line 5(FIX):").replace("line 20:", "line 20(FIX):")


def claude_code():
    d = HOME / ".claude" / "projects" / "-home-hana-omnibrain"
    d.mkdir(parents=True, exist_ok=True)
    sess = "0f3b2c1a-demo"
    L = []

    def add(t, **kw):
        base = {"type": t, "sessionId": sess, "cwd": "/home/hana/omnibrain",
                "gitBranch": "feat/collector", "version": "2.1.x",
                "timestamp": f"2026-07-06T0{len(L)}:00:00Z", "uuid": f"u{len(L):03d}"}
        base.update(kw)
        L.append(json.dumps(base, ensure_ascii=False))

    add("user", message={"role": "user", "content": [
        {"type": "text", "text": "OmniBrainの収集器を設計したい。方針: 収集はLLMを使わず決定論でいくことにする。"}]})
    add("assistant", message={"role": "assistant", "model": "claude-opus-4-8", "content": [
        {"type": "text", "text": "了解。決定: SQLiteはスナップショットコピーしてから読む方式を採用します。前提: ソースには一切書き込まないこと。"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/home/hana/omnibrain/app.py"}}]})
    add("user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": FILE_V1}]}]})
    add("assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/home/hana/omnibrain/app.py", "old_string": "line 5:", "new_string": "line 5(FIX):"}}]})
    add("user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": FILE_V2}]}]})
    add("user", message={"role": "user", "content": [
        {"type": "text", "text": "スクショ添付する。Review Queue UIのこの部分。"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_1PX}}]})
    add("user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t3", "content": [{"type": "text",
            "text": "Traceback (most recent call last):\n  File \"parse.py\", line 12\nKeyError: 'text'\nexit code 1"}]}]})
    add("assistant", message={"role": "assistant", "content": [
        {"type": "text", "text": "原因はbubbleスキーマの差分。richTextへのフォールバックを追加する修正で解決した。TODO: bubbleId新形式のフィクスチャ追加。"}]})
    add("summary", summary="収集器の設計方針を確定（決定論・スナップショット読み）", leafUuid="u007")
    (d / f"{sess}.jsonl").write_text("\n".join(L) + "\n")


def cursor():
    d = HOME / ".config" / "Cursor" / "User" / "globalStorage"
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "state.vscdb")
    con.execute("CREATE TABLE IF NOT EXISTS cursorDiskKV(key TEXT PRIMARY KEY, value TEXT)")
    rows = [
        ("composerData:c-tamo", json.dumps({"composerId": "c-tamo", "name": "tamo収集器の命名"}, ensure_ascii=False)),
        ("bubbleId:c-tamo:b1", json.dumps({"type": 1, "text": "収集器の名前どうする？魚を掬う感じの。"}, ensure_ascii=False)),
        ("bubbleId:c-tamo:b2", json.dumps({"type": 2, "richText": "タモ網から「tamo」を提案。決定: CLI名はtamoにする。"}, ensure_ascii=False)),
        ("bubbleId:c-tamo:b3", json.dumps({"type": 2, "weirdNewField": {"x": 1}}, ensure_ascii=False)),  # 空文=スキップされる
        ("someOtherKey:x", "{\"noise\":true}"),
    ]
    con.executemany("INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES(?,?)", rows)
    con.commit()
    con.close()


def codex():
    d = HOME / ".codex" / "sessions" / "2026" / "07" / "06"
    d.mkdir(parents=True, exist_ok=True)
    L = [
        {"timestamp": "2026-07-06T10:00:00Z", "type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "codex側の履歴もtamoで吸えるか試す"}]},
        {"timestamp": "2026-07-06T10:00:05Z", "type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "rollout形式はJSONL追記なのでオフセットカーソルで増分取込できる。"}]},
        {"unknown_shape": {"v": 2}},  # 未知行 → イベント0件でrawのみ
    ]
    (d / "rollout-2026-07-06-demo.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in L) + "\n")


def aider():
    d = HOME / "proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".aider.chat.history.md").write_text(
        "# aider chat started at 2026-07-05 21:00:00\n\n"
        "#### aiderの履歴もtamoの対象にしたい\n\n"
        "了解。`.aider.chat.history.md` は追記型Markdownなのでオフセット増分で読める。\n"
    )


def inbox():
    d = TAMO / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    att = base64.b64encode("添付メモ: LP検証は48時間で判定する".encode()).decode()
    body = {
        "schema": "tamo.inbox.v1", "source": "chatgpt_web", "session": "conv-abc123",
        "title": "3Dリプレイ収益化の相談",
        "messages": [
            {"role": "user", "text": "ブラウザ側の会話も収集器に入れたい", "ts": "2026-07-06T12:00:00Z",
             "attachments": [{"name": "memo.txt", "mime": "text/plain", "data_b64": att}]},
            {"role": "assistant", "text": "MV3拡張からlocalhostのinboxへPOSTすればよい。", "ts": "2026-07-06T12:00:10Z"},
        ],
    }
    (d / "demo.json").write_text(json.dumps(body, ensure_ascii=False))


if __name__ == "__main__":
    for fn in (claude_code, cursor, codex, aider, inbox):
        fn()
    print(f"fixtures -> {HOME} / inbox -> {TAMO / 'inbox'}")
