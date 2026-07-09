"""tamo CLI — `python -m tamo.cli <cmd>` または `tamo <cmd>`。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import adapters
from .config import inbox_token, load_sources, sources_path
from .optimize import build_pack
from .probe import dump_sources_toml, run_probe
from .store import Store
from .util import tamo_home


def _store() -> Store:
    return Store(tamo_home())


def _reindex() -> dict:
    store = _store()
    try:
        return store.reindex_blob_texts()
    finally:
        store.close()


# ---------------------------------------------------------------- collect

def do_collect(rescan: list[str] | None = None, only: list[str] | None = None, quiet: bool = False) -> dict:
    adapters.load_all()
    store = _store()
    lock = tamo_home() / ".lock"
    try:
        fd = lock.open("x")
    except FileExistsError:
        print(f"別のtamoが実行中のようです（{lock} を確認）", file=sys.stderr)
        sys.exit(2)
    totals = {"raw_new": 0, "events_new": 0, "quarantined": 0}
    try:
        for cfg in load_sources():
            kind = cfg.get("kind")
            if only and kind not in only and cfg.get("key") not in only:
                continue
            cls = adapters.REGISTRY.get(kind)
            if not cls:
                print(f"  ! 未知のkind: {kind}（スキップ）", file=sys.stderr)
                continue
            ad = cls(cfg)
            cursor = {} if (rescan and (kind in rescan or ad.key in rescan)) else store.get_cursor(ad.key)
            t0 = time.time()
            new_cursor, items = ad.collect(cursor)
            n_raw = n_ev = n_q = 0
            for it in items:
                if it["error"] is not None:
                    store.put_quarantine(kind, it["locator"], it["payload"], it["error"])
                    n_q += 1
                    continue
                raw_id, created = store.put_raw(kind, it["locator"], it["payload"])
                if created:
                    n_raw += 1
                for ev in it["events"]:
                    if store.upsert_event(ev, raw_id):
                        n_ev += 1
            store.set_cursor(ad.key, new_cursor)
            store.commit()
            totals["raw_new"] += n_raw
            totals["events_new"] += n_ev
            totals["quarantined"] += n_q
            if not quiet:
                print(f"  {ad.key:<24} raw+{n_raw:<5} events+{n_ev:<5} quarantine+{n_q:<3} ({time.time() - t0:.2f}s)")
    finally:
        fd.close()
        lock.unlink(missing_ok=True)
        store.close()
    return totals


# ------------------------------------------------------------------- cmds

def cmd_probe(args):
    extra = [Path(p) for p in (args.scan or [])]
    res = run_probe(Path(args.home).expanduser(), extra)
    print("== tamo probe ==")
    for n in res["notes"]:
        print("  •", n)
    if res["detected_only"]:
        print("-- 検出のみ（アダプタ設定が必要） --")
        for d in res["detected_only"]:
            print(f"  ? {d['kind']:<14} {d['path']}\n      ヒント: {d['hint']}")
    toml = dump_sources_toml(res)
    if args.write:
        sources_path().write_text(toml)
        print(f"-> {sources_path()} に {len(res['sources'])} ソースを書き込みました")
    else:
        print("-- sources.toml（--write で保存） --")
        print(toml)


def cmd_collect(args):
    t = do_collect(rescan=args.rescan, only=args.only)
    print(f"合計: raw+{t['raw_new']} events+{t['events_new']} quarantine+{t['quarantined']}")


def cmd_watch(args):
    if args.http:
        from .http_inbox import start_background

        start_background(args.port)
        print(f"HTTP inbox: http://127.0.0.1:{args.port}/inbox  (X-Tamo-Token: {inbox_token()})")
    if not args.once:
        print(f"{args.interval}s 間隔でポーリング収集します（WSL2の/mnt/c配下はinotifyが効かないためポーリングが正解）")
    while True:
        totals = None
        try:
            totals = do_collect(quiet=True)
        except SystemExit:
            pass
        if totals and totals.get("events_new"):
            # 収集器は無人運転が前提: 新イベントがあれば導出ルールも自動再生成する
            # （決定論+出所ID+マーカー冪等なので人手レビューのゲートは置かない。HITLは下流OmniBrainの責務）
            _rules_refresh(args.rules, args.rules_project)
        try:
            _maybe_autoprune()
        except Exception as e:  # noqa: BLE001
            print(f"[tamo] prune error: {e}", file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


def cmd_stats(args):
    store = _store()
    s = store.stats()
    store.close()
    print(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_sessions(args):
    store = _store()
    for s in store.list_sessions(args.limit):
        print(f"{s['last_ts'] or '-':<20} {s['n_events']:>4}ev  {s['session_key']:<40} {s['title'] or ''}")
    store.close()


def cmd_search(args):
    store = _store()
    for r in store.search(args.query, args.limit, source=getattr(args, "source", None)):
        print(f"[{r['ts'] or '-'}] {r['actor']:<9} {r['session_key']}\n    {r['snippet']}\n    e:{r['event_id']}")
    store.close()


def cmd_pack(args):
    store = _store()
    if args.session:
        events = store.iter_session_events(args.session)
        title = f"tamo pack: {args.session}"
    else:
        events = store.recent_events(days=args.days)
        title = f"tamo pack: 直近{args.days}日"
    md, stats = build_pack(events, budget_tokens=args.budget, query=args.query or "", title=title)
    store.close()
    if args.out:
        Path(args.out).write_text(md)
        print(f"-> {args.out} ({stats['used_tokens']}tok)", file=sys.stderr)
    else:
        print(md)


def cmd_export(args):
    store = _store()
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    n = 0
    try:
        if args.format == "ndjson":
            rows = store.con.execute("SELECT ces FROM events ORDER BY session_key, seq")
            for (ces,) in rows:
                out.write(ces + "\n")
                n += 1
        else:  # omnibrain: 1行=1セッション（生会話参照つき）
            for s in store.list_sessions(limit=100000):
                events = store.iter_session_events(s["session_key"])
                line = {
                    "schema": "tamo.session.v1",
                    "session_key": s["session_key"],
                    "source_kind": s["source_kind"],
                    "title": s["title"],
                    "first_ts": s["first_ts"], "last_ts": s["last_ts"],
                    "events": events,
                }
                if args.include_raw:  # ADR-024: 下流(OmniBrain)でも原文を失わない
                    rows = store.con.execute(
                        "SELECT DISTINCT r.locator, r.sha256, r.payload FROM raw_records r "
                        "JOIN events e ON e.raw_id = r.id WHERE e.session_key=?",
                        (s["session_key"],))
                    line["raw"] = [{"locator": lc, "sha256": sh,
                                    "payload": pl.decode("utf-8", "replace")}
                                   for lc, sh, pl in rows]
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
                n += 1
    finally:
        if args.out:
            out.close()
        store.close()
    print(f"exported {n} lines ({args.format})", file=sys.stderr)


_HOOK_SNIPPET = """\
以下を ~/.claude/settings.json にマージしてください（jsonのhooksキー）:
{
  "hooks": {
    "Stop":       [ { "hooks": [ { "type": "command", "command": "%(cmd)s", "async": true, "timeout": 30 } ] } ],
    "SessionEnd": [ { "hooks": [ { "type": "command", "command": "%(cmd)s", "async": true, "timeout": 30 } ] } ]
  }
}
- Stop はターン毎（準リアルタイム取込）、SessionEnd はセッション終了時の確定取込。
- async: true なのでClaude Codeをブロックしません。
- フックはstdinで session_id / transcript_path / cwd を受け取り、該当transcriptだけ即時取込します。
- なお Codex CLI (v0.5x以降) と Cursor (v1.7以降) にも同型のフック機構があるため、
  同じ `tamo ingest-hook` を流用できます（transcript_pathが無い場合は全体collectにフォールバック）。
