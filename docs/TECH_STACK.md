# tamo tech stack

English | [日本語](TECH_STACK.ja.md)

> The technologies tamo uses and *why each one was chosen*, organized by layer so
> it is easy to check later. **The relevant file name is always given**, so when
> you want to verify a behavior, open that file and you land on the implementation.
>
> **Which doc to read**: for usage, see the [README](../README.md); for design
> philosophy, data model and NFRs, [ARCHITECTURE.md](ARCHITECTURE.md); for
> on-machine verification steps, [VERIFICATION.md](VERIFICATION.md).

## Table of contents

1. [Overall architecture and design principles](#1-overall-architecture-and-design-principles)
2. [Runtime and dependency policy](#2-runtime-and-dependency-policy)
3. [Data layer — SQLite / WAL / FTS5](#3-data-layer--sqlite--wal--fts5)
4. [CES v1 — canonical events and deterministic IDs](#4-ces-v1--canonical-events-and-deterministic-ids)
5. [CAS — content-addressed storage and magic-number sniffing](#5-cas--content-addressed-storage-and-magic-number-sniffing)
6. [textract — deterministic text extraction](#6-textract--deterministic-text-extraction)
7. [Adapter layer and cursor strategy](#7-adapter-layer-and-cursor-strategy)
8. [probe — real-machine fingerprinting](#8-probe--real-machine-fingerprinting)
9. [optimize — read-time optimization P1–P4](#9-optimize--read-time-optimization-p1p4)
10. [Serving layer — CLI / HTTP inbox / MCP / export](#10-serving-layer--cli--http-inbox--mcp--export)
11. [Browser extension (MV3)](#11-browser-extension-mv3)
12. [WSL2 / Windows integration](#12-wsl2--windows-integration)
13. [Test strategy and measured results](#13-test-strategy-and-measured-results)
14. [Where to fix it when it breaks (quick reference)](#14-where-to-fix-it-when-it-breaks-quick-reference)
15. [Glossary](#15-glossary)

---

## 1. Overall architecture and design principles

```
[sources]                         [tamo core]                          [consumers]
Claude Code transcript(JSONL) ─┐  probe.py    machine scan, auto-config
Cursor state.vscdb ────────────┤  adapters/   incremental, idempotent,    ┌ mcp_server.py (8 tools)
Codex CLI rollout(JSONL) ──────┼→   tolerant parsing                   ──┼ cli.py pack (Markdown)
aider history.md ──────────────┤  schema.py   CES v1 normalization        └ cli.py export (NDJSON)
browser ext → http_inbox.py ───┘  cas.py      blob extraction + reference replacement
                                  textract.py attachment text extraction
                                  store.py    SQLite+FTS5 persistence
                                  optimize.py read-time P1–P4
```

The four design principles (same as [ARCHITECTURE.md](ARCHITECTURE.md), here with
their implementation consequences):

| Principle | Implementation consequence |
|---|---|
| Lossless ingestion, read-time optimization | Raw text always survives in `raw_records`/`quarantine`. pack/search are "views" that can be rebuilt at any time |
| No LLM in collection or storage | event_id, key-point extraction, sentence selection — all deterministic (same input → same output). Semantic distillation belongs downstream (OmniBrain HITL) |
| Drift is assumed | Broken lines → quarantine; unknown schemas → raw kept + meta event. The guarantee is "never crash, never lose", not "always parse" |
| Attachments: metadata first | Even when contents are unreadable, kind/name/size always land in the body text. Text extraction is a bonus |

## 2. Runtime and dependency policy

| Item | Choice | Why |
|---|---|---|
| Language | Python **3.11+** (requires-python in `pyproject.toml`) | `X \| None` type syntax, the faster CPython. Ships out of the box on WSL2's Ubuntu |
| Core dependencies | **Zero** (stdlib only) | One-shot `pip install -e .` on corporate PCs and offline machines. Minimizes supply-chain risk and audit cost |
| Optional extras | `mcp`=FastMCP serving / `pdf`=pypdf / `media`=Pillow | External dependencies are allowed only in the layer where "collection still works perfectly without them" |
| Packaging | PEP 621 (`pyproject.toml`) + setuptools | CLI registered via `[project.scripts] tamo = "tamo.cli:main"` |

The standard-library modules in use and their roles (≒ this project's real stack):

`sqlite3` (persistence/FTS5), `zipfile`+`zlib` (OOXML/PDF unpacking), `hashlib` (sha256 = CAS/event_id),
`base64`, `re` (extractors, key-point rules), `html` (XML entities), `mimetypes`, `difflib` (P2 unified diff),
`http.server` (inbox), `argparse` (CLI), `pathlib`, `json`, `tempfile`+`shutil` (SQLite snapshots), `secrets` (token generation)

## 3. Data layer — SQLite / WAL / FTS5

**File**: `tamo/store.py` (the schema is `_SCHEMA` at the top)

A single file `~/.tamo/tamo.db`, `PRAGMA journal_mode=WAL`.
Why: no server, backup is one file, and WAL makes "the resident watcher writes
while MCP reads" work.

8 tables + 1 virtual:

| Table | Role |
|---|---|
| `raw_records` | Raw text verbatim (idempotent via UNIQUE locator). Same idea as ADR-024 |
| `events` | CES-normalized events. The `event_id` PK is the linchpin of idempotency |
| `sessions` | Session aggregates (title, counts, time range) |
| `blobs` / `blob_refs` | CAS metadata and event⇔blob references |
| `blob_texts` | Text extracted from attachments (sha256-keyed cache) |
| `cursors` | Per-source incremental positions |
| `quarantine` | Unparseable lines (raw text + error) |
| `events_fts` (FTS5 virtual) | Full-text search index |

**Why FTS5 trigram**: Japanese has no word boundaries, so the default `unicode61`
tokenizer makes word search nearly useless. `tokenize='trigram'` (SQLite 3.34+)
matches on 3-character windows, so **CJK substring search like 「甲板カバー」
("deck cover") or 「スナップショット」("snapshot") works with no morphological
analyzer**. On older SQLite it falls back to regular FTS5, and failing that to
`LIKE` (`_init_fts`/`search`).

**What goes into FTS** (`upsert_event`): `events.text` holds only the event body
and stays lean; the FTS row gets the body plus "[添付 name] + first 4,000 chars of
extracted text" concatenated. → Search hits the contents of attachments, but the
body that pack and friends read never bloats.

**snapshot_sqlite** (`tamo/adapters/__init__.py`): live databases (Cursor etc.)
are copied — main file + `-wal` + `-shm` — to a temp dir with `shutil.copy2`, then
`PRAGMA wal_checkpoint(TRUNCATE)` is run on the *copy* before reading. Why not
open directly: to avoid both lock contention with the app that holds the DB and
broken locking across WSL2's `/mnt/c` (9p filesystem). **tamo never writes to the
sources.**

## 4. CES v1 — canonical events and deterministic IDs

**File**: `tamo/schema.py`

The Canonical Event Schema normalizes utterances from every source into one shape:
`actor ∈ {user, assistant, tool, system}` × `kind ∈ {message, tool_use, tool_result, meta}`,
with `content` as an array of blocks (`text` / `tool_use` / `tool_result` / `blob`
/ the intermediate forms `image_b64`, `file_b64`).

**Deriving event_id** (the heart of idempotency):

```
event_id = sha256("source_kind|session_key|native_id or locator|kind|content fingerprint")[:32]
```

The point is that timestamps are **excluded** (time-format drift on the source side
never mints a new ID). Collecting the same line any number of times bounces off
`INSERT OR IGNORE` = re-collect is always safe.

`blocks_text()` flattens content to plain text for search/pack. Blob references are
rendered as `[添付(動画) clip.mp4 video/mp4 76B sha=e8066f89bf3f]` (literal stored
text: 添付 = attachment, 動画 = video) — **kind word (Japanese), name and size**
included. This is where the metadata-first principle is implemented.

## 5. CAS — content-addressed storage and magic-number sniffing

**File**: `tamo/cas.py`

- Attachment and pasted-image base64 is siphoned into `cas/ab/cd/<sha256>.<ext>`
  at ingestion time (2+2 hex-digit fan-out avoids directory bloat), and the event
  side is replaced with a `blob` reference block. The same binary is stored once no
  matter how many times it appears
- Claude Code transcripts embed pasted images as base64 and balloon to several MB;
  this replacement shrinks logs by orders of magnitude

**sniff prefers magic numbers** (declared MIME is assumed to lie):

| Leading bytes | Verdict |
|---|---|
| `%PDF` | application/pdf |
| `PK\x03\x04` | Open the ZIP and judge precisely by internal structure: `word/`→docx, `xl/`→xlsx, `ppt/`→pptx, otherwise→zip (`textract.office_kind`) |
| `\x89PNG` / `\xff\xd8\xff` / `GIF8` / RIFF+`WEBP` | The various image types |
| `ftyp` (bytes 4–8) | Brand check: `qt  `→mov, `M4A`→m4a, otherwise→mp4 |
| `\x1aE\xdf\xa3` (EBML) | webm/mkv → video/webm |
| RIFF+`WAVE` / `OggS` / `fLaC` / `ID3`, `\xff\xfb` | wav / ogg / flac / mp3 |

The declared MIME is used only when no magic matches (meaningless declarations
like `octet-stream` are ignored). An xlsx lying as `application/octet-stream`
being classified correctly is covered by an E2E test.

The kind vocabulary is `media_kind_ja()` in `tamo/util.py` (画像/動画/音声/PDF/
Word文書/表計算/スライド/テキスト/圧縮/ファイル — image/video/audio/PDF/Word
document/spreadsheet/slides/text/archive/file). This vocabulary is why a search for
a Japanese kind word like 「動画」("video") hits.

## 6. textract — deterministic text extraction

**File**: `tamo/textract.py`. The policy: "**readable = a search asset; unreadable
is still the happy path**".

| Format | Extractor | Implementation | Deps |
|---|---|---|---|
| docx | `docx-xml` | Concatenate `<w:t>` from `word/document.xml`, split on `</w:p>` | none |
| xlsx | `xlsx-xml` | Sheet names (`workbook.xml`) + `<si>` from `sharedStrings.xml` + inlineStr `<t>` | none |
| pptx | `pptx-xml` | `<a:t>` from `ppt/slides/slide*.xml`, per slide | none |
| PDF | `pypdf` → `pdf-naive` | See below | pypdf optional |
| txt/md/csv/json | `plain` | Try decoding UTF-8 → **CP932** → UTF-16, in that order | none |
| html | `html-strip` | Drop script/style + strip tags + `html.unescape` | none |

OOXML (docx/xlsx/pptx) is really just "ZIP+XML", so `zipfile` plus regular
expressions gives a zero-dependency extractor — that is why Office support is
doable with the stdlib.

**The two-tier PDF approach**:
1. `pypdf` first if available. It interprets ToUnicode CMaps, so **Japanese PDFs
   are readable**. Adopted if it passes the quality gate ≥0.5
2. Otherwise, naive stdlib extraction: try `zlib.decompress` (FlateDecode) on
   `stream..endstream`, then read only literal strings `( )` in `Tj/TJ/'/"`
   operators inside `BT..ET`. **Hex strings `<...>` are never read** — CID fonts
   (the mainstream for Japanese PDFs) end up there, and naive extraction of them
   is guaranteed mojibake
3. Finally the **quality gate** (readable-character ratio: alphanumerics/symbols +
   kana + CJK ideographs + full-width; ≥0.66 for naive extraction). Text that
   fails is **discarded**. Better "no extraction" than garbled text in the search
   index producing false hits

Limits: `MAX_TEXT=200,000 chars` / `MAX_BYTES=32MB` (beyond that, metadata only).

Results are sha256-cached in `blob_texts`; `tamo reindex-blobs` re-extracts old
databases retroactively and refreshes FTS (`store.reindex_blob_texts`).

## 7. Adapter layer and cursor strategy

**File**: `tamo/adapters/*.py`. The contract shared by every adapter:
(1) never write to the source (2) broken lines → `quarantine` + keep going
(3) unknown shapes → keep raw + `meta` event (4) incremental cursors.

| Adapter | Source | Cursor strategy | Notes |
|---|---|---|---|
| `claude_code` | `~/.claude/projects/**/*.jsonl` | **Byte offset** (fits append-only logs) | A final line without a newline = still being written, skipped until next run. CLI / VS Code extension / JetBrains / Claude Desktop / third-party VS extension all share **the same location** |
| `codex_cli` | `~/.codex/sessions/**/*.jsonl` | Byte offset | |
| `aider` | `.aider.chat.history.md` | Byte offset | Turns split on Markdown headings |
| `cursor_ide` | `cursorDiskKV` in `state.vscdb` | **rowid** (the KV store is append-ish) | Handles both the old `composerData` array and the new `bubbleId` split. In-place updates: `--rescan` |
| `generic_jsonl` | Anything (configure glob + field names) | Byte offset | Catch-all #1 for unsupported agents |
| `inbox` | `~/.tamo/inbox/*.json` | Move to `done/` after processing | Catch-all #2; where the browser extension posts |

**inbox v1 format** (`tamo.inbox.v1`):
`source/session/title/messages[{role,text,ts,attachments[]}]`. Attachments: with
`data_b64` they go to the CAS; with only a `url` they become a URL note; **even
bare `{name, mime, size}` metadata is accepted** and preserved in the body as
`[添付(動画 未取得) … 50.0MB]` (literal stored text: "attachment (video, not
fetched)"). The metadata-first principle: even what the extension dropped for
exceeding limits keeps its context.

## 8. probe — real-machine fingerprinting

**File**: `tamo/probe.py`. The tool that backs "works with any agent" with real
machines instead of spec sheets.

- **Scan targets**: the Linux/WSL home, plus `/mnt/c/Users/*` under WSL2
  (overridable via `TAMO_WIN_ROOT`, also used by tests)
- **Claude Code family**: `.claude/projects` (WSL and each Windows user) +
  `CLAUDE_CONFIG_DIR` (follows both a `.claude`-root value and a direct `projects`
  value). When it detects `.vscode-server/extensions/anthropic.claude-code*` (WSL
  remote) or `.vscode/extensions` (native/Windows), it notes "sessions are written
  to the transcripts above, no extra configuration needed". Also detects Visual
  Studio (.NET IDE) `ClaudeCodeExtension` traces
- **SQLite family (Cursor etc.)**: snapshots the DB and inspects down to **the
  table list and the distribution of key prefixes** (how many `composerData:`, how
  many `bubbleId:`, …) → when drift happens, what changed is visible at a glance
- **Detection only**: Windsurf / Gemini CLI / goose / Cline / Copilot Chat are
  reported by location only. The plan: look at a real machine's fingerprint first,
  then add a `generic_jsonl` config or a dedicated adapter
- Results are written to `sources.toml` (`--write`). collect reads only this config

## 9. optimize — read-time optimization P1–P4

**File**: `tamo/optimize.py`. Every stage is **deterministic** (no LLM, same input
→ same output). "Storage is never touched; every read passes through these four
stages", so algorithm improvements apply to past data too.

| Stage | Name | Implementation |
|---|---|---|
| P1 | dedup | Verbatim repeats of 120+ chars (**exact match**) collapsed into a reference (`⟨e:xxxx⟩と同一`, "same as ⟨e:xxxx⟩") |
| P2 | snapshot folding | `tool_result` runs against the same file compressed into "final version in full + earlier as `difflib.unified_diff`" (diff capped at 3,500 chars) |
| P3 | key-point extraction | Rule-based regexes (Japanese & English), 5 classes: decisions / constraints & assumptions / TODOs / error→fix / files touched |
| P4 | budget packing | See below |

**P4 selection logic** (`build_pack`):
- Token estimation `estimate_tokens`: ASCII word = 1, non-word chars such as CJK =
  1 token per char (a crude but deterministic estimate that points the right way
  for real conversations mixing Japanese and English)
- TF-IDF: vectorized with a simple tokenizer (ASCII words + **CJK character
  bigrams**)
- Base score = `0.55 × recency((i+1)/n) + 0.45 × cos(TF-IDF, query)` (recency only
  when no query is given)
- **MMR** (λ=0.35): `score = base − 0.35 × max(similarity to already selected)`
  suppresses redundant utterances. Implemented with incremental max-sim updates in
  O(n·k) (the naive O(n·k²) took 44.7s on a 100k-event DB → incremental updates +
  a candidate cap of 800 brought it to 0.42s, 106×). Vectors are pre-normalized so
  cosine = dot product
- Cut off the moment the budget (`--budget`) is exceeded; key-point sections are
  packed first, then the conversation tail
- Every line carries a `⟨e:first-8-hex⟩` provenance ID, always traceable to the
  raw text in the `events` table

## 10. Serving layer — CLI / HTTP inbox / MCP / export

**CLI** (`tamo/cli.py`, argparse): 22 subcommands — `probe / collect / watch /
stats / sessions / search / pack / export / reindex-blobs / mirror / rules / run /
serve / mcp / prune / purge / quarantine / recall / show / token / hook /
ingest-hook`.
`mirror` (git-friendly Markdown mirror, `tamo/derive.py`), `rules` (idempotent
marker-block writes of derived rules) and `run` (agent execution wrapper) are the
feedback features adopted after studying the SpecStory competitor.
`serve` is the resident mode bundling the collection thread + HTTP inbox + MCP
(streamable-http, FastMCP/uvicorn) + daily auto-prune into one process (the
product as a standalone service). `mcp` starts stdio/HTTP standalone.
`show` displays a single session (`latest` resolution, `--tail`, continuation via
`--since-event`). `prune`/`purge` implement the retention NFR
(`[retention] days` in `~/.tamo/settings.toml`, default 0 = forever; judged by
event activity time, mtime unused; dry-run required; never deletes silently —
a lesson from the Claude Code cleanup incident).
`tamo/redact.py` masks secrets before commit/sharing (known key prefixes +
conservative key=value line masking, deterministic).
`hook` prints the snippet for Claude Code's `Stop`/`SessionEnd` hooks, and
`ingest-hook` takes `transcript_path` on stdin and immediately ingests **that one
file** incrementally (`async: true` keeps the host tool unblocked).

**HTTP inbox** (`tamo/http_inbox.py`, stdlib `http.server`):
- Binds to `127.0.0.1` only (never exposed to the LAN) + `X-Tamo-Token`
  (`~/.tamo/inbox.token`, auto-generated on first run via `secrets`). Comparison
  uses `secrets.compare_digest` (timing-safe). `/health` validates the token only
  when one is supplied (so the extension's "connection check" can test authz too)
- Auth OK = 204 / token mismatch = 403 (body includes remediation guidance) /
  non-JSON = 400 / 50MB cap
- The server **only validates and writes to a file**. Parsing is funneled through
  the regular inbox adapter — the network never touches a parser directly
  (minimized attack surface)

**MCP** (`tamo/mcp_server.py`, FastMCP, optional extras): 8 tools —
`recall (start here) / search_context / get_context / get_context_pack /
list_sessions / get_session / get_blob_text / get_blob_base64`.
Registration: stdio via `claude mcp add tamo -- python -m tamo.mcp_server` (same
for Cursor/Codex CLI), HTTP (streamable-http, `tamo serve`) with
`--header "X-Tamo-Token: $(tamo token)"`.
**HTTP requires the token via `_TokenGate` (an ASGI wrapper)** — avoiding the
asymmetry of authenticating writes (inbox) while reads (every conversation + blobs)
pass through. stdio needs none: the client child process *is* the user.
`get_blob_text` returns the extracted text of an attachment (lighter than base64);
`get_blob_base64` returns the original bytes.

**export**: NDJSON, one line = one session (`tamo.session.v1`). `--include-raw`
bundles the raw records tied to each session → flows straight into OmniBrain's
chunked distillation → HITL. The division of labor: **tamo does deterministic
collection, OmniBrain does semantic distillation**.

## 11. Browser extension (MV3)

**Directory**: `browser-extension/`. Chrome Manifest V3.

```
popup.js ──(scoop request)──> content/main.js ──> sites/*.js or generic.js
                                │ payload (tamo.inbox.v1)
popup.js <──────────────────────┘
   │ tamo.post
background.js(Service Worker) ──POST──> http://127.0.0.1:8787/inbox
```

| Choice | Why |
|---|---|
| localhost POSTs happen in the **background SW** | A content script's fetch trips over the page's CSP / mixed content (https→http). The SW can send freely under `host_permissions` (127.0.0.1 only) |
| claude.ai/ChatGPT use the **same-origin API** approach | The DOM breaks with every UI redesign; the JSON APIs the app itself uses are orders of magnitude more stable. claude.ai: `/api/organizations` → `chat_conversations?tree=True&rendering_mode=messages`. ChatGPT: `/api/auth/session` → walk the `backend-api/conversation` mapping tree from `current_node` back to the root |
| On failure, **automatic fallback to generic DOM** | The payload keeps `note: "site adapter failed: …"`. When something breaks, the fix is one `sites/*.js` file |
| Gemini alone uses the DOM approach | No stable JSON API usable for conversation retrieval. Reads the `<user-query>`/`<model-response>` custom elements |
| Generic extraction is a **3-tier heuristic** | (1) role attributes (`data-message-author-role` etc.) (2) class-name keywords (user/assistant/ai…) (3) alternation inferred from repeated sibling elements. Last resort: the whole `main` as one message |
| Unsupported sites get **on-demand injection** | `activeTab`+`scripting.executeScript`. Never requests `<all_urls>` = minimal permissions |
| Settings in `chrome.storage.sync` | Port (default 8787) and token. Saved from the popup |

**Attachment policy** (`content/lib.js`): limits of **6MB/attachment, 20MB/conversation,
20 attachments, 400 messages, 100k chars/message**.
- Images: fetch → bundle as b64 (icons under 48px excluded)
- **Video/audio: the payload is never fetched.** If a `poster` exists, only the
  thumbnail is bundled, and a `[添付(動画 未取得) …]` note ("attachment (video,
  not fetched)") is always left in the body — this also handles Gemini's video
  attachments gracefully
- claude.ai uploaded documents: bundles the platform's **already-extracted text**
  (`extracted_content`)
- ChatGPT images/files: resolve the URL via `files/{id}/download` → b64 (failures
  become notes)
- b64 encoding uses `TextEncoder` + 32KB chunks (avoids `btoa` stack overflow on
  huge strings)

## 12. WSL2 / Windows integration

| Concern | Approach |
|---|---|
| Windows-side transcripts | probe scans `/mnt/c/Users/*/.claude/projects` (overridable via `TAMO_WIN_ROOT`) |
| Relocated `.claude` | Follows the `CLAUDE_CONFIG_DIR` environment variable (the VS / VS Code extensions read the same location) |
| Browser (Windows) → tamo (WSL) | WSL2 localhost forwarding (on by default on Win11) delivers `127.0.0.1:8787`. `localhostForwarding=true` in `%UserProfile%\.wslconfig` |
| File watching on `/mnt/c` | inotify does not work over 9p → `watch` is **polling-based** (`--interval`) |
| SQLite locks on `/mnt/c` | Avoided by snapshot_sqlite (copy, then read) |

## 13. Test strategy and measured results

**pytest suite** (`tests/test_*.py`, 55+ tests) + generator-style fixtures
(`tests/make_fixtures.py` = a mock environment of 5 sources,
`tests/make_attachment_fixtures.py` = synthetic PDF/docx/xlsx).
CI (`.github/workflows/ci.yml`) runs pytest on an ubuntu/windows × Python
3.11/3.13 matrix and syntax-checks the extension JS with `node --check`.
Coverage pillars: idempotency / encoding resilience (cp932 reproduction) / lock
reclamation / CAS self-healing / inbox move-after-commit / quarantine flows /
redact patterns / HTTP authorization / MCP tools + auth gate (when mcp installed).
The extension, which cannot touch live sites, is covered by **contract tests**
(generate a payload with the same logic as lib.js → inbox → collect → search).

| Verification | Result |
|---|---|
| Fresh-DB E2E (probe→collect) | events=22 / sessions=7 / blobs=4 / blob_texts=3 / quarantine=1 |
| Idempotency | Second collect is +0 across all sources |
| Drift drill (append 3 lines: valid / broken JSON / unknown schema) | +2 incremental / quarantine +1 / raw kept + meta event, **no stoppage** |
| Attachment extraction | docx-xml 42 chars (search hit on 「甲板カバー連動」, a Japanese sample phrase) / xlsx-xml 20 chars (lying MIME → correct magic verdict) / pdf-naive 64 chars / pypdf 53 chars |
| Metadata-only attachment | `[添付(動画 未取得) berth_trial.mp4 video/mp4 50.0MB]` preserved in the body, search hit on the kind word 「動画」("video") |
| pack | 5 sources, 18 events → 1,285 tokens, every line tagged ⟨e:xxxx⟩ |
| Performance (100k events / 195MB, tests/bench.py) | Ingestion 8,000 ev/s / FTS search 0.04–16ms / tail fetch 0.1ms / continuation fetch 4ms / MCP get_session (compact) 3ms / pack (5,000 ev) 0.42s |
| HTTP inbox | 204 (valid token) / 403 (forged) / 400 (non-JSON) → ingestion confirmed |
| MCP | Direct calls to all 8 tools + list_tools registration check |
| Extension | All JS `node --check` PASS / manifest valid / contract tests (search hit on attachment text "JIS Z 3183", not-fetched note preserved) |

## 14. Where to fix it when it breaks (quick reference)

| Symptom | Look at first | Fix in |
|---|---|---|
| quarantine count in `tamo stats` grows | The `error` and raw text in the `quarantine` table | The tolerant parser in the matching `tamo/adapters/*.py` |
| Cursor's new format yields no new events | Key-prefix distribution from `tamo probe` | `adapters/cursor_ide.py` |
| Claude Code transcript format change | quarantine + probe's transcript count | `adapters/claude_code.py` (officially documented as "internal format may change between versions") |
| Scoop fails on claude.ai/ChatGPT | `⚠ site adapter failed: …` in the popup | `browser-extension/content/sites/*.js` (meanwhile the generic DOM path keeps working) |
| Supporting a new AI chat site | Try whether generic can scoop it | Add one file under `sites/` + a manifest `matches` entry |
| An unknown CLI agent | `detected_only` from `tamo probe` | A `generic_jsonl` entry in `sources.toml`, or a new adapter |
| Japanese PDFs missing from search | Is `blob_texts.extractor` empty? | `pip install pypdf` → `tamo reindex-blobs` |
| You improved an extractor | — | `tamo reindex-blobs` for retroactive re-extraction |

## 15. Glossary

| Term | Meaning |
|---|---|
| CES | Canonical Event Schema. The normalized event format shared by all sources (v1) |
| CAS | Content-Addressed Storage. Blob store addressed by sha256; deduplication comes for free |
| FTS5 / trigram | SQLite's full-text search extension / 3-char-window tokenizer. The key to CJK substring matching |
| WAL | Write-Ahead Logging. SQLite journal mode for concurrent read/write and crash resilience |
| TF-IDF / MMR | Term-rarity weighting / Maximal Marginal Relevance (balancing relevance and diversity in selection) |
| quarantine | Where unparseable data is isolated. Keeps raw text + error, rescuable later by fixing the adapter |
| HITL | Human-in-the-Loop. Out of scope for tamo (deterministic collection); refers to downstream OmniBrain's approval step |
| MV3 | Chrome extension Manifest V3. Resident Service Worker + declarative permission model |
| inbox v1 | `tamo.inbox.v1`. The JSON contract browser extensions etc. use to post into tamo |
| locator | Provenance coordinates of raw text (e.g. `path::byte-offset` / `inbox::file.json`). The UNIQUE key of raw_records |
