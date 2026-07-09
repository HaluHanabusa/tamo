# tamo on-machine verification guide

English | [日本語](VERIFICATION.ja.md)

Target environment: **Windows 11 + WSL2 (Ubuntu)** / CLI: **Claude Code** / Browser: **claude.ai, Gemini, ChatGPT** (Chrome or Edge)
Estimated time: 40–60 minutes (Phases 1–4 are the core; 5 onward is optional)

> The basic verification technique: plant a **unique marker string** into a
> conversation on each surface (e.g. `tamo検証CLI-001` — "tamo verification";
> keeping the markers in Japanese also exercises CJK search) and pass/fail on
> whether `tamo search` finds it later. The session_key tells you which surface
> it came from.

---

## Phase 0. Installation (WSL2 side)

```bash
cd ~ && unzip tamo.zip && cd tamo
python3 -m venv ~/.venvs/tamo
source ~/.venvs/tamo/bin/activate
pip install -e ".[mcp,pdf]"        # mcp = MCP server / pdf = Japanese PDF extraction (optional)
tamo --help
```

| # | Check | Expected result | ✓ |
|---|---|---|---|
| 0-1 | `tamo --help` | Lists the probe/collect/serve/search/show/pack/... subcommands | ☐ |
| 0-2 | `python3 -c "import sqlite3;print(sqlite3.sqlite_version)"` | **3.34 or newer** (required for trigram CJK search; older still works but search degrades to LIKE) | ☐ |

> Without a venv, use `pip install -e ".[mcp,pdf]" --break-system-packages`.
> Run all subsequent commands in a shell with the venv activated.

## Phase 1. Collecting Claude Code (CLI)

**1-1. Plant a marker**: open Claude Code in a project you normally use, say
`This session is a test for tamo検証CLI-001. Decision: verify using the marker approach.`,
continue the session for at least one exchange, then end it.

**1-2. Detect and collect**:

```bash
tamo probe --write     # scan the machine → ~/.tamo/sources.toml
tamo collect
tamo collect           # ← second run
tamo stats
tamo search "tamo検証CLI-001"
tamo show latest:claude_code --tail 3
```

| # | Check | Expected result | ✓ |
|---|---|---|---|
| 1-1 | probe output | Shows `claude_code[wsl]: ~/.claude/projects (transcript N件)`. With the VS Code extension installed, a "no extra configuration needed" note | ☐ |
| 1-2 | 1st collect | Ingests `raw+N events+M` (proportional to existing history) | ☐ |
| 1-3 | 2nd collect | **+0 across all sources** (idempotency) | ☐ |
| 1-4 | search | The CLI-001 marker utterance hits, session_key is `claude_code:...` | ☐ |
| 1-5 | show latest | Last 3 events of the most recent session shown with ⟨e:xxxx⟩ tags | ☐ |

## Phase 2. Standalone service and MCP connection

**2-1. Start serve** (separate terminal; leave it running):

```bash
source ~/.venvs/tamo/bin/activate
tamo serve
```

**2-2. Register with Claude Code** (original terminal):

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: $(tamo token)"
tamo hook   # paste the printed CLAUDE.md snippet into the target project's CLAUDE.md (recommended)
```

> HTTP is token-authenticated (the serve startup banner prints a copy-pasteable
> registration example). Requests without the header get a 401.

**2-3. Call it from inside Claude Code**: in a new session, say the following in
order:

1. `Search for "tamo検証CLI-001" with tamo's search_context and show me the raw results.`
2. `Check tamo for what happened with that tamo検証CLI-001 topic we discussed.`

