"""CAS: アトミック書込・破損自己修復・マジックナンバー判定・重複排除。"""
from __future__ import annotations

from pathlib import Path

from tamo.cas import CAS, sniff


def test_atomic_write_and_self_heal(tmp_path):
    c = CAS(tmp_path / "cas")
    data = b"tamo cas verify " * 64
    m1 = c.put(data, "text/plain", "a.txt")
    p = Path(m1["path"])
    assert p.read_bytes() == data

    # クラッシュ書きかけ（サイズ不一致）を再現 → 再putで自己修復されること
    p.write_bytes(b"CORRUPT")
    m2 = c.put(data, "text/plain", "a.txt")
    assert Path(m2["path"]).read_bytes() == data
    # tempファイルの残骸が無いこと
    assert not list((tmp_path / "cas").rglob("*.tmp*"))


def test_dedup_same_bytes(tmp_path):
    c = CAS(tmp_path / "cas")
    m1 = c.put(b"same-bytes-here-123", None, "x.bin")
    m2 = c.put(b"same-bytes-here-123", None, "y.bin")
    assert m1["sha256"] == m2["sha256"] and m1["path"] == m2["path"]
    files = [p for p in (tmp_path / "cas").rglob("*") if p.is_file()]
    assert len(files) == 1  # 同一バイナリは1回しか保存されない


def test_sniff_magic_over_declared_mime():
    """申告mimeが嘘でもマジックナンバーで正しく判定する。"""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))
    from make_attachment_fixtures import make_pdf, make_xlsx

    ext, mime = sniff(make_xlsx(), "application/octet-stream")  # 嘘mime
    assert (ext, mime) == (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    ext, mime = sniff(make_pdf(), None)
    assert (ext, mime) == (".pdf", "application/pdf")
    ext, mime = sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "text/plain")
    assert (ext, mime) == (".png", "image/png")
