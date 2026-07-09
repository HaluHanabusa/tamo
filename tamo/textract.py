"""tamo.textract — 添付ファイルからの決定論的テキスト抽出。

方針:
  - LLM不使用・依存ゼロ（stdlibのみ）。pypdf があれば PDF だけ品質が上がる（任意依存）。
  - Office系 (docx/xlsx/pptx) は実体がZIP+XMLなので zipfile + 正規表現で抽出できる。
  - PDF は pypdf → 無ければ stdlib素朴抽出（FlateDecode + リテラル文字列演算子のみ）。
    日本語PDFに多いCIDフォントは16進文字列(<...>)で来るため素朴抽出では文字化けする。
    → 16進文字列は最初から読まない + 品質ゲートで低品質テキストは破棄（誤情報を入れない）。
  - 抽出結果は FTS に載せて「添付の中身まで検索できる」状態にするのが目的。
"""
from __future__ import annotations

import html
import io
import re
import zipfile
import zlib

MAX_TEXT = 200_000  # 保存上限（文字）
MAX_BYTES = 32 * 1024 * 1024  # これ以上のファイルは抽出しない

_TEXTY_MIME = re.compile(r"^text/|json$|xml$|csv$|javascript$|x-sh$|markdown$")


def extract_text(data: bytes, mime: str, name: str | None = None) -> tuple[str, str]:
    """(text, extractor名) を返す。抽出不能/低品質なら ("", "")。"""
    if not data or len(data) > MAX_BYTES:
        return "", ""
    mime = (mime or "").split(";")[0].strip().lower()
    name = name or ""
    try:
        if mime == "application/pdf" or data[:4] == b"%PDF":
            return _pdf(data)
        if data[:4] == b"PK\x03\x04":
            kind = office_kind(data)
            if kind == "docx":
                return _docx(data), "docx-xml"
            if kind == "xlsx":
                return _xlsx(data), "xlsx-xml"
            if kind == "pptx":
                return _pptx(data), "pptx-xml"
            return "", ""  # 一般zipは中身を展開しない（zip爆弾回避）
        if mime == "text/html" or name.endswith((".html", ".htm")):
            return _html(_decode(data)), "html-strip"
        if _TEXTY_MIME.search(mime) or _looks_texty(data):
            return _clip(_decode(data)), "plain"
    except Exception:  # noqa: BLE001  抽出失敗は「テキスト無し」に落とすだけ
        return "", ""
    return "", ""