| # | Check | Expected result | ✓ |
|---|---|---|---|
| 2-1 | serve startup output | Shows the inbox (8787) / mcp (8788) URLs plus registration examples, and stays resident | ☐ |
| 2-2 | `claude mcp list` | tamo is connected | ☐ |
| 2-3 | Utterance 1 | A search_context tool call runs and returns the CLI-001 hit | ☐ |
| 2-4 | Utterance 2 | Chains search → **get_context** (surrounding context) and traces its answer back to "Decision: verify using the marker approach" | ☐ |
| 2-5 | Live collection | The Phase 2-3 session itself hits `tamo search "raw results"` about 60 seconds later (serve's automatic collection) | ☐ |

> stdio alternative: `claude mcp add tamo -- ~/.venvs/tamo/bin/tamo mcp` (absolute path recommended)

## Phase 3. Browser extension "tamo scoop" (Windows side)

**3-1. Load it**: Chrome/Edge → `chrome://extensions` (`edge://extensions` on
Edge) → enable Developer mode → *Load unpacked* → select
`\\wsl.localhost\Ubuntu\home\<username>\tamo\browser-extension`
(if the WSL path fails to load, copy the folder to the Windows side).

**3-2. Pairing**: with serve running, just open the extension popup — it
**pairs automatically** (you'll see a "paired with tamo ✓" message). To paste
manually instead, use the output of `tamo token`.

**3-3. Isolating connectivity issues** (when things don't work):

```bash
# (a) Windows PowerShell: curl.exe http://127.0.0.1:8787/health → "ok" means the route is fine
# (b) Post from WSL without the extension (tests server + token in isolation):
TOKEN=$(tamo token)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8787/inbox \
  -H "X-Tamo-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"schema":"tamo.inbox.v1","source":"curl_test","session":"t1","messages":[{"role":"user","text":"tamo検証CURL-001"}]}'
# → if this returns 204 and tamo search "tamo検証CURL-001" hits, the server side is healthy. The only remaining suspect is the extension
# (c) Extension: does the popup's connection check pass? / are you on a conversation URL? / did you reload the tab? / record the status text
```

**3-4. Scoop on the 3 sites**: create a conversation on each site and click the
**🎣 button (top right)** (or the popup's scoop button):

| Site | Conversation content (example) | Expected behavior |
|---|---|---|
| claude.ai | `tamo検証WEB-CLA-001. Remember this sentence.` + **attach one docx or PDF** | Extraction OK (claude.ai) / attachments≥1 |
| ChatGPT | `tamo検証WEB-GPT-001` + attach one image | Extraction OK (chatgpt.com) |
| Gemini | `tamo検証WEB-GEM-001` | Extraction OK (gemini.google.com). It's DOM-based, so `⚠...generic(fallback)` is **still a pass** |

**3-5. Verify on the WSL side** (with serve running, just wait ~60 seconds; in a
hurry, `tamo collect --only inbox`):

```bash
tamo search "tamo検証WEB-CLA-001"
tamo search "tamo検証WEB-GPT-001"
tamo search "tamo検証WEB-GEM-001"
tamo stats
```

| # | Check | Expected result | ✓ |
|---|---|---|---|
| 3-1 | Popup connection check | "connected to tamo OK" | ☐ |
| 3-2 | Scooping the 3 sites | Each shows "posted ✓ (claude:... / chatgpt:... / web:...)" | ☐ |
| 3-3 | search ×3 | Each marker hits, and the session_key prefix identifies the origin | ☐ |
| 3-4 | Attachments | `blobs` in stats increases. For the claude.ai docx/PDF, `tamo search "<a word inside the document>"` **hits the contents** | ☐ |
| 3-5 | Preservation of not-fetched items | If any attachment was too large / unfetchable, the body keeps `[添付(種別 未取得) 名前 ...]` (literal stored text: "attachment (kind, not fetched) name") | ☐ |

## Phase 4. Cross-surface handoff (the actual goal)

In a **new** Claude Code session:

1. `Search tamo for "tamo検証WEB-CLA-001" and tell me which surface the conversation came from.`
   → confirms **a browser (claude.ai) conversation is retrievable from the CLI**
2. `Call get_context_pack with query="tamo検証" and paste the key points.`
   → returns cross-CLI/browser decisions with ⟨e:xxxx⟩ tags

| # | Check | Expected result | ✓ |
|---|---|---|---|
| 4-1 | Cross-surface search | The claude.ai-origin marker is retrievable from Claude Code | ☐ |
| 4-2 | pack | Content from multiple surfaces mixed into a single pack | ☐ |

## Phase 5. Feedback (optional)

```bash
cd <your usual project>
tamo rules --project <part of the project name> --write CLAUDE.md
tamo mirror --project <same> --redact --out .tamo/history
tamo run -- claude   # single-command swap; auto-collects on exit
```

Expected: the marker block `<!-- tamo:rules:begin/end -->` is appended to
CLAUDE.md and stays a single block on re-runs / `.tamo/history/*.md` is generated
with API keys etc. masked as `[REDACTED:...]`.

## Phase 6. NFR (optional)

```bash
tamo prune --days 365 --dry-run   # preview what would be deleted (does not delete)
cat ~/.tamo/settings.toml          # where the retention setting lives
tamo stats                         # db_bytes / oldest_event
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| probe doesn't find claude_code | Check `ls ~/.claude/projects`. If you use `CLAUDE_CONFIG_DIR`, probe should follow it as-is. If you only use the Windows side, check that `/mnt/c/Users/<name>/.claude/projects` appears |
| CJK search misses | Check the SQLite version from Phase 0-2. Below 3.34, consider updating the OS's sqlite |
| Extension shows "site adapter failed → generic" | The private API changed. **If the generic fallback captured the content, it's a pass.** The only fix is `browser-extension/content/sites/<that-site>.js` |
| Extraction fails on ChatGPT | Confirm you're logged in and the URL is a `/c/<id>` conversation page |
| serve port conflicts | Work around with `tamo serve --mcp-port 8790 --inbox-port 8791` (update the registration URL too) |
| quarantine keeps growing | `tamo stats` for the count; raw text is quarantined so nothing is lost. Report it with the `probe` fingerprint attached and the adapter gets fixed |

## Reporting template

```
Phase 1: 1-1[ ] 1-2[ ] 1-3[ ] 1-4[ ] 1-5[ ]
Phase 2: 2-1[ ] 2-2[ ] 2-3[ ] 2-4[ ] 2-5[ ]
Phase 3: 3-1[ ] 3-2[ ] 3-3[ ] 3-4[ ] 3-5[ ]  (CLA/GPT/GEM breakdown: )
Phase 4: 4-1[ ] 4-2[ ]
Observations, full error text, probe output:
```
