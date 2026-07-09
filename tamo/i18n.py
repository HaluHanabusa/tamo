"""tamo.i18n — UIメッセージの最小翻訳層（gettext方式・msgid=日本語）。

設計方針:
  - 翻訳対象は「UIメッセージ」だけ: stderr/確認プロンプト/--helpヘルプ/HTTPエラー本文/
    起動バナー/MCPツール説明。pack・recall・mirror・rules の本文や保存データの語彙
    （[添付(動画 未取得)] 等）は「同入力→同出力」の決定論成果物なのでロケールで変えない。
  - msgid はソース中の日本語そのもの。英語辞書(_EN)に無いキーは日本語のまま出る
    （黙って壊れず、未訳が目に見える）。網羅は tests/test_i18n.py がASTスキャンで強制する。
  - f-string は使わず `t("… {name} …", name=value)` 形式で渡す（キーが揺れないように）。

言語判定: TAMO_LANG > LC_ALL/LC_MESSAGES/LANG > WindowsのUIロケール > 既定 "en"。
"""
from __future__ import annotations

import os


def _detect() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            return "ja" if v.lower().startswith("ja") else "en"
    if os.name == "nt":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if langid & 0xFF == 0x11:  # 日本語 (primary language ID)
                return "ja"
            return "en"
        except Exception:  # noqa: BLE001
            pass
    try:
        import locale

        loc = (locale.getlocale()[0] or "").lower()
        if loc.startswith(("ja", "japanese")):
            return "ja"
    except Exception:  # noqa: BLE001
        pass
    return "en"


_DETECTED = _detect()


def lang() -> str:
    """現在の表示言語。TAMO_LANG は毎回読む（テスト・一時切替のため）。"""
    v = os.environ.get("TAMO_LANG")
    if v:
        return "ja" if v.lower().startswith("ja") else "en"
    return _DETECTED


def t(msg: str, **kw) -> str:
    """UIメッセージを現在言語で返す。英語辞書に無ければ日本語のまま（安全側）。"""
    out = msg if lang() == "ja" else _EN.get(msg, msg)
    return out.format(**kw) if kw else out