def office_kind(data: bytes) -> str | None:
    """PK先頭のバイト列が docx/xlsx/pptx のどれかを内部構造で判定。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist()[:200])
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------ helpers

def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp932", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _looks_texty(data: bytes) -> bool:
    head = data[:4096]
    if b"\x00" in head:
        return False
    return sum(1 for b in head if b in (9, 10, 13) or 32 <= b < 127 or b >= 128) / max(len(head), 1) > 0.95


def _clip(t: str) -> str:
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t[:MAX_TEXT]


_TAG = re.compile(r"<[^>]+>")


def _xml_texts(xml: str, tag: str) -> list[str]:
    """<w:t ...>x</w:t> 形式から中身だけを順序どおり取り出す。"""
    out = []
    for m in re.finditer(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", xml, re.S):
        out.append(html.unescape(m.group(1)))
    return out


def _docx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = []
    for pxml in re.split(r"</w:p>", xml):
        t = "".join(_xml_texts(pxml, "w:t"))
        if t.strip():
            paras.append(t)
    return _clip("\n".join(paras))


def _xlsx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    parts = []
    try:  # シート名
        wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
        names = re.findall(r'<sheet[^>]*name="([^"]+)"', wb)
        if names:
            parts.append("sheets: " + ", ".join(html.unescape(n) for n in names))
    except KeyError:
        pass
    try:  # 共有文字列（セルの文字はほぼここに集約される）
        ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        parts.extend(html.unescape(_TAG.sub("", si)) for si in _xml_texts(ss, "si"))
    except KeyError:
        pass
    # inlineStr のセルも一応拾う
    for n in z.namelist():
        if n.startswith("xl/worksheets/") and n.endswith(".xml") and len(parts) < 5000:
            sheet = z.read(n).decode("utf-8", "replace")
            parts.extend(html.unescape(x) for x in _xml_texts(sheet, "t") if x.strip())
    return _clip("\n".join(p for p in parts if p.strip()))


def _pptx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    slides = sorted(n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    out = []
    for n in slides:
        xml = z.read(n).decode("utf-8", "replace")
        texts = _xml_texts(xml, "a:t")
        if texts:
            out.append(f"[{n.rsplit('/', 1)[-1]}] " + " ".join(texts))
    return _clip("\n".join(out))


def _html(t: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = _TAG.sub(" ", t)
    return _clip(re.sub(r"[ \t]{2,}", " ", html.unescape(t)))


# ---------------------------------------------------------------------- PDF

def _pdf(data: bytes) -> tuple[str, str]:
    # 1) pypdf があれば最優先（ToUnicode対応で日本語PDFも読める）
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(data))
        text = _clip("\n".join((page.extract_text() or "") for page in reader.pages))
        if _quality(text) >= 0.5 and text.strip():
            return text, "pypdf"
    except Exception:  # noqa: BLE001  壊れ気味のPDFやpypdf非対応 → 素朴抽出へ
        pass
    # 2) stdlib素朴抽出（リテラル文字列のみ。CID/16進は読まない）
    text = _clip(_pdf_naive(data))
    if _quality(text) >= 0.66 and text.strip():  # 化けたら捨てる（誤テキストを検索に載せない）
        return text, "pdf-naive"
    return "", ""


_PDF_ESC = {b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b", b"f": "\f",
            b"(": "(", b")": ")", b"\\": "\\"}


def _pdf_unescape(raw: bytes) -> str:
    out, i = [], 0
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _PDF_ESC:
                out.append(_PDF_ESC[nxt])
                i += 2
                continue
            m = re.match(rb"\\([0-7]{1,3})", raw[i:])
            if m:
                out.append(chr(int(m.group(1), 8)))
                i += 1 + len(m.group(1))
                continue
            i += 1
            continue
        out.append(c.decode("latin-1"))
        i += 1
    return "".join(out)


def _pdf_naive(data: bytes) -> str:
    """stdlibのみのPDFテキスト抽出。
    FlateDecode(zlib)を試し、BT..ETブロック内のリテラル文字列 ( ) の Tj/TJ/'/" だけ読む。
    16進文字列 <...> はCIDフォントの可能性が高く化けるので読まない（品質ゲートの一部）。"""
    blocks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        s = m.group(1).rstrip(b"\r\n")
        try:
            s = zlib.decompress(s)
        except Exception:  # noqa: BLE001  非圧縮 or 他フィルタ
            pass
        if b"BT" not in s:
            continue
        for bt in re.finditer(rb"BT(.*?)ET", s, re.S):
            frags = []
            for sm in re.finditer(rb"\(((?:\\.|[^\\()])*)\)\s*(?:Tj|'|\")", bt.group(1), re.S):
                frags.append(_pdf_unescape(sm.group(1)))
            for arr in re.finditer(rb"\[((?:\\.|[^\]])*)\]\s*TJ", bt.group(1), re.S):
                for sm in re.finditer(rb"\(((?:\\.|[^\\()])*)\)", arr.group(1), re.S):
                    frags.append(_pdf_unescape(sm.group(1)))
            if frags:
                blocks.append("".join(frags))
    return "\n".join(blocks)


def _quality(t: str) -> float:
    """可読文字（英数記号・かな・漢字・全角記号）の比率。低ければ化けとみなす。"""
    if not t.strip():
        return 0.0
    good = sum(
        1 for c in t
        if c.isspace() or (32 <= ord(c) < 127)
        or 0x3000 <= ord(c) <= 0x30FF   # 全角記号・かな
        or 0x4E00 <= ord(c) <= 0x9FFF   # CJK統合漢字
        or 0xFF00 <= ord(c) <= 0xFFEF   # 全角英数
    )
    return good / len(t)
