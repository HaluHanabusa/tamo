"""encoding耐性: cp932ロケール相当の環境でも導入導線と出力が壊れないこと。

日本語Windowsの実態(cp932)を PYTHONIOENCODING で全OS上に再現する。
過去バグ: probe --writeがcp932でsources.tomlを書き、厳格UTF-8のtomllibが
読めず全collectが死んだ / pack出力の ⟨⟩ がUnicodeEncodeErrorで落ちた。
"""
from __future__ import annotations

import tomllib

from conftest import run_tamo

CP932 = {"PYTHONIOENCODING": "cp932"}


def test_probe_write_under_cp932(fixture_env):
    home, state = fixture_env
    r = run_tamo("probe", "--home", str(home), "--write", env=CP932)
    assert r.returncode == 0, r.stderr
    raw = (state / "sources.toml").read_bytes()
    tomllib.loads(raw.decode("utf-8"))  # UTF-8で書かれ、読取側の契約を満たす

    r = run_tamo("collect", env=CP932)
    assert r.returncode == 0, r.stderr


def test_pack_and_recall_under_cp932(fixture_env, tmp_path):
    home, state = fixture_env
    run_tamo("probe", "--home", str(home), "--write")
    run_tamo("collect")

    out = tmp_path / "pack.md"
    r = run_tamo("pack", "--out", str(out), env=CP932)
    assert r.returncode == 0, r.stderr
    assert "⟨e:" in out.read_text(encoding="utf-8")  # cp932非対応文字もUTF-8ファイルで書ける

    # stdoutパイプ（キャプチャ）でも 🕒★📎⟨⟩ を含むrecallが落ちない
    r = run_tamo("recall", "決定論", env=CP932)
    assert r.returncode == 0, r.stderr
    assert "UnicodeEncodeError" not in r.stderr
