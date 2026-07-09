"""tamo.config — sources.toml の読み書きとトークン管理。"""
from __future__ import annotations

import secrets
import tomllib
from pathlib import Path

from .util import tamo_home


def sources_path() -> Path:
    return tamo_home() / "sources.toml"


def load_sources() -> list[dict]:
    p = sources_path()
    if not p.exists():
        return [{"kind": "inbox", "key": "inbox:default", "enabled": True}]
    with p.open("rb") as f:
        data = tomllib.load(f)
    return [s for s in data.get("source", []) if s.get("enabled", True)]


def inbox_token() -> str:
    p = tamo_home() / "inbox.token"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(24) + "\n")  # 改行を付ける（catでプロンプトと合体して見えなくなる事故防止）
        p.chmod(0o600)
    return p.read_text().strip()


# ------------------------------------------------------------ 設定(NFR含む)

_DEFAULT_SETTINGS = {
    "retention": {"days": 0},  # 0 = 無期限（収集器の既定。ソース側が30日で消える前提の受け皿なので消さない）
    "serve": {"interval": 60, "inbox_port": 8787, "mcp_port": 8788},
}

_SETTINGS_TEMPLATE = """\
# tamo settings — 非機能要件の設定
# このファイルが無い場合は既定値で動きます。

[retention]
# データ保持日数。0 = 無期限（既定）。
# tamoは「ソース側(例: Claude Codeは既定30日で削除)より長く残す受け皿」なので既定では消しません。
# N>0 にすると `tamo prune` / `tamo serve` の日次自動プルーニングが
# 「イベントの活動時刻」基準でN日より古いデータを削除します（ファイルmtimeは見ません）。
# 注意: Claude Codeと違い、0 は「削除しない」の意味であり保存を止めることはありません。
days = 0

[serve]
interval = 60      # 収集ポーリング間隔(秒)
inbox_port = 8787  # ブラウザ拡張の投函口
mcp_port = 8788    # MCP (streamable-http) ポート
"""


def settings_path() -> Path:
    return tamo_home() / "settings.toml"


def ensure_settings_file() -> Path:
    p = settings_path()
    if not p.exists():
        p.write_text(_SETTINGS_TEMPLATE, encoding="utf-8")
    return p


def load_settings() -> dict:
    import copy
    import os

    s = copy.deepcopy(_DEFAULT_SETTINGS)
    p = settings_path()
    if p.exists():
        try:
            with p.open("rb") as f:
                data = tomllib.load(f)
            for sect, vals in data.items():
                if isinstance(vals, dict):
                    s.setdefault(sect, {}).update(vals)
        except Exception:  # noqa: BLE001  壊れた設定ファイルでも既定値で動き続ける
            pass
    env = os.environ.get("TAMO_RETENTION_DAYS")
    if env and env.isdigit():
        s["retention"]["days"] = int(env)
    return s
