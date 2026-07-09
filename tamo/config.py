"""tamo.config — sources.toml の読み書きとトークン管理。"""
from __future__ import annotations

import secrets
import tomllib
from pathlib import Path

from .i18n import lang, t
from .util import tamo_home


def sources_path() -> Path:
    return tamo_home() / "sources.toml"


def load_sources() -> list[dict]:
    p = sources_path()
    if not p.exists():
        return [{"kind": "inbox", "key": "inbox:default", "enabled": True}]
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        # 生トレースバックにしない: 行番号付きの原因と直し方を1画面で伝える
        raise SystemExit(t(
            "sources.toml の書式エラー: {err}\n"
            "  ファイル: {path}\n"
            "  手で直すか、`tamo probe --write` で再生成できます（既存は .bak に退避されます）",
            err=e, path=p)) from e
    except UnicodeDecodeError as e:
        raise SystemExit(t(
            "sources.toml がUTF-8で読めません（旧tamoがOS既定エンコーディングで書いた可能性）: {err}\n"
            "  ファイル: {path}\n"
            "  `tamo probe --write` で再生成してください（既存は .bak に退避されます）",
            err=e, path=p)) from e
    return [s for s in data.get("source", []) if s.get("enabled", True)]


def inbox_token() -> str:
    p = tamo_home() / "inbox.token"
    if not p.exists():
        # 改行を付ける（catでプロンプトと合体して見えなくなる事故防止）
        p.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        p.chmod(0o600)
    return p.read_text(encoding="utf-8").strip()


# ------------------------------------------------------------ 設定(NFR含む)

_DEFAULT_SETTINGS = {
    # days: 0 = 無期限（収集器の既定。ソース側が30日で消える前提の受け皿なので消さない）
    # warn_db_mb: 総ディスク使用量がこのMBを超えたら日次で警告（0=警告しない）。
    #   無期限保存は「黙って増え続ける」のが最大のリスクなので、消さない代わりに必ず知らせる
    "retention": {"days": 0, "warn_db_mb": 2048},
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
# 業務PCでは社内のデータ保持規程に合わせる（例: days = 90）ことを推奨します
# — 無期限保存のリスクは docs/ARCHITECTURE.md の「保存期間のリスク」を参照。
days = 0

# 総ディスク使用量（DB+添付CAS）がこのMBを超えたら serve/watch/stats が日次で警告します。
# 0 = 警告しない。無期限保存の既定に対する「黙って増え続けない」ための防波堤です。
warn_db_mb = 2048

[serve]
interval = 60      # 収集ポーリング間隔(秒)
inbox_port = 8787  # ブラウザ拡張の投函口
mcp_port = 8788    # MCP (streamable-http) ポート
"""

# 文書型の定数はt()辞書でなく言語別定数で持つ（cli.pyのフックスニペットと同じ方針）
_SETTINGS_TEMPLATE_EN = """\
# tamo settings — non-functional requirements
# If this file is absent, defaults apply.

[retention]
# Retention period in days. 0 = unlimited (default).
# tamo is the receptacle that outlives the sources (e.g. Claude Code deletes its
# transcripts after 30 days by default), so nothing is deleted by default.
# With N>0, the daily auto-prune of `tamo prune` / `tamo serve` deletes data older
# than N days, judged by **event activity time** (file mtime is never used).
# Note: unlike Claude Code, 0 means "never delete" — it does not stop collection.
# On work machines we recommend matching your data-retention policy (e.g. days = 90)
# — see the retention-risk section in docs/ARCHITECTURE.md.
days = 0

# When total disk usage (DB + attachment CAS) exceeds this many MB, serve/watch/stats
# warn once a day. 0 = no warning. The guardrail that keeps the unlimited default
# from growing silently.
warn_db_mb = 2048

[serve]
interval = 60      # collection polling interval (seconds)
inbox_port = 8787  # browser-extension inbox
mcp_port = 8788    # MCP (streamable-http) port
"""


def settings_path() -> Path:
    return tamo_home() / "settings.toml"


def ensure_settings_file() -> Path:
    p = settings_path()
    if not p.exists():
        # コメントを表示言語で生成（設定キー自体は言語非依存）
        p.write_text(_SETTINGS_TEMPLATE if lang() == "ja" else _SETTINGS_TEMPLATE_EN, encoding="utf-8")
    return p


def load_settings() -> dict:
    import copy
    import os
    import sys

    s = copy.deepcopy(_DEFAULT_SETTINGS)
    p = settings_path()
    if p.exists():
        try:
            with p.open("rb") as f:
                data = tomllib.load(f)
            for sect, vals in data.items():
                if isinstance(vals, dict):
                    s.setdefault(sect, {}).update(vals)
        except Exception as e:  # noqa: BLE001  壊れた設定ファイルでも既定値で動き続ける
            # ただし無言にはしない — typoした設定が効いていないことに気づけるように
            print(t("[tamo] settings.toml が読めません（既定値で続行）: {err}\n  ファイル: {path}",
                    err=e, path=p), file=sys.stderr)
    env = os.environ.get("TAMO_RETENTION_DAYS")
    if env and env.isdigit():
        s["retention"]["days"] = int(env)
    return s
