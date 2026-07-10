# tamo 🎣 — cross-agent context harvester

English | [日本語](docs/ja/README.md)

Your AI conversations are scattered across Claude Code, Cursor, Codex CLI, aider,
and browser chats (claude.ai / ChatGPT / Gemini…). tamo **harvests them into one
local database, makes them searchable, and hands them to whichever agent you use
next.**

- **Save it before it's gone** — every tool deletes its own logs on its own terms
  (Claude Code defaults to 30 days). tamo is the receptacle that outlives them
  (default retention: forever)
- **Search everything from one place** — full-text search with CJK substring
  matching, including the *contents of attachments* (PDF / Word / Excel / text)
- **Hand it off as-is** — serve history to any agent over MCP, or paste a
  token-budgeted Markdown pack
- **Fully local, zero dependencies** — stdlib-only, deterministic (no LLM in the
  collection path). Your data never leaves your machine

New here? Follow the **[15-minute tutorial](docs/TUTORIAL.md)** — it even ships a
sandbox so you can try everything without touching real data.

## Install — first 5 minutes

```bash
pip install -e .            # collection is dependency-free; add ".[mcp]" for the serving side
tamo probe --write          # scan your machine → generate ~/.tamo/sources.toml
tamo collect                # incremental, idempotent harvest of every source
tamo stats                  # see what came in
tamo search "snapshot"      # full-text search (CJK substring match works too)
```

Runs on Windows (native or WSL2), macOS, and Linux. tamo writes only under
`~/.tamo` (configurable via `TAMO_HOME`) and **never writes to the sources**.

## Everyday commands

| What you want | Command |
|---|---|
| "What did we decide about X?" in one shot | `tamo recall "deck cover interlock"` (`--copy` → clipboard) |
| Full-text search (incl. attachments) | `tamo search "term1 term2" --source gemini` |
| Peek at the latest session | `tamo show latest --tail 5` |
| Build a handoff pack | `tamo pack --budget 6000 --query "design" --out pack.md` |
| Mirror history into your repo | `tamo mirror --project myapp` (secrets masked by default) |
| Feed decisions/constraints into CLAUDE.md | `tamo rules --write CLAUDE.md` |
| Inspect unparseable lines | `tamo quarantine` (growth = source format drift) |

## Run it as a service — `tamo serve` (recommended)

One command starts periodic collection + the browser inbox + an MCP server +
daily prune:

```bash
tamo serve
```

Registration is a copy-paste from the startup banner:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: $(tamo token)"
# stdio variant (same for Cursor / Codex CLI; no token needed): claude mcp add tamo -- tamo mcp
```

Then just ask your agent *"what did we decide about X?"* — it will call tamo's
`recall` tool. Paste the snippet from `tamo hook` into `CLAUDE.md` to make agents
reach for tamo proactively. For per-turn real-time ingestion use the hooks config
that `tamo hook` prints; for CLIs without hooks, wrap them: `tamo run -- <cmd>`.

To keep it running: `systemd --user` (Linux/WSL2) or one Task Scheduler entry
(Windows).

## Scoop browser conversations — bundled extension "tamo scoop"

1. `chrome://extensions` → Developer mode → *Load unpacked* → `browser-extension/`
2. With `tamo serve` running, open the popup once — it pairs automatically
3. Click the 🎣 button (top right) on claude.ai / ChatGPT / Gemini / any chat site

It works in reverse too: *search & copy* in the popup fetches past context to
paste into the web chat. Details and troubleshooting:
[browser-extension/README.md](browser-extension/README.md).

## Where your data lives — retention and deletion

- Everything is in `~/.tamo/tamo.db` (one SQLite file) plus `~/.tamo/cas/`
  (attachments). Backup = copy the folder
- Default retention is **forever**; tamo warns once a day when total size passes
  a threshold (`[retention] warn_db_mb`, default 2048). Set
  `[retention] days = 90` in `~/.tamo/settings.toml` for auto-prune
- Manual: `tamo prune --days N` (confirmation + `--dry-run`), full wipe:
  `tamo purge --yes`
- **Read the [retention risk notes](docs/ARCHITECTURE.md) before choosing** —
  infinite retention of plaintext conversations is a real security/compliance
  trade-off (recommended: `days = 90` on work machines, keep `TAMO_HOME` out of
  cloud-synced folders, rely on full-disk encryption)

## Supported sources

| Source | What tamo reads |
|---|---|
| Claude Code (CLI / VS Code / JetBrains / Desktop) | `~/.claude/projects/**/*.jsonl` |
| Cursor | `state.vscdb` (snapshot-copied before reading) |
| Codex CLI | `~/.codex/sessions/**/*.jsonl` |
| aider | `.aider.chat.history.md` |
| Browser (claude.ai / ChatGPT / Gemini / any site) | bundled extension → HTTP inbox |
| Any JSONL-writing agent | `generic_jsonl` (a few lines in sources.toml) |

Windsurf / Cline / Copilot Chat and others are *detected and reported* by
`tamo probe` (adapters land once their real formats are fingerprinted).

## Honest limitations

- Log formats are undocumented and change without notice. tamo guarantees
  "never crash, never lose" (broken lines are quarantined with their raw bytes —
  see `tamo quarantine`), not "always parse everything"
- It cannot recover history that the source deleted before you installed tamo —
  run `tamo serve` early
- The database is plaintext; full-disk encryption (BitLocker / FileVault) is
  assumed

## Learn more

| Document | Contents |
|---|---|
| [docs/TUTORIAL.md](docs/TUTORIAL.md) | hands-on walkthrough, zero to cross-agent recall in 15 min ([日本語](docs/ja/TUTORIAL.md)) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | design principles, data model, optimizer, retention risks ([日本語](docs/ja/ARCHITECTURE.md)) |
| [docs/TECH_STACK.md](docs/TECH_STACK.md) | why each technology was chosen, file-by-file map ([日本語](docs/ja/TECH_STACK.md)) |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | on-machine verification steps ([日本語](docs/ja/VERIFICATION.md)) |
| [browser-extension/README.md](browser-extension/README.md) | extension details ([日本語](docs/ja/browser-extension.md)) |

MIT License