"""


def cmd_mirror(args):
    from .derive import mirror_sessions

    store = _store()
    try:
        r = mirror_sessions(store, Path(args.out), project=args.project, redact=args.redact)
    finally:
        store.close()
    print(f"mirrored {r['written']} sessions -> {r['dir']}", file=sys.stderr)
    if r["redacted"]:
        print(f"  redacted: {json.dumps(r['redacted'], ensure_ascii=False)}", file=sys.stderr)
    for f in r["files"]:
        print(f"  {f}", file=sys.stderr)


def cmd_rules(args):
    from .derive import derive_rules, write_rules_into

    store = _store()
    try:
        md = derive_rules(store, project=args.project, days=args.days,
                          per_section=args.per_section)
    finally:
        store.close()
    if not md:
        print("抽出できる決定/制約/エラー対処が見つかりませんでした", file=sys.stderr)
        return
    if args.write:
        action = write_rules_into(Path(args.write), md)
        print(f"rules {action}: {args.write}", file=sys.stderr)
    else:
        print(md, end="")


def cmd_run(args):
    """エージェントをそのまま実行し、終了後に増分収集する薄いラッパー。
    フックが無いエージェント(codex/aider等)でも「1コマンド差し替え」で取込が回る。"""
    import subprocess

    cmd = [c for c in (args.command or []) if c != "--"]
    if not cmd:
        print("usage: tamo run -- <command...>   (例: tamo run -- claude)", file=sys.stderr)
        sys.exit(2)
    try:
        rc = subprocess.call(cmd)
    except KeyboardInterrupt:
        rc = 130
    except FileNotFoundError:
        print(f"コマンドが見つかりません: {cmd[0]}", file=sys.stderr)
        sys.exit(127)
    ns = argparse.Namespace(only=args.only, rescan=None)
    cmd_collect(ns)
    sys.exit(rc)


def _rules_refresh(rules_file: str | None, project: str | None) -> None:
    """新イベントがあったサイクルで導出ルールを冪等再生成（無人運転前提・レビューゲート無し）。"""
    if not rules_file:
        return
    from .derive import derive_rules, write_rules_into

    store = _store()
    try:
        md = derive_rules(store, project=project)
    finally:
        store.close()
    if md:
        action = write_rules_into(Path(rules_file), md)
        print(f"[tamo] rules {action}: {rules_file}", file=sys.stderr)


def _maybe_autoprune() -> None:
    """retention.days>0 のとき1日1回だけpruneを走らせる（serve/watchの無人運転用）。"""
    from datetime import date

    from .config import load_settings

    days = load_settings()["retention"]["days"]
    if not days:
        return
    marker = tamo_home() / ".last_prune"
    today = date.today().isoformat()
    if marker.exists() and marker.read_text().strip() == today:
        return
    store = _store()
    try:
        r = store.prune(days)
    finally:
        store.close()
    marker.write_text(today)
    if r["events"] or r["raw_records"] or r["quarantine"]:
        print(f"[tamo] auto-prune(>{days}d): events-{r['events']} raw-{r['raw_records']} "
              f"quarantine-{r['quarantine']} blobs_gc-{r['blobs_gc']}", file=sys.stderr)


def cmd_serve(args):
    """収集 + HTTP inbox + MCP(streamable-http) + 自動prune を1プロセスで動かす常駐サービス。
    OmniBrain等の下流が無くても、これ単体で「貯める・探す・渡す」が完結する。"""
    import threading

    from .config import ensure_settings_file, load_settings

    ensure_settings_file()
    s = load_settings()["serve"]
    interval = args.interval or s["interval"]
    mcp_port = args.mcp_port or s["mcp_port"]
    inbox_port = args.inbox_port or s["inbox_port"]

    if not args.no_inbox:
        from .http_inbox import start_background

        start_background(inbox_port)
        print(f"inbox : http://127.0.0.1:{inbox_port}/inbox  (X-Tamo-Token: {inbox_token()})")
    print(f"mcp   : http://127.0.0.1:{mcp_port}/mcp  (streamable-http)")
    print(f"collect: {interval}s間隔でポーリング（ソース側の自動削除より先に掬うのが仕事）")
    print("登録例:")
    print(f"  claude mcp add --transport http tamo http://127.0.0.1:{mcp_port}/mcp")
    print(f"  # stdio派: claude mcp add tamo -- tamo mcp")

    def loop() -> None:
        while True:
            totals = None
            try:
                totals = do_collect(quiet=True)
            except SystemExit:
                pass
            except Exception as e:  # noqa: BLE001  収集失敗でサービス全体は止めない
                print(f"[tamo] collect error: {e}", file=sys.stderr)
            if totals and totals.get("events_new"):
                _rules_refresh(args.rules, args.rules_project)
            try:
                _maybe_autoprune()
            except Exception as e:  # noqa: BLE001
                print(f"[tamo] prune error: {e}", file=sys.stderr)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="tamo-collector").start()
    from . import mcp_server

    mcp_server.run("streamable-http", "127.0.0.1", mcp_port)  # blocking


def cmd_mcp(args):
    from . import mcp_server

    mcp_server.run("streamable-http" if args.http else "stdio", args.host, args.port)


def cmd_prune(args):
    from .config import ensure_settings_file, load_settings

    ensure_settings_file()
    days = args.days if args.days is not None else load_settings()["retention"]["days"]
    if not days or days <= 0:
        print("保持日数が未設定です: --days N を指定するか settings.toml の [retention] days を設定してください"
              "（0 = 無期限 = 何も消しません）", file=sys.stderr)
        sys.exit(2)
    store = _store()
    try:
        r = store.prune(days, dry_run=args.dry_run)
        if args.vacuum and not args.dry_run:
            store.con.execute("VACUUM")
    finally:
        store.close()
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_purge(args):
    if not args.yes:
        print("全データ(DB/CAS/処理済みinbox)を削除します。実行するには --yes を付けてください。", file=sys.stderr)
        sys.exit(2)
    import shutil

    home = tamo_home()
    removed = []
    for name in ("tamo.db", "tamo.db-wal", "tamo.db-shm", ".last_prune"):
        p = home / name
        if p.exists():
            p.unlink()
            removed.append(name)
    for d in ("cas", "inbox/done"):
        p = home / d
        if p.exists():
            shutil.rmtree(p)
            removed.append(d + "/")
    print(f"purged: {', '.join(removed) or '(何もありませんでした)'}"
          "（sources.toml / settings.toml / inbox.token / 未処理inboxは残しています）")


def _to_clipboard(text: str) -> str | None:
    """クリップボードへコピー。WSLではclip.exe(UTF-16LE+BOM)で日本語も化けない。"""
    import shutil as _sh
    import subprocess as _sp

    if _sh.which("clip.exe"):
        _sp.run(["clip.exe"], input=b"\xff\xfe" + text.encode("utf-16-le"), check=True)
        return "clip.exe"
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if _sh.which(cmd[0]):
            _sp.run(cmd, input=text.encode(), check=True)
            return cmd[0]
    return None


def cmd_recall(args):
    from .recall import recall

    store = _store()
    try:
        md = recall(store, args.query, budget_tokens=args.budget, max_hits=args.hits,
                    source=args.source)
    finally:
        store.close()
    print(md)
    if args.copy:
        method = _to_clipboard(md)
        if method:
            print(f"[tamo] クリップボードにコピーしました ({method}) — そのままブラウザAIに貼れます", file=sys.stderr)
        else:
            print("[tamo] クリップボードツールが見つかりません (clip.exe/pbcopy/wl-copy/xclip)", file=sys.stderr)


def cmd_show(args):
    from .schema import blocks_text
    from .util import strip_noise, truncate

    store = _store()
    try:
        key = args.session
        if key == "latest" or key.startswith("latest:"):
            src = key.split(":", 1)[1] if ":" in key else None
            key = store.latest_session_key(src)
            if not key:
                print("セッションがありません", file=sys.stderr)
                sys.exit(1)
        evs = store.iter_session_events(key, since_event_id=args.since_event,
                                        since_ts=args.since_ts, tail=args.tail)
    finally:
        store.close()
    if args.json:
        print(json.dumps(evs, ensure_ascii=False, indent=1))
        return
    print(f"# {key}  ({len(evs)}件)", file=sys.stderr)
    for e in evs:
        text = strip_noise(blocks_text(e.get("content", [])))
        head = f"[{e.get('ts') or '-'}] {e.get('actor'):<9}"
        if e.get("kind") not in (None, "message"):
            head += f" ({e['kind']})"
        print(f"{head} ⟨e:{e['event_id'][:8]}⟩\n    {truncate(text, 300)}")


def cmd_hook(args):
    print(_HOOK_SNIPPET % {"cmd": args.command})
    print(_CLAUDE_MD_SNIPPET)


_CLAUDE_MD_SNIPPET = """
# ---- おまけ: CLAUDE.md に貼るとClaudeがtamoを能動的に使うようになるスニペット ----
## tamo (cross-agent context)
`tamo` MCPが使えるとき:
- 「あの件どうなってた?」「前に決めたこと」と聞かれたら、まず **`recall(query="話題の語")` を1回だけ**呼ぶ
  （検索+前後の顛末+添付根拠を合成済みのMarkdownが返る。追加のツール往復は原則不要）
