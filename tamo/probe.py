"""tamo.probe — 実機フィンガープリンタ。

「どのエージェントにも対応」を仕様書ではなく実機で担保するための道具。
各エージェントの保存形式は非公開かつ頻繁に変わるため、tamoは
  1) 既知の候補ロケーションを走査（WSL2なら /mnt/c のWindows側、
     ネイティブWindowsなら自分の %APPDATA% 配下も見る）
  2) SQLiteはスナップショットして中のテーブル/キー分布まで検分
  3) 対応アダプタがあるものは sources.toml を自動生成、無いものは
     「検出済み・要generic_jsonl設定 or アダプタ追加」として報告
という流れで、ユーザーの環境にあるものだけを確実に拾う。
"""
from __future__ import annotations

import os
import sqlite3
from collections import Counter
from pathlib import Path

from .adapters import snapshot_sqlite
from .i18n import t


def _win_users() -> list[Path]:
    base = Path(os.environ.get("TAMO_WIN_ROOT", "/mnt/c/Users"))
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        if p.name.lower() in ("public", "default", "default user", "all users"):
            continue
        if (p / "AppData").exists():
            out.append(p)
    return out


def _inspect_vscdb(db: Path) -> dict:
    info: dict = {"path": str(db), "tables": [], "key_prefixes": {}}
    try:
        with snapshot_sqlite(db) as snap:
            con = sqlite3.connect(snap)
            try:
                info["tables"] = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if "cursorDiskKV" in info["tables"]:
                    c = Counter()
                    for (k,) in con.execute("SELECT key FROM cursorDiskKV LIMIT 20000"):
                        c[str(k).split(":", 1)[0]] += 1
                    info["key_prefixes"] = dict(c.most_common(8))
            finally:
                con.close()
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)
    return info


