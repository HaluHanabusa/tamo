# tamo tutorial — from zero to cross-agent recall in 15 minutes

[日本語版](ja/TUTORIAL.md)

This walkthrough takes you from installation to the point where any of your AI
agents can answer *"what did we decide about X?"* from conversations that
happened in a **different** tool.

Prerequisites: Python 3.11+, `pip`, and (optionally) Chrome for the browser part.
Works on Windows (native or WSL2), macOS, and Linux.

---

## 0. No AI history yet? Use the sandbox

Every step below works against a **generated demo environment**, so you can try
tamo without exposing your real data:

```bash
python tests/make_fixtures.py /tmp/demo/home /tmp/demo/state
export TAMO_HOME=/tmp/demo/state          # PowerShell: $env:TAMO_HOME="..."
tamo probe --home /tmp/demo/home --write  # instead of plain `tamo probe --write`
```

This creates fake Claude Code / Cursor / Codex CLI / aider / browser data.
When you are done playing, `unset TAMO_HOME` and start over with your real home.

## 1. Install and first harvest

```bash
git clone https://github.com/HaluHanabusa/tamo && cd tamo
pip install -e ".[mcp]"     # collection itself is dependency-free; [mcp] adds the serving side
tamo probe --write          # scan your machine, write ~/.tamo/sources.toml
tamo collect                # incremental, idempotent harvest of every source
tamo stats
```

`probe` reports what it found (Claude Code transcripts, Cursor DB, …) and what it
only *detected* (tools that need a config entry). `collect` backfills everything
that exists on disk, then only reads increments. Run it twice — the second run
adds `+0`, because every event has a deterministic ID.

Expected shape of `tamo stats`:

```json
{ "events": 1234, "sessions": 42, "quarantine": 0,
  "per_source": { "claude_code": 900, "cursor_ide": 300, "...": 34 } }
```

> `quarantine` is where unparseable lines go — kept **with their raw bytes**,
> never dropped. If it grows after a tool update, run `tamo quarantine`
> to see what changed.

## 2. Search and recall

```bash
tamo search "snapshot"              # full-text, CJK substring match included
tamo search "トルク" --source cursor  # restrict to one tool
tamo recall "what did we decide about the collector"
```

`search` returns hit lines with `e:<id>` source pointers. `recall` is the
one-shot version: it finds the sessions, stitches *match + key points + how it
ended* into a single Markdown digest, and marks every line with `⟨e:xxxx⟩` so
you can always trace back to the original (`tamo show <session>` /
`--since-event`).

`tamo recall ... --copy` puts the digest on your clipboard — paste it into any
chat.

## 3. Build a handoff pack

```bash
tamo pack --budget 6000 --query "collector design" --out pack.md
```

This runs the deterministic 4-stage optimizer (dedup → snapshot folding → key
point extraction → token-budget selection) and produces a Markdown pack that
fits in the token budget you asked for. Paste it at the top of a fresh session
in any agent.

## 4. Run it as a service (recommended)

```bash
tamo serve
```

One process = periodic collection + browser inbox + MCP server + daily prune.
The startup banner prints a ready-to-paste registration command:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: <your token>"
# stdio variant (Cursor / Codex CLI use the same form, no token needed):
claude mcp add tamo -- tamo mcp
```

Now ask Claude Code: *"あの件どうなってた？"* / *"check tamo for what we decided
about X"* — it will call the `recall` tool. Add the snippet from `tamo hook` to
your `CLAUDE.md` to make agents reach for tamo proactively, and use the printed
hooks config for real-time per-turn ingestion.

To keep it running: `systemd --user` on Linux/WSL2, or one Task Scheduler entry
(`tamo serve`) on Windows.

## 5. Scoop browser conversations

1. `chrome://extensions` → enable Developer mode → *Load unpacked* →
   select `browser-extension/`
2. With `tamo serve` running, open the extension popup once — it **pairs
   automatically** (if the token ever drifts, press *Re-pair*)
3. On claude.ai / ChatGPT / Gemini / any chat site, click the 🎣 button
   (top-right) or use the popup's scoop button

The popup also has the reverse direction: type a query in *search & copy*, and
paste your past context into the web chat.

## 6. Feed history back into your repo

```bash
tamo mirror --project myapp        # sessions → ./.tamo/history/*.md (secrets masked by default)
tamo rules --write CLAUDE.md       # decisions/constraints/error-fixes → CLAUDE.md marker block
```

`mirror` output is committable and reviewable in PRs; masking is on by default
(`--no-redact` to opt out). `rules` rewrites only its own marker block, so your
hand-written CLAUDE.md content is safe.

## 7. Housekeeping

```bash
tamo stats                   # size, oldest event, quarantine count
tamo prune --days 90 --dry-run   # see what would be deleted (activity-time based)
tamo prune --days 90             # asks for confirmation before deleting
tamo quarantine              # inspect unparseable lines (a growth = format drift)
tamo purge --yes             # nuke all data (keeps config and token)
```

Default retention is **forever** — tamo exists to outlive the sources' own
cleanup. See the retention risk notes in
[ARCHITECTURE.md](ARCHITECTURE.md) before deciding what is right for you;
for work machines we recommend setting `[retention] days = 90` (or your
company's policy) in `~/.tamo/settings.toml`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `port 8787/8788 is in use` | another tamo is running, or change `--inbox-port` / `--mcp-port` |
| extension shows `403` | press **Re-pair** in the popup |
| `another tamo is running (PID …)` | a live process holds the lock; stale locks are reclaimed automatically |
| search misses attachment text | `pip install pypdf` then `tamo reindex-blobs` |
| `quarantine` count rising | `tamo quarantine` + `tamo probe` fingerprint, then file an issue |
