"""pytest共通フィクスチャ。

テストは2レベル:
  - 直接呼び出し（Store/CAS/redact等のユニット）
  - subprocessでのCLI実行（run_tamo — 実ユーザーと同じ経路。encoding系はこちらでしか再現できない）
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def tamo_home(tmp_path, monkeypatch) -> Path:
    """空のTAMO_HOMEを用意して環境変数を向ける。"""
    home = tmp_path / "state"
    home.mkdir()
    monkeypatch.setenv("TAMO_HOME", str(home))
    return home


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """make_fixtures.py の5ソース模擬環境を生成し (home, tamo_home) を返す。"""
    home = tmp_path / "home"
    state = tmp_path / "state"
    subprocess.run(
        [sys.executable, str(REPO / "tests" / "make_fixtures.py"), str(home), str(state)],
        check=True, cwd=str(REPO), capture_output=True,
    )
    monkeypatch.setenv("TAMO_HOME", str(state))
    return home, state


def run_tamo(*args: str, env: dict | None = None, input_text: str | None = None,
             timeout: int = 120) -> subprocess.CompletedProcess:
    """CLIをsubprocessで実行（TAMO_HOMEは呼び出し元のenvironを引き継ぐ）。"""
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "tamo.cli", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=e, input=input_text, timeout=timeout,
    )
