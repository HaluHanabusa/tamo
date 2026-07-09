"""添付物(PDF/docx/xlsx)のE2E検証。

合成した最小ファイルを inbox v1 形式で投函 → collect → 
FTSで「添付の中身」を日本語検索 → store.get_blob_text で抽出結果を確認する。
"""
from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path

TAMO = Path(sys.argv[1])


def make_pdf() -> bytes:
    """非圧縮・リテラル文字列のみの最小PDF（素朴抽出のハッピーパス）。"""
    content = b"BT /F1 12 Tf 72 720 Td (Deck crane safety spec: use EARS notation) Tj T* [(Budget) -250 (48h LP validation)] TJ ET"
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n",
        b"4 0 obj << /Length " + str(len(content)).encode() + b" >> stream\n" + content + b"\nendstream endobj\n",
    ]
    body = b"%PDF-1.4\n" + b"".join(objs)
    return body + b"trailer << /Root 1 0 R >>\n%%EOF"


def make_docx() -> bytes:
    doc = ("<?xml version='1.0'?><w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
           "<w:body>"
           "<w:p><w:r><w:t>艤装クレーン安全仕様書</w:t></w:r></w:p>"
           "<w:p><w:r><w:t xml:space='preserve'>決定: 甲板カバー連動条件は</w:t></w:r>"
           "<w:r><w:t>EARS記法のWHILE節で書く</w:t></w:r></w:p>"
           "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def make_xlsx() -> bytes:
    ss = ("<?xml version='1.0'?><sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
          "<si><t>ハッチカバー開閉トルク</t></si><si><t>320 kN·m</t></si></sst>")
    wb = "<workbook><sheets><sheet name='トルク実測' sheetId='1'/></sheets></workbook>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/sharedStrings.xml", ss)
    return buf.getvalue()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


if __name__ == "__main__":
    inbox = TAMO / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": "tamo.inbox.v1", "source": "claude_web", "session": "att-demo-1",
        "title": "添付E2E: 仕様書レビュー",
        "messages": [
            {"role": "user", "ts": "2026-07-07T15:00:00Z",
             "text": "この3ファイルをレビューして。仕様書と実測値とPDFメモ。",
             "attachments": [
                 {"name": "crane_spec.docx", "data_b64": b64(make_docx())},           # mime無し→sniffで判定させる
                 {"name": "torque.xlsx", "mime": "application/octet-stream", "data_b64": b64(make_xlsx())},  # 嘘mime→magic優先を確認
                 {"name": "memo.pdf", "mime": "application/pdf", "data_b64": b64(make_pdf())},
             ]},
            {"role": "assistant", "ts": "2026-07-07T15:00:30Z",
             "text": "受領。docxの決定事項とxlsxのトルク実測を確認します。"},
        ],
    }
    (inbox / "att_demo.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print("posted att_demo.json (docx/xlsx/pdf)")
