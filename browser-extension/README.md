# tamo scoop — browser extension (MV3)

English | [日本語](README.ja.md)

An extension that **posts conversations from AI chat sites into tamo's inbox
(localhost)**. Data is never sent anywhere except localhost.

## Support

| Site | Method | Attachments |
|---|---|---|
| claude.ai | Same-origin API (the JSON the app itself uses) | Documents: bundles claude.ai's already-extracted text; images: preview as b64 |
| chatgpt.com / chat.openai.com | Same-origin API (mapping-tree reconstruction) | Images/files: resolved via download URL → b64 |
| gemini.google.com | DOM (user-query / model-response elements) | In-message images as b64 |
| Everything else | Generic DOM heuristics (injected on demand) | In-message images as b64 |

**Attachment policy**: whatever can be fetched as b64 is bundled (6MB/attachment,
20MB/conversation, up to 20 items). Anything unfetchable or over the limits always
leaves `[添付(未取得): 名前 mime サイズ]` in the body (literal stored text:
"attachment (not fetched): name mime size") — information is never dropped
silently. Bundled PDF/docx/xlsx files are automatically text-extracted on the tamo
side and become full-text searchable.

## Installation

1. `chrome://extensions` → enable Developer mode → *Load unpacked* → this folder
2. Start `tamo serve` (WSL or native, either works; this one process brings up
   the inbox + MCP + automatic collection)
3. Open the extension popup once → it **pairs automatically**, no token setup
   needed (`GET /pair`, with a Host-header check so nothing outside localhost can
   read it). If the token ever drifts, the popup's **Re-pair** button restores it
   instantly (this is the fix for a 403)
4. On an AI chat conversation page, one click on the **🎣 button (top right)** →
   a result toast appears in place (the popup's scoop button does the same; the
   persistent 🎣 can be turned off with a popup checkbox. For long conversations,
   the button shows progress n/25 while older messages auto-load)

> **WSL2 localhost**: on Windows 11, WSL2's localhost forwarding is enabled by
> default, so the Windows browser reaches tamo inside WSL at `127.0.0.1:8787`.
> If it doesn't, check `localhostForwarding=true` in `%UserProfile%\.wslconfig`.

## If "Cannot read properties of undefined" appears after updating the extension

By Chrome's design, after an extension reload the old scripts in **tabs opened
before the update** lose `chrome.runtime` (orphaning). **Reload that tab with F5**
and it's fixed. The current version detects orphaning, shows a "please reload"
toast, and automatically stops its background polling as well.

## About drift (an honest note)

The claude.ai / ChatGPT APIs are private and change without notice. This extension
**automatically falls back to generic DOM extraction when a site adapter fails**,
and even then keeps `note: "site adapter failed: ..."` in the payload. When
something breaks, the fix is a single adapter file (`content/sites/*.js`).