- 「Geminiで聞いてた」「Claude(Web)で」のような**面指定**があれば recall(query, source="gemini") のように渡す
- recallで足りない時だけ: `get_session(session_key, since_event_id=...)` で続き、`get_blob_text(sha)` で添付全文
- セッション開始時は `get_context_pack(query=...)` で要点パックを引くこと
- 会話中の `[添付(... ) ... sha=xxxx]` 参照の中身は `get_blob_text(sha256)` で読めること
- 導出ルール（この下の tamo:rules 区間）は `tamo rules --write` / `tamo watch --rules` が自動更新すること

# ---- おまけ2: /recall スラッシュコマンド（~/.claude/commands/recall.md として保存） ----
---
description: tamoで過去の全エージェント会話を一発調査
---
tamo MCPの `recall` ツールを query="$ARGUMENTS" で1回呼び、返ってきたMarkdown（★=一致行、
📎=添付根拠、⟨e:xxxx⟩=出所ID）だけを根拠に日本語で簡潔に答えてください。追加のツール往復は不要です。
"""


def cmd_ingest_hook(args):
    """エージェントのフックから呼ばれる: stdinのJSONを読み、対象transcriptのみ即時取込。"""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:  # noqa: BLE001
        payload = {}
    tp = payload.get("transcript_path") or payload.get("transcriptPath")
    store = _store()
    try:
        if tp and Path(tp).exists():
            from .adapters.claude_code import collect_file

            ckey = f"hookfile:{tp}"
            cur = store.get_cursor(ckey)
            new_cur, items = collect_file(Path(tp), cur)
            for it in items:
                if it["error"] is not None:
                    store.put_quarantine("claude_code", it["locator"], it["payload"], it["error"])
                    continue
                raw_id, _ = store.put_raw("claude_code", it["locator"], it["payload"])
                for ev in it["events"]:
                    store.upsert_event(ev, raw_id)
            store.set_cursor(ckey, new_cur)
            store.commit()
        else:
            store.close()
            do_collect(quiet=True)
            return
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
    sys.exit(0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tamo", description="tamo — AIエージェント横断のコンテキスト収集器（タモ網）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="環境を走査してソースを自動検出")
    p.add_argument("--home", default=str(Path.home()))
    p.add_argument("--scan", nargs="*", help="aider等を探す追加ディレクトリ")
    p.add_argument("--write", action="store_true", help="sources.tomlに書き込む")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("collect", help="全ソースを増分収集")
    p.add_argument("--rescan", nargs="*", help="カーソルを無視して全再走査するkind/key")
    p.add_argument("--only", nargs="*", help="このkind/keyのみ収集")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("watch", help="常駐（ポーリング収集 + 任意でHTTP inbox + 導出ルール自動還流）")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--http", action="store_true")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--once", action="store_true", help="1サイクルだけ実行して終了（cron向け）")
    p.add_argument("--rules", metavar="FILE", help="新イベント収集のたびに導出ルールをこのファイルへ冪等更新（例: CLAUDE.md）")
    p.add_argument("--rules-project", help="rules対象の部分一致フィルタ")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("stats", help="統計")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("sessions", help="セッション一覧")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("search", help="全文検索（FTS5 trigram: 日本語部分一致OK）")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--source", help="面の絞り込み（source_kind部分一致）")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("pack", help="トークン予算内の引き継ぎパック(Markdown)を生成")
    p.add_argument("--budget", type=int, default=6000)
    p.add_argument("--query", default="")
    p.add_argument("--session", help="特定session_keyのみ")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("export", help="NDJSONエクスポート（OmniBrain等の下流へ）")
    p.add_argument("--format", choices=["ndjson", "omnibrain"], default="omnibrain")
    p.add_argument("--include-raw", action="store_true")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("reindex-blobs", help="添付テキストを再抽出してFTSへ遡及登録（抽出器更新後/旧DB移行用）")
    p.set_defaults(fn=lambda a: print(json.dumps(_reindex(), ensure_ascii=False)))

    p = sub.add_parser("mirror", help="セッションをgitコミット可能なMarkdownとしてプロジェクトへミラー")
    p.add_argument("--out", default=".tamo/history", help="出力先ディレクトリ（既定 ./.tamo/history）")
    p.add_argument("--project", help="session_key/タイトル/locatorへの部分一致フィルタ")
    p.add_argument("--redact", action="store_true", help="APIキー等の秘密情報をマスクしてから書く")
    p.set_defaults(fn=cmd_mirror)

    p = sub.add_parser("rules", help="履歴から導出ルール(決定/制約/エラー対処)を規則ベース抽出しCLAUDE.md等へ還流")
    p.add_argument("--project", help="session_key/タイトル/locatorへの部分一致フィルタ")
    p.add_argument("--days", type=int, help="直近N日のイベントのみ対象")
    p.add_argument("--per-section", type=int, default=20)
    p.add_argument("--write", metavar="FILE", help="マーカー区間を冪等更新して書き込む（例: CLAUDE.md）")
    p.set_defaults(fn=cmd_rules)

    p = sub.add_parser("run", help="エージェントCLIをそのまま実行し、終了時に増分収集（例: tamo run -- claude）")
    p.add_argument("--only", help="終了時に収集するソースkind（既定は全ソース）")
    p.add_argument("command", nargs=argparse.REMAINDER, help="実行するコマンド（-- の後に書く）")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("serve", help="単体サービス起動: 収集 + HTTP inbox + MCP(streamable-http) + 自動prune を1プロセスで")
    p.add_argument("--interval", type=int, help="収集ポーリング間隔(秒)。既定はsettings.toml")
    p.add_argument("--mcp-port", type=int, help="MCPポート（既定8788）")
    p.add_argument("--inbox-port", type=int, help="inboxポート（既定8787）")
    p.add_argument("--no-inbox", action="store_true", help="ブラウザ投函口を起動しない")
    p.add_argument("--rules", metavar="FILE", help="新イベント収集のたびに導出ルールを冪等更新")
    p.add_argument("--rules-project", help="rules対象の部分一致フィルタ")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="MCPサーバ単体起動（既定stdio。`claude mcp add tamo -- tamo mcp`）")
    p.add_argument("--http", action="store_true", help="streamable-httpで常駐")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("prune", help="保持期間を超えた古いデータを削除（活動時刻基準・mtime不使用）")
    p.add_argument("--days", type=int, help="保持日数（省略時はsettings.tomlのretention.days）")
    p.add_argument("--dry-run", action="store_true", help="削除せず件数だけ表示")
    p.add_argument("--vacuum", action="store_true", help="削除後にVACUUMでDBを詰める")
    p.set_defaults(fn=cmd_prune)

    p = sub.add_parser("purge", help="全データ削除（DB/CAS/処理済みinbox）。設定とトークンは残す")
    p.add_argument("--yes", action="store_true", help="確認なしで実行")
    p.set_defaults(fn=cmd_purge)

    p = sub.add_parser("recall", help="「あの件どうなってた？」一発調査（検索+前後の顛末+添付根拠をMarkdown合成）")
    p.add_argument("query")
    p.add_argument("--budget", type=int, default=3500)
    p.add_argument("--hits", type=int, default=4)
    p.add_argument("--source", help='面の絞り込み（部分一致: gemini / chatgpt / claude_web / claude_code / cursor…）')
    p.add_argument("--copy", action="store_true", help="結果をクリップボードへ（WSLはclip.exe/UTF-16LE対応）")
    p.set_defaults(fn=cmd_recall)

    p = sub.add_parser("show", help="1セッションを表示（latest可・--tail/--since-eventで途中から）")
    p.add_argument("session", help="session_key、または latest / latest:<source_kind>")
    p.add_argument("--tail", type=int, help="末尾N件だけ")
    p.add_argument("--since-event", help="このevent_id（8桁短縮可）の次から")
    p.add_argument("--since-ts", help="このISO時刻より後だけ")
    p.add_argument("--json", action="store_true", help="CES JSONで出力")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("token", help="ブラウザ拡張用のinboxトークンを表示（無ければ生成）")
    p.set_defaults(fn=lambda a: print(inbox_token()))

    p = sub.add_parser("hook", help="Claude Code等のフック設定スニペットを表示")
    p.add_argument("--command", default="tamo ingest-hook")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("ingest-hook", help="(フックから呼ばれる) stdinのJSONで対象transcriptを即時取込")
    p.set_defaults(fn=cmd_ingest_hook)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:  # serve/watch等のCtrl+C停止はトレースバックを出さず静かに終了
        print("\n[tamo] 停止しました (Ctrl+C)", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:  # `tamo search ... | head` 等でパイプが先に閉じた場合は正常終了
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