# ---------------------------------------------------------------------------
# 英語辞書（msgid=日本語）。キーの網羅と{placeholder}の一致は tests/test_i18n.py が検査する。
_EN: dict[str, str] = {
    # ---- cli.py: lock / collect ----
    "別のtamoが実行中です (PID {pid})。serve/watch常駐中なら手動collectは不要です。\n"
    "  そのPIDが本当に存在しないのにこの表示が続く場合は {lock} を削除してください":
        "Another tamo is running (PID {pid}). While serve/watch is resident, manual collect is unnecessary.\n"
        "  If that PID really does not exist and this keeps appearing, delete {lock}",
    "[tamo] 残留ロックを回収しました（PID {pid} は動いていません）: {lock}":
        "[tamo] Reclaimed a stale lock (PID {pid} is not running): {lock}",
    "ロックを取得できません: {lock}（tamoが動いていなければ削除して再実行してください）":
        "Cannot acquire the lock: {lock} (if no tamo is running, delete it and retry)",
    "  ! 未知のkind: {kind}（スキップ）": "  ! unknown kind: {kind} (skipped)",
    "合計: raw+{raw} events+{ev} quarantine+{q}": "total: raw+{raw} events+{ev} quarantine+{q}",
    "ヒント: ソースが未設定のためinboxだけを見ています。まず `tamo probe --write` で環境を走査してください":
        "Hint: no sources are configured, so only the inbox was checked. Run `tamo probe --write` first to scan your machine",
    # ---- cli.py: probe ----
    "-- 検出のみ（アダプタ設定が必要） --": "-- detected only (adapter config needed) --",
    "  ? {kind:<14} {path}\n      ヒント: {hint}": "  ? {kind:<14} {path}\n      hint: {hint}",
    "-- 既存の sources.toml を {bak} に退避しました。変更点:":
        "-- backed up the existing sources.toml to {bak}. Changes:",
    "  … 他{n}行": "  … {n} more lines",
    "-> {path} に {n} ソースを書き込みました": "-> wrote {n} sources to {path}",
    "   保持期間は既定で無期限です（業務PCでは settings.toml の [retention] days = 90 等を推奨。"
    "リスクと運用指針: docs/ARCHITECTURE.md）":
        "   Retention is unlimited by default (on work machines we recommend e.g. [retention] days = 90 "
        "in settings.toml. Risks and guidance: docs/ARCHITECTURE.md)",
    "-- sources.toml（--write で保存） --": "-- sources.toml (save with --write) --",
    # ---- cli.py: watch/serve loop ----
    "[tamo] 稼働{h:.1f}h: 直近{m}分 raw+{raw} events+{ev} ({c}サイクル)":
        "[tamo] up {h:.1f}h: last {m}min raw+{raw} events+{ev} ({c} cycles)",
    "[tamo] collect中断: {code}": "[tamo] collect aborted: {code}",
    "HTTP inboxポート {port} を開けません（{err}）。\n"
    "  別のtamo(serve/watch)が動いていませんか？ `--port <別番号>` で変更できます":
        "Cannot open HTTP inbox port {port} ({err}).\n"
        "  Is another tamo (serve/watch) running? You can change it with `--port <other>`",
    "{interval}s 間隔でポーリング収集します（WSL2の/mnt/c配下はinotifyが効かないためポーリングが正解）":
        "Polling every {interval}s (under WSL2's /mnt/c inotify does not work, so polling is the right call)",
    "serve にはMCP拡張が必要です: pip install 'mcp[cli]'\n"
    "  （MCP無しで収集+ブラウザ投函だけなら `tamo watch --http` が使えます）":
        "serve requires the MCP extra: pip install 'mcp[cli]'\n"
        "  (for collection + browser inbox without MCP, use `tamo watch --http`)",
    "MCPポート {port} は使用中です。既に tamo serve が動いていませんか？\n"
    "  変更するには: tamo serve --mcp-port <別番号>（settings.toml の [serve] mcp_port でも可）":
        "MCP port {port} is in use. Is tamo serve already running?\n"
        "  To change it: tamo serve --mcp-port <other> (or [serve] mcp_port in settings.toml)",
    "inboxポート {port} を開けません（{err}）。\n"
    "  変更するには: tamo serve --inbox-port <別番号>（settings.toml でも可）":
        "Cannot open inbox port {port} ({err}).\n"
        "  To change it: tamo serve --inbox-port <other> (or settings.toml)",
    "mcp   : http://127.0.0.1:{port}/mcp  (streamable-http, X-Tamo-Token認証)":
        "mcp   : http://127.0.0.1:{port}/mcp  (streamable-http, X-Tamo-Token auth)",
    "collect: {interval}s間隔でポーリング（ソース側の自動削除より先に掬うのが仕事）":
        "collect: polling every {interval}s (the job is to scoop before the sources auto-delete)",
    "登録例:": "Registration examples:",
    "  # stdio派: claude mcp add tamo -- tamo mcp": "  # stdio variant: claude mcp add tamo -- tamo mcp",
    # ---- cli.py: search/rules/run ----
    "(該当なし: {query!r} — 語を短くする・別の言い方を試す、または `tamo collect` で最新を取り込んでください)":
        "(no matches: {query!r} — try shorter or different words, or run `tamo collect` to ingest the latest)",
    "抽出できる決定/制約/エラー対処が見つかりませんでした":
        "No extractable decisions/constraints/error-fixes were found",
    "usage: tamo run -- <command...>   (例: tamo run -- claude)":
        "usage: tamo run -- <command...>   (e.g. tamo run -- claude)",
    "コマンドが見つかりません: {cmd}": "Command not found: {cmd}",
    # ---- cli.py: retention/prune/quarantine/purge ----
    "[tamo] データが {gb:.1f}GB に達しています（警告閾値 {warn}MB）。\n"
    "  保持期間の設定: ~/.tamo/settings.toml の [retention] days = 90 など\n"
    "  手動整理: tamo prune --days N --dry-run で確認 → --vacuum で詰める\n"
    "  この警告の閾値変更/無効化: [retention] warn_db_mb（0=警告しない）":
        "[tamo] Data has reached {gb:.1f}GB (warning threshold {warn}MB).\n"
        "  Set retention: [retention] days = 90 etc. in ~/.tamo/settings.toml\n"
        "  Manual cleanup: check with tamo prune --days N --dry-run, then compact with --vacuum\n"
        "  Change/disable this warning: [retention] warn_db_mb (0 = no warning)",
    "保持日数が未設定です: --days N を指定するか settings.toml の [retention] days を設定してください"
    "（0 = 無期限 = 何も消しません）":
        "No retention period set: pass --days N or set [retention] days in settings.toml "
        "(0 = unlimited = nothing is deleted)",
    "削除を実行するには --yes を付けてください（非対話環境）":
        "Pass --yes to actually delete (non-interactive environment)",
    "{days}日より古い上記データを削除します。よろしいですか？ [y/N] ":
        "The data above, older than {days} days, will be deleted. Proceed? [y/N] ",
    "中止しました": "Aborted",
    "id={id} は見つかりません": "id={id} not found",
    "隔離データを削除します（原文はここにしか残っていません）。実行するには --yes を付けてください。":
        "This deletes quarantined data (the raw text exists nowhere else). Pass --yes to proceed.",
    "cleared: {n}件": "cleared: {n} rows",
    "隔離データはありません（全行パース成功）": "No quarantined data (every line parsed)",
    "-- 原文: tamo quarantine show --id N / 削除: tamo quarantine clear --yes":
        "-- raw text: tamo quarantine show --id N / delete: tamo quarantine clear --yes",
    "全データ(DB/CAS/処理済みinbox)を削除します。実行するには --yes を付けてください。":
        "This deletes ALL data (DB/CAS/processed inbox). Pass --yes to proceed.",
    "purged: {items}（sources.toml / settings.toml / inbox.token / 未処理inboxは残しています）":
        "purged: {items} (sources.toml / settings.toml / inbox.token / unprocessed inbox are kept)",
    "(何もありませんでした)": "(nothing to remove)",
    # ---- cli.py: recall/show ----
    "[tamo] クリップボードにコピーしました ({method}) — そのままブラウザAIに貼れます":
        "[tamo] Copied to clipboard ({method}) — paste it straight into a browser AI",
    "[tamo] クリップボードツールが見つかりません (clip.exe/pbcopy/wl-copy/xclip)":
        "[tamo] No clipboard tool found (clip.exe/pbcopy/wl-copy/xclip)",
    "セッションがありません": "No sessions",
    "# {key}  ({n}件)": "# {key}  ({n} events)",
    # ---- cli.py: main / handlers ----
    "tamo — AIエージェント横断のコンテキスト収集器（タモ網）":
        "tamo — cross-agent context harvester (a landing net for AI conversations)",
    "\n[tamo] 停止しました (Ctrl+C)": "\n[tamo] Stopped (Ctrl+C)",
    "[tamo] エラー: {err}\n  （詳細なトレースバックは TAMO_DEBUG=1 を付けて再実行すると出ます）":
        "[tamo] Error: {err}\n  (re-run with TAMO_DEBUG=1 for the full traceback)",
    # ---- cli.py: argparse help ----
    "環境を走査してソースを自動検出": "scan the machine and auto-detect sources",
    "aider等を探す追加ディレクトリ": "extra directories to scan for aider etc.",
    "sources.tomlに書き込む": "write sources.toml",
    "全ソースを増分収集": "incrementally collect every source",
    "カーソルを無視して全再走査するkind/key": "kind/key to fully rescan, ignoring cursors",
    "このkind/keyのみ収集": "collect only this kind/key",
    "常駐（ポーリング収集 + 任意でHTTP inbox + 導出ルール自動還流）":
        "stay resident (polling collection + optional HTTP inbox + auto rule feedback)",
    "1サイクルだけ実行して終了（cron向け）": "run one cycle and exit (for cron)",
    "新イベント収集のたびに導出ルールをこのファイルへ冪等更新（例: CLAUDE.md）":
        "idempotently refresh derived rules into this file whenever new events arrive (e.g. CLAUDE.md)",
    "rules対象の部分一致フィルタ": "substring filter for rules targets",
    "統計": "statistics",
    "セッション一覧": "list sessions",
    "全文検索（FTS5 trigram: 日本語部分一致OK）": "full-text search (FTS5 trigram: CJK substring match works)",
    "面の絞り込み（source_kind部分一致）": "filter by surface (source_kind substring)",
    "トークン予算内の引き継ぎパック(Markdown)を生成": "build a handoff pack (Markdown) within a token budget",
    "特定session_keyのみ": "only this session_key",
    "NDJSONエクスポート（OmniBrain等の下流へ）": "NDJSON export (for downstream tools such as OmniBrain)",
    "添付テキストを再抽出してFTSへ遡及登録（抽出器更新後/旧DB移行用）":
        "re-extract attachment text and backfill FTS (after extractor updates / old DB migration)",
    "セッションをgitコミット可能なMarkdownとしてプロジェクトへミラー":
        "mirror sessions into the project as commit-ready Markdown",
    "出力先ディレクトリ（既定 ./.tamo/history）": "output directory (default ./.tamo/history)",
    "session_key/タイトル/locatorへの部分一致フィルタ": "substring filter on session_key/title/locator",
    "APIキー等の秘密情報をマスクして書く（既定ON。--no-redact で原文のまま）":
        "mask secrets such as API keys (on by default; --no-redact writes verbatim)",
    "履歴から導出ルール(決定/制約/エラー対処)を規則ベース抽出しCLAUDE.md等へ還流":
        "rule-based extraction of decisions/constraints/error-fixes from history, fed back into CLAUDE.md etc.",
    "直近N日のイベントのみ対象": "only events from the last N days",
    "マーカー区間を冪等更新して書き込む（例: CLAUDE.md）":
        "idempotently update the marker block in this file (e.g. CLAUDE.md)",
    "エージェントCLIをそのまま実行し、終了時に増分収集（例: tamo run -- claude）":
        "run an agent CLI as-is and collect increments on exit (e.g. tamo run -- claude)",
    "終了時に収集するソースkind（既定は全ソース）": "source kind to collect on exit (default: all)",
    "実行するコマンド（-- の後に書く）": "the command to run (after --)",
    "単体サービス起動: 収集 + HTTP inbox + MCP(streamable-http) + 自動prune を1プロセスで":
        "standalone service: collection + HTTP inbox + MCP (streamable-http) + auto-prune in one process",
    "収集ポーリング間隔(秒)。既定はsettings.toml": "collection polling interval in seconds (default from settings.toml)",
    "MCPポート（既定8788）": "MCP port (default 8788)",
    "inboxポート（既定8787）": "inbox port (default 8787)",
    "ブラウザ投函口を起動しない": "do not start the browser inbox",
    "新イベント収集のたびに導出ルールを冪等更新": "idempotently refresh derived rules whenever new events arrive",
    "MCPサーバ単体起動（既定stdio。`claude mcp add tamo -- tamo mcp`）":
        "run the MCP server alone (stdio by default; `claude mcp add tamo -- tamo mcp`)",
    "streamable-httpで常駐": "stay resident over streamable-http",
    "保持期間を超えた古いデータを削除（活動時刻基準・mtime不使用）":
        "delete data older than the retention period (activity-time based; mtime is never used)",
    "保持日数（省略時はsettings.tomlのretention.days）": "retention days (defaults to retention.days in settings.toml)",
    "削除せず件数だけ表示": "show counts only, delete nothing",
    "確認プロンプトを省略して削除（purgeと同じ方針）": "skip the confirmation prompt (same policy as purge)",
    "削除後にVACUUMでDBを詰める": "VACUUM the DB after deleting",
    "パース不能で隔離した行の閲覧/削除（増加はアダプタのドリフト兆候）":
        "inspect/delete quarantined lines (growth signals adapter drift)",
    "show対象のid": "id for show",
    "source_kindの部分一致で絞り込み": "filter by source_kind substring",
    "clearの確認を省略": "skip confirmation for clear",
    "全データ削除（DB/CAS/処理済みinbox）。設定とトークンは残す":
        "delete all data (DB/CAS/processed inbox); config and token are kept",
    "確認なしで実行": "run without confirmation",
    "「あの件どうなってた？」一発調査（検索+前後の顛末+添付根拠をMarkdown合成）":
        "one-shot 'what happened with X?' (search + surrounding outcomes + attachment evidence, stitched into Markdown)",
    "面の絞り込み（部分一致: gemini / chatgpt / claude_web / claude_code / cursor…）":
        "filter by surface (substring: gemini / chatgpt / claude_web / claude_code / cursor…)",
    "結果をクリップボードへ（WSLはclip.exe/UTF-16LE対応）": "copy the result to the clipboard (WSL uses clip.exe/UTF-16LE)",
    "1セッションを表示（latest可・--tail/--since-eventで途中から）":
        "show one session (latest works; resume with --tail/--since-event)",
    "session_key、または latest / latest:<source_kind>": "session_key, or latest / latest:<source_kind>",
    "末尾N件だけ": "only the last N events",
    "このevent_id（8桁短縮可）の次から": "from just after this event_id (8-char short form accepted)",
    "このISO時刻より後だけ": "only events after this ISO timestamp",
    "CES JSONで出力": "output raw CES JSON",
    "ブラウザ拡張用のinboxトークンを表示（無ければ生成）": "print the inbox token for the browser extension (generated if missing)",
    "Claude Code等のフック設定スニペットを表示": "print hook config snippets for Claude Code etc.",
    "(フックから呼ばれる) stdinのJSONで対象transcriptを即時取込":
        "(called by hooks) immediately ingest the transcript named in stdin JSON",
    # ---- config.py ----
    "sources.toml の書式エラー: {err}\n"
    "  ファイル: {path}\n"
    "  手で直すか、`tamo probe --write` で再生成できます（既存は .bak に退避されます）":
        "sources.toml syntax error: {err}\n"
        "  file: {path}\n"
        "  Fix it by hand, or regenerate with `tamo probe --write` (the old file is backed up to .bak)",
    "sources.toml がUTF-8で読めません（旧tamoがOS既定エンコーディングで書いた可能性）: {err}\n"
    "  ファイル: {path}\n"
    "  `tamo probe --write` で再生成してください（既存は .bak に退避されます）":
        "sources.toml is not readable as UTF-8 (an old tamo may have written it in the OS default encoding): {err}\n"
        "  file: {path}\n"
        "  Regenerate with `tamo probe --write` (the old file is backed up to .bak)",
    "[tamo] settings.toml が読めません（既定値で続行）: {err}\n  ファイル: {path}":
        "[tamo] Cannot read settings.toml (continuing with defaults): {err}\n  file: {path}",
    # ---- store.py ----
    "このDB({path})は新しいtamo(schema v{ver})で作られています。tamoを更新してください（このtamoは v{cur} まで）":
        "This DB ({path}) was created by a newer tamo (schema v{ver}). Please upgrade tamo (this one supports up to v{cur})",
    # ---- probe.py ----
    "WSL2/Windows境界を検出: Windowsユーザー {users} 側も走査します":
        "WSL2/Windows boundary detected: also scanning the Windows side for users {users}",
    "ネイティブWindowsを検出: %APPDATA% 配下(Cursor/Windsurf/Cline等)も走査します":
        "Native Windows detected: also scanning under %APPDATA% (Cursor/Windsurf/Cline etc.)",
    "CLAUDE_CONFIG_DIR={dir} を検出（VS/VS Code拡張もこの場所を共有します）":
        "CLAUDE_CONFIG_DIR={dir} detected (VS/VS Code extensions share this location)",
    "claude_code[{label}]: {root} (transcript {n}件)": "claude_code[{label}]: {root} ({n} transcripts)",
    "{label} のClaude Code拡張を検出: セッションは上記 .claude/projects のtranscriptに書かれるので追加設定は不要です":
        "Claude Code extension detected in {label}: its sessions are written to the .claude/projects transcripts above, so no extra config is needed",
    "Visual Studio拡張(win:{user}) を検出: これも .claude/projects / CLAUDE_CONFIG_DIR のtranscriptを読み書きします":
        "Visual Studio extension detected (win:{user}): it also reads/writes the .claude/projects / CLAUDE_CONFIG_DIR transcripts",
    "cursorDiskKV相当のテーブル構造をprobe出力で確認し、アダプタ追加/流用を検討":
        "check the cursorDiskKV-like table structure in the probe output, then add or reuse an adapter",
    "aider: {n}ファイル": "aider: {n} files",
    "logs.json/checkpointは配列JSON。必要なら専用アダプタ追加":
        "logs.json/checkpoint are JSON arrays; add a dedicated adapter if needed",
    "generic_jsonl (role/content) で概ね取れる": "generic_jsonl (role/content) mostly covers this",
    "形式をprobeで確認のうえgeneric_jsonl設定": "check the format via probe, then configure generic_jsonl",
    "# tamo sources — `tamo probe --write` により生成。手で編集可。":
        "# tamo sources — generated by `tamo probe --write`. Safe to edit by hand.",
    "# 検出のみ: {kind} @ {path}  ヒント: {hint}": "# detected only: {kind} @ {path}  hint: {hint}",
    # ---- http_inbox.py ----
    "bad token: `tamo token` の値と拡張のトークン設定を一致させるか、拡張の「再ペアリング」を押してください":
        "bad token: make the extension's token match the value of `tamo token`, or press \"Re-pair\" in the extension",
    "bad token: 拡張の「再ペアリング」で解消できます": "bad token: pressing \"Re-pair\" in the extension fixes this",
    "bad token: トークン不一致です。拡張の「再ペアリング」を押すか、`tamo token` の値を設定に貼ってください":
        "bad token: token mismatch. Press \"Re-pair\" in the extension, or paste the value of `tamo token` into its settings",
    # ---- mcp_server.py ----
    "mcpパッケージが必要です: pip install 'mcp[cli]'": "The mcp package is required: pip install 'mcp[cli]'",
    "unauthorized: X-Tamo-Token ヘッダ（`tamo token` の値）が必要です。"
    "登録例: claude mcp add --transport http tamo http://127.0.0.1:8788/mcp"
    " --header \"X-Tamo-Token: $(tamo token)\"":
        "unauthorized: the X-Tamo-Token header (value of `tamo token`) is required. "
        "Example: claude mcp add --transport http tamo http://127.0.0.1:8788/mcp"
        " --header \"X-Tamo-Token: $(tamo token)\"",
    "mcp SDKが古くhttp認証を適用できません: pip install -U 'mcp[cli]'":
        "The mcp SDK is too old to apply HTTP auth: pip install -U 'mcp[cli]'",
    "[tamo] MCP: http://{host}:{port}/mcp （X-Tamo-Token 認証・値は `tamo token`）":
        "[tamo] MCP: http://{host}:{port}/mcp (X-Tamo-Token auth; value = `tamo token`)",
}