def run_probe(home: Path, extra_scan: list[Path] | None = None) -> dict:
    """検出結果を返す。{"sources": [source cfg...], "detected_only": [...], "notes": [...]}"""
    home = Path(home)
    sources: list[dict] = []
    detected: list[dict] = []
    notes: list[str] = []
    wins = _win_users()
    if wins:
        notes.append(t("WSL2/Windows境界を検出: Windowsユーザー {users} 側も走査します",
                       users=", ".join(p.name for p in wins)))
    # ネイティブWindowsでは自分のホームが %USERPROFILE% そのもの。
    # AppData系(Cursor/Windsurf/Cline/Copilot/VS)の走査対象に自分自身を足す
    # （従来はWSL経由 /mnt/c しか見ておらず、ネイティブWinのCursorユーザーを黙って見逃していた）
    win_self = [home] if os.name == "nt" and (home / "AppData").exists() else []
    if win_self:
        notes.append(t("ネイティブWindowsを検出: %APPDATA% 配下(Cursor/Windsurf/Cline等)も走査します"))
    win_all = wins + win_self
    home_label = "home" if os.name == "nt" else "wsl"  # 既存WSLユーザーのkey互換のためwslは維持

    # --- Claude Code (ホーム + Windows側 + CLAUDE_CONFIG_DIR) ---
    cc_roots = [(home / ".claude" / "projects", home_label)] + [
        (u / ".claude" / "projects", f"win:{u.name}") for u in wins
    ]
    ccd = os.environ.get("CLAUDE_CONFIG_DIR")
    if ccd:
        p = Path(ccd).expanduser()
        # CLAUDE_CONFIG_DIR が .claude ルートでも projects 直指しでも受ける
        cc_roots.append((p / "projects" if (p / "projects").exists() else p, "config_dir"))
        notes.append(t("CLAUDE_CONFIG_DIR={dir} を検出（VS/VS Code拡張もこの場所を共有します）", dir=ccd))
    seen_roots: set[str] = set()
    for root, label in cc_roots:
        if root.exists() and str(root) not in seen_roots:
            seen_roots.add(str(root))
            n = sum(1 for _ in root.rglob("*.jsonl"))
            sources.append({"kind": "claude_code", "key": f"claude_code:{label}", "root": str(root), "enabled": True})
            notes.append(t("claude_code[{label}]: {root} (transcript {n}件)", label=label, root=root, n=n))

    # --- VS Code / Visual Studio の Claude Code 拡張（transcriptは上記.claudeと共有） ---
    ext_globs = [
        (home / ".vscode-server" / "extensions", "anthropic.claude-code*", "VS Code (WSL remote)"),
        (home / ".vscode" / "extensions", "anthropic.claude-code*", "VS Code"),
    ] + [(u / ".vscode" / "extensions", "anthropic.claude-code*", f"VS Code (win:{u.name})") for u in wins]
    for base, pat, label in ext_globs:
        if base.exists() and any(base.glob(pat)):
            notes.append(t("{label} のClaude Code拡張を検出: セッションは上記 .claude/projects のtranscriptに書かれるので追加設定は不要です",
                           label=label))
            break
    for u in win_all:  # Visual Studio(.NET IDE)向けサードパーティ拡張の痕跡
        vsx = u / "AppData" / "Local" / "Microsoft" / "VisualStudio"
        if vsx.exists() and any(vsx.rglob("ClaudeCodeExtension*")):
            notes.append(t("Visual Studio拡張(win:{user}) を検出: これも .claude/projects / CLAUDE_CONFIG_DIR のtranscriptを読み書きします",
                           user=u.name))
            break

    # --- Codex CLI ---
    for root, label in [(home / ".codex" / "sessions", home_label)] + [
        (u / ".codex" / "sessions", f"win:{u.name}") for u in wins
    ]:
        if root.exists():
            sources.append({"kind": "codex_cli", "key": f"codex_cli:{label}", "root": str(root), "enabled": True})
            notes.append(f"codex_cli[{label}]: {root}")

    # --- Cursor / Windsurf (state.vscdb系) ---
    vs_candidates = [
        ("cursor_ide", home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb", "linux"),
        ("windsurf", home / ".config" / "Windsurf" / "User" / "globalStorage" / "state.vscdb", "linux"),
    ]
    for u in win_all:
        roam = u / "AppData" / "Roaming"
        vs_candidates.append(("cursor_ide", roam / "Cursor" / "User" / "globalStorage" / "state.vscdb", f"win:{u.name}"))
        vs_candidates.append(("windsurf", roam / "Windsurf" / "User" / "globalStorage" / "state.vscdb", f"win:{u.name}"))
    for kind, db, label in vs_candidates:
        if db.exists():
            info = _inspect_vscdb(db)
            notes.append(f"{kind}[{label}]: {db} tables={info['tables'][:4]} keys={info.get('key_prefixes')}")
            if kind == "cursor_ide" and "cursorDiskKV" in info.get("tables", []):
                sources.append({"kind": "cursor_ide", "key": f"cursor_ide:{label}", "db": str(db), "enabled": True})
            else:
                detected.append({"kind": kind, "path": str(db),
                                 "hint": t("cursorDiskKV相当のテーブル構造をprobe出力で確認し、アダプタ追加/流用を検討")})

    # --- aider（home直下1〜2階層 + 指定ディレクトリ）---
    aider_paths: list[str] = []
    scan_roots = [home] + list(extra_scan or [])
    for r in scan_roots:
        for pat in (".aider.chat.history.md", "*/.aider.chat.history.md", "*/*/.aider.chat.history.md"):
            aider_paths += [str(p) for p in Path(r).glob(pat)]
    if aider_paths:
        sources.append({"kind": "aider", "key": "aider:default", "paths": sorted(set(aider_paths)), "enabled": True})
        notes.append(t("aider: {n}ファイル", n=len(set(aider_paths))))

    # --- 検出のみ（アダプタ未実装 or 形式未固定）---
    only = [
        ("gemini_cli", home / ".gemini" / "tmp", t("logs.json/checkpointは配列JSON。必要なら専用アダプタ追加")),
        ("goose", home / ".local" / "share" / "goose" / "sessions", t("generic_jsonl (role/content) で概ね取れる")),
        ("opencode", home / ".local" / "share" / "opencode", t("形式をprobeで確認のうえgeneric_jsonl設定")),
        ("cline", home / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev", "tasks/*/api_conversation_history.json"),
    ]
    for u in win_all:
        only.append(("cline", u / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev",
                     "tasks/*/api_conversation_history.json"))
        only.append(("vscode_copilot", u / "AppData" / "Roaming" / "Code" / "User" / "workspaceStorage",
                     "*/chatSessions/*.json"))
    for kind, p, hint in only:
        if Path(p).exists():
            detected.append({"kind": kind, "path": str(p), "hint": hint})

    # --- inbox は常時有効（ブラウザ拡張の受け口）---
    sources.append({"kind": "inbox", "key": "inbox:default", "enabled": True})
    return {"sources": sources, "detected_only": detected, "notes": notes}


def dump_sources_toml(result: dict) -> str:
    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [t("# tamo sources — `tamo probe --write` により生成。手で編集可。"), ""]
    for s in result["sources"]:
        lines.append("[[source]]")
        for k, v in s.items():
            lines.append(f"{k} = {fmt(v)}")
        lines.append("")
    for d in result["detected_only"]:
        lines.append(t("# 検出のみ: {kind} @ {path}  ヒント: {hint}",
                       kind=d["kind"], path=d["path"], hint=d["hint"]))
    return "\n".join(lines) + "\n"
