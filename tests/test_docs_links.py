"""ドキュメントの相対リンクが全て実在するファイルを指すことの検証。

言語別ディレクトリ(docs/ja/)構成はリンクの相対パスを間違えやすいので、
移動・改稿のたびにここで機械検証する。
"""
from __future__ import annotations

import re

from conftest import REPO

_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _md_files():
    yield from REPO.glob("*.md")
    yield from (REPO / "docs").rglob("*.md")
    yield from (REPO / "browser-extension").glob("*.md")


def test_relative_markdown_links_resolve():
    bad = []
    for f in _md_files():
        for m in _LINK.finditer(f.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (f.parent / target).exists():
                bad.append(f"{f.relative_to(REPO)} -> {target}")
    assert not bad, "リンク切れ:\n" + "\n".join(bad)


def test_no_stray_ja_suffix_files():
    """翻訳は docs/ja/ に集約する（*.ja.md 方式へ逆戻りさせない）。"""
    stray = [str(p.relative_to(REPO)) for p in _md_files() if p.name.endswith(".ja.md")]
    assert not stray, f"*.ja.md が残っています（docs/ja/ へ）: {stray}"


def test_no_self_links():
    """言語切替行が自分自身を指す事故（移動時にパス更新を忘れた印）を検出する。"""
    bad = []
    for f in _md_files():
        for m in _LINK.finditer(f.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            try:
                if (f.parent / target).resolve() == f.resolve():
                    bad.append(f"{f.relative_to(REPO)} -> {target}")
            except OSError:
                pass
    assert not bad, "自己参照リンク:\n" + "\n".join(bad)
