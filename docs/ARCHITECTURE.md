# tamo architecture

English | [日本語](ARCHITECTURE.ja.md)

> **Which doc to read**: for usage, see the [README](../README.md); for why each
> technology was chosen and the file-by-file map, see [TECH_STACK.md](TECH_STACK.md);
> for on-machine verification steps, see [VERIFICATION.md](VERIFICATION.md).
> This document explains the thinking behind tamo — design principles, data model,
> optimization, feedback loop, and non-functional requirements.

## 1. Design principles

1. **Lossless ingestion, read-time optimization** — the raw source text is always
   stored in SQLite; summaries and compression are generated as "views" that can be
   rebuilt at any time. Improve an algorithm and it retroactively applies to all
   past data.
2. **No LLM in collection or storage** — deterministic event_id generation,
   rule-based key-point extraction, TF-IDF+MMR sentence selection: everything is
   deterministic (same input → same output). Semantic distillation is a downstream
   job (e.g. OmniBrain's HITL).
3. **Drift is assumed** — every vendor's log format is undocumented and unwarranted.
   Broken lines go to quarantine; unknown future schemas are kept as raw plus a meta
   event. tamo **never crashes and never loses data**.
4. **Attachments: metadata first** — even when the contents cannot be read, "what
   was attached (kind, name, size)" is always preserved in the body text. Text
   extraction is a bonus on top.

## 2. Overall architecture

```
[agents]                        [tamo]                                 [consumers]
Claude Code transcript ─┐   probe: auto-detection
Cursor state.vscdb ─────┤   collect: incremental, idempotent,      ┌→ MCP server (recall/search/pack/blob)
Codex CLI rollout ──────┼→    lossless                            ─┼→ tamo pack (paste-ready Markdown)
aider history.md ───────┤   CES normalization + CAS (blob dedup)   └→ export NDJSON (OmniBrain etc.)
browser (MV3 ext) ──────┘   SQLite + FTS5 (CJK trigram)
                            optimize: dedup / diff folding / key points
```

### Module layout

```
tamo/
  util.py        noise removal, token estimation, CJK tokenizer, media-kind vocabulary
  schema.py      CES v1 (canonical events), deterministic event_id
  cas.py         content-addressed storage, magic-number sniffing
  textract.py    attachment text extraction (OOXML/PDF/plain, deterministic)
  store.py       SQLite (WAL), FTS5 trigram, quarantine, prune, schema versioning
  probe.py       machine scan, fingerprinting, sources.toml generation
  optimize.py    P1–P4 (dedup / diff folding / key points / budget packing)
  recall.py      one-shot lookup (per-session digests)
  derive.py      mirror (git-friendly history) / rules (derived-rule feedback)
  redact.py      secret masking (on by default for mirror)
  config.py      settings.toml (retention and other NFRs) / inbox token
  http_inbox.py  127.0.0.1-only, token auth (/inbox /pair /recall /health)
  mcp_server.py  FastMCP (stdio / streamable-http with token auth)
  cli.py         probe/collect/serve/search/recall/show/pack/mirror/rules/prune/quarantine… 22 commands
  adapters/      claude_code / cursor_ide / codex_cli / aider / generic_jsonl / inbox
browser-extension/  MV3 extension "tamo scoop" (auto-pairing, persistent 🎣, search & copy)
```

## 3. Sessions and events (CES)

Utterances from every source are normalized into the Canonical Event Schema (CES v1):
`actor ∈ {user, assistant, tool, system}` × `kind ∈ {message, tool_use, tool_result, meta}`.

- **Unit**: `session_key = source:native-session-id`. Events are ordered by the
  original file order (`seq`) and time; `tamo sessions` / `list_sessions` sort by
  most recent activity
- **The heart of idempotency**: `event_id = sha256(source|session|native_id or
  locator|kind|content fingerprint)[:32]`. Timestamps are deliberately **excluded**,
  so time-format drift never mints a new ID and re-collecting is always safe
  (duplicates bounce off `INSERT OR IGNORE`)
- **Starting mid-stream**: cursors are byte offsets, so at install time tamo
  backfills every existing file in full and only reads increments afterwards.
  Transcripts that begin mid-conversation (already resumed or compacted) are
  ingested "as they are" (lossless = "everything on disk, nothing assumed")
- **Two time axes**: `session_key` (membership), `seq` (order within a session),
  `ts` (wall clock). DOM captures without a `ts` are placed on the timeline via the
  extension's `captured_at`
- **Fetching only the continuation**: `tamo show <key> --since-event <8 hex digits
  of ⟨e:xxxx⟩>` / the `since_event_id` parameter of MCP `get_session`. Unknown IDs
  return an empty result (fail-safe against accidentally sending everything)
- **Topics spanning multiple sessions**: `recall` lists per-session digests newest
  first and prepends a timeline summary such as
  「🕒 3セッションに跨る: 07-01 → 07-05 → 07-08」("spans 3 sessions", literal
  product output)

### Schema evolution

The schema version is stamped into `PRAGMA user_version`; older databases are
upgraded to the current version at connect time via the migration ledger
(`store._migrate`). Column additions are not picked up by
`CREATE TABLE IF NOT EXISTS`, so every future change must add its ALTER steps to
the ledger. If a newer database is opened by an older tamo, it fails with a clear
error asking for an upgrade (never breaks silently).

## 4. Attachments — CAS + deterministic text extraction

Pasted images and attachment base64 are siphoned into `cas/ab/cd/<sha256>.<ext>`
at ingestion time, and the conversation log side is replaced with a reference like
`[blob image/png 70B sha=c414cd0e204d]`.

- **Deduplication**: paste the same screenshot/PDF a hundred times, disk pays once
- **Atomic writes**: temp+rename; an existing file with a size mismatch (a past
  crashed half-write) is automatically rewritten — a store whose integrity is
  content-addressed must never keep corruption
- **MIME by magic number first**: even when the declared MIME lies, tamo inspects
  down to the ZIP internals behind the `PK` header to distinguish docx/xlsx/pptx.
  Video/audio are likewise magic-sniffed for mp4/mov/webm/wav/ogg/flac/mp3
- **Unreadable attachments still leave context**: the body always carries
  `[添付(動画) clip.mp4 video/mp4 76B sha=…]` (literal stored text: 添付 =
  attachment, 動画 = video) with kind, name and size, so you can search by kind
  word (video/image/PDF…) or filename
- **Text extraction (textract)**: docx/xlsx/pptx are read directly as ZIP+XML
  (zero dependencies); PDF is two-tiered — pypdf if available (handles Japanese
  ToUnicode CMaps), else a naive stdlib extractor. Text that fails the quality gate
  (readable-character ratio) is **discarded** rather than polluting the search
  index. Results are cached in `blob_texts` and indexed in the referencing event's
  FTS row (= `tamo search` hits the *contents* of attachments).
  `tamo reindex-blobs` re-extracts retroactively

## 5. Read-time optimization — `tamo pack`'s P1–P4

Storage is never touched; every read passes through four deterministic stages:

- **P1 dedup** — verbatim repeats of 120+ chars are collapsed into a reference
  (`⟨e:xxxx⟩と同一`, "same as ⟨e:xxxx⟩")
- **P2 snapshot folding** — successive tool_results for the same file are
  compressed into "final version in full + earlier versions as unified diffs"
- **P3 key-point extraction** — rule-based (Japanese & English): decisions /
  constraints / TODOs / error→fix pairs / files touched
- **P4 budget packing** — TF-IDF+MMR (λ=0.35) selects the conversation tail and
  formats Markdown that fits the requested token budget

Every line carries a `⟨e:xxxx⟩` provenance ID, so you can always trace back to the
original text in the `events` table.

## 6. Feedback — returning harvested history to the agents

The collect → distill → feed-back loop (in tamo, every stage is deterministic):

- **mirror**: mirrors sessions into `./.tamo/history/*.md` — committable to git,
  reviewable in PRs (the reasoning you did with the AI), greppable. The source of
  truth is tamo's DB; the mirror is a view fully regenerated on each run.
  **Secret masking is on by default** (`--no-redact` for the raw text; redact.py
  deterministically masks 20+ known key prefixes, key=value lines, and credentials
  embedded in URLs)
- **rules**: feeds P3-derived context (decisions, constraints, …) into CLAUDE.md
  and friends. Only the `<!-- tamo:rules:begin/end -->` marker block is updated,
  idempotently, so hand-written content is never clobbered. Being a collector,
  tamo places no human-review gate here — determinism, provenance IDs, and
  full regeneration on every run *are* the safety mechanism: a bad extraction is
  fixed by fixing the extraction rule and regenerating, not by editing the output
- **run**: a wrapper so that agents without hooks still get ingestion by swapping
  a single command

## 7. Real-time ingestion (hooks)

Claude Code's `Stop` / `SessionEnd` hooks pass `transcript_path` on stdin, so
`tamo ingest-hook` immediately does an incremental ingest of **that one file**
(`async: true` keeps the host tool unblocked). `tamo hook` prints the config
snippet. The same shape works for Codex CLI and Cursor hooks. Background coverage
is handled by the polling loop of `tamo serve` (or `tamo watch`). Under WSL2's
`/mnt/c`, inotify does not work, which is why watching is polling-based.

## 8. HTTP inbox — the drop box for browser conversations

`tamo serve` (or `tamo watch --http`) opens `127.0.0.1:8787/inbox`. The token
lives in `~/.tamo/inbox.token` (auto-generated on first run). From an MV3
extension or a userscript:

```js
fetch("http://127.0.0.1:8787/inbox", {
  method: "POST",
  headers: { "X-Tamo-Token": TOKEN, "Content-Type": "application/json" },
  body: JSON.stringify({
    schema: "tamo.inbox.v1",
    source: "chatgpt_web",            // originating surface (used for search --source filtering)
    session: "conv-abc123",           // conversation ID
    title: "optional title",
    note: "optional note (truncation / fallback reason etc.; preserved as a meta event)",
    messages: [
      { role: "user", text: "...", ts: "2026-07-07T09:00:00Z",
        attachments: [{ name: "memo.txt", mime: "text/plain", data_b64: "..." }] },
      { role: "assistant", text: "..." }
    ]
  })
});
```

- The server **only validates and writes to a file**. Parsing is funneled through
  the regular inbox adapter (the network never touches a parser directly =
  minimized attack surface)
- Attachments: with `data_b64` they go to the CAS; with only a `url` they become a
  URL note; even bare `{name, mime, size}` metadata is accepted and preserved in
  the body as `[添付(動画 未取得) … 50.0MB]` (literal stored text: "attachment
  (video, not fetched)")
- Inbox files move to `done/` **only after the DB commit succeeds** (a crash never
  loses data)

## 9. MCP — serving every agent

Eight tools: `recall` (one-shot lookup — start here) / `search_context` /
`get_context` (surrounding context) / `get_context_pack` / `list_sessions` /
`get_session` (resolves `latest`, continuation via `since_event_id`) /
`get_blob_text` / `get_blob_base64`.

- **stdio** (default): the client launches tamo as a child process = it *is* the
  local user, so no auth
- **streamable-http** (`tamo serve`): `X-Tamo-Token` (or `Authorization: Bearer`)
  required. Authenticating only writes (inbox) while letting reads (every
  harvested conversation plus raw blobs) pass would be an indefensible asymmetry.
  Binding to 127.0.0.1 means "not exposed to the LAN"; it is not authentication
- Responses are compact by default (bodies slimmed to 1,500 chars, a 60k-char
  guard on the whole response; overflow trims from the oldest side with an
  explicit `truncated:true`) — never crowding the caller's context window

## 10. Claude Code extensions for VS Code / Visual Studio

The CLI, the VS Code extension, the JetBrains extension, and Claude Desktop's
coding integration **all write JSONL transcripts to the same
`~/.claude/projects`** (`%USERPROFILE%\.claude\projects` on Windows), so the
`claude_code` adapter ingests them all as-is. probe also follows relocations via
`CLAUDE_CONFIG_DIR`. When `tamo probe` detects an installed extension, it notes
"no extra configuration needed".

## 11. OmniBrain integration (optional)

```bash
tamo export --format omnibrain --include-raw --out sessions.ndjson
```

One line = one session, as NDJSON (`tamo.session.v1`). `--include-raw` bundles the
raw records too, so the output can flow straight into a downstream chunked
distillation → HITL approval pipeline. The division of labor:
**tamo does deterministic collection, downstream does semantic distillation**
(export does not mask — it honors the lossless contract).

## 12. Non-functional requirements (NFR)

| Item | Policy |
|---|---|
| **Retention** | Default **forever** (`[retention] days = 0` in `settings.toml`). Sources are assumed to delete their own logs — tamo's reason to exist is being the receptacle that outlives them. Risks of infinite retention and operating guidance in §12.1; the daily size warning (`warn_db_mb`, default 2048MB) is the guard rail |
| **Deletion** | With `days>0`, `serve`/`watch` auto-prune daily. Judged by **event activity time** (file mtime is never used); events with unknown ts are kept (fail-safe); `--dry-run` and a confirmation prompt; deletions are always reported (never silent). Full wipe: `tamo purge --yes` |
| **Transparency** | `tamo stats` always shows `db_bytes` / `oldest_event` / quarantine count. `tamo quarantine` exposes the quarantined raw text |
| **Latency** | Measured on a 100k-event / 195MB DB: FTS search 0.04–16ms / tail fetch 0.1ms / continuation fetch 4ms / pack (5,000 ev) 0.42s / ingestion ~8,000 events/s |
| **Context economy** | `search` returns snippets, `pack` fits a token budget, `get_session` is compact by default. `compact=false` only when raw CES is needed |
| **Access control** | Both inbox and MCP (HTTP) bind to `127.0.0.1` + **X-Tamo-Token auth** (timing-safe comparison). `~/.tamo` is 0700, the token 0600 (POSIX; on Windows this relies on the user profile plus OS account separation) |
| **Concurrency** | Single writer via `.lock` (PID-recorded, stale locks auto-reclaimed). Reads (MCP/search) run concurrently thanks to WAL |
| **Encryption** | Delegated to OS full-disk encryption (BitLocker/FileVault). Since the data is plaintext, the defense is retention policy plus permissions |
| **Backup** | Copy `~/.tamo`, or `tamo export --include-raw` (NDJSON) |

### 12.1 Retention risks and operating guidance (forever is not right for everyone)

The default is infinite retention so tamo can "outlive the sources" — but let's be
explicit about **when that becomes a risk**.

**Measured scale** (2026-07, `tests/bench.py`): 100k events (text only) = 208MB
DB, ingestion 8,800 ev/s, search 0.03–12ms, pack (5,000 ev) 0.27s. Heavy use at
500 events/day means ~180k ev/year ≈ **400MB/year + attachment CAS**. In other
words, **performance and disk are not the near-term bottleneck**. The real risks
are elsewhere:

1. **Indefinite accumulation of secrets (most important)** — API keys, passwords,
   and customer data pasted into conversations are, by the lossless design, stored
   **in plaintext, forever** in the DB itself (redaction only applies to mirror
   output). The blast radius of a leak grows monotonically over time, and
   `~/.tamo` becomes the highest-value target an attacker could ask for:
   *every history of every AI tool, in one place*. Laptop theft, malware, account
   compromise, or handing back a work PC at departure is the moment it matters.
2. **Compliance** — on a work PC, personal data and confidential material
   inevitably enters via conversations. "Forever" can collide with GDPR / local
   data-protection storage-limitation principles and internal retention policies.
   If a policy exists, setting `days` to match it is the correct move.
3. **Location accidents** — putting `TAMO_HOME` inside a synced folder
   (OneDrive/Dropbox/…) not only makes an ever-growing DB eat sync bandwidth, it
   **uploads your supposedly local-only history to the cloud**. Keep it on a
   non-synced path.
4. **Far-future performance** — at millions of events, pack/recall's full-session
   reads and fragmentation without VACUUM start to bite. `tamo prune --vacuum`
   is the remedy.

**Guard rails already implemented**: daily disk-usage warning
(`[retention] warn_db_mb`, default 2048MB; surfaced by serve/watch/stats; 0
disables) / `prune` (activity-time based, dry-run, confirmation, reported
deletions) / `purge` / always-on visibility via `stats` / mirror's
default redaction.

**Recommended profiles**:

| Usage | Recommended setting |
|---|---|
| Personal dev machine (disk encrypted) | Keep the default (forever) + occasional `prune --vacuum` when warned |
| Work PC / team development | `[retention] days = 90` (or your company's policy) |
| Shared machine / no encryption | Not recommended (the plaintext-DB assumption breaks down) |

## 13. Honest limitations

- Every agent's log format is **undocumented and changes without notice**. tamo
  guarantees "never crash, never lose", not "always parse everything". When
  `tamo quarantine` grows, check the `tamo probe` fingerprint and fix the adapter
- Cursor is supported in both its old `composerData.conversation` array and new
  `bubbleId`-split forms, but an even newer internal format would need a follow-up
- On the browser side, claude.ai/ChatGPT use the same-origin-API approach and are
  robust to redesigns, but Gemini's DOM reading and the generic fallback can get
  shallower after UI overhauls (even then, whatever was captured plus the note is
  preserved)
- Windsurf / Cline / Copilot Chat are detection-only, pending real-machine
  fingerprints

## 14. UI language (i18n) design

What gets translated is drawn along one line: **UI feedback is localized;
deterministic artifacts are not.**

- **Localized** (English default, Japanese via locale/`TAMO_LANG`): stderr
  messages, confirmation prompts, `--help` texts, HTTP error bodies, startup
  banners, the settings.toml template comments, and the browser-extension UI
  (`_locales/`). Implementation: `tamo/i18n.py`, gettext-style with the
  **Japanese source string as the msgid** — untranslated keys degrade to
  Japanese (visible, never a crash), and `tests/test_i18n.py` AST-scans every
  `t()` call site to force dictionary coverage and placeholder parity.
  Document-shaped constants (hook snippets, settings template) live as
  language-paired constants instead of dictionary entries.
- **Not localized**: the bodies of `pack` / `recall` / `mirror` / `rules`
  (deterministic views — same input must give the same output on every machine),
  the stored-data vocabulary (`[添付(動画 未取得) …]` kind words, which are what
  makes Japanese-word search work), and rule markers (`<!-- tamo:rules … -->`,
  functional anchors for idempotent updates).
- **MCP tool descriptions are static English** — the audience is the LLM, and
  English is the lingua franca every model reads; they explicitly mention that
  CJK substring search works.
- Language detection: `TAMO_LANG` > `LC_ALL`/`LC_MESSAGES`/`LANG` > Windows UI
  locale > default `en`.
