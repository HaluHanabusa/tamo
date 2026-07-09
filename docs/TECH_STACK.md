# tamo 技術スタック解説書

> tamoで使っている技術と「なぜそれを選んだか」を、後から確認しやすいように
> レイヤーごとにまとめたもの。**該当ファイル名を常に併記**しているので、
> 挙動を確かめたくなったらそのファイルを開けば実装に辿り着けます。
>
> **読み分け**: 使い方は [README](../README.md)、設計思想・データモデル・NFRは
> [ARCHITECTURE.md](ARCHITECTURE.md)、実機確認手順は [VERIFICATION.md](VERIFICATION.md)。

## 目次

1. [全体アーキテクチャと設計原則](#1-全体アーキテクチャと設計原則)
2. [ランタイムと依存方針](#2-ランタイムと依存方針)
3. [データ層 — SQLite / WAL / FTS5](#3-データ層--sqlite--wal--fts5)
4. [CES v1 — 正準イベントと決定論的ID](#4-ces-v1--正準イベントと決定論的id)
5. [CAS — コンテンツアドレス格納とマジックナンバー判定](#5-cas--コンテンツアドレス格納とマジックナンバー判定)
6. [textract — 決定論テキスト抽出](#6-textract--決定論テキスト抽出)
7. [アダプタ層とカーソル戦略](#7-アダプタ層とカーソル戦略)
8. [probe — 実機フィンガープリンタ](#8-probe--実機フィンガープリンタ)
9. [optimize — 読出時最適化 P1〜P4](#9-optimize--読出時最適化-p1p4)
10. [配布層 — CLI / HTTP inbox / MCP / export](#10-配布層--cli--http-inbox--mcp--export)
11. [ブラウザ拡張 (MV3)](#11-ブラウザ拡張-mv3)
12. [WSL2 / Windows 統合](#12-wsl2--windows-統合)
13. [テスト戦略と実測値](#13-テスト戦略と実測値)
14. [壊れたらどこを直すか（早見表）](#14-壊れたらどこを直すか早見表)
15. [用語集](#15-用語集)

---

## 1. 全体アーキテクチャと設計原則

```
[ソース]                          [tamo コア]                        [消費側]
Claude Code transcript(JSONL) ─┐  probe.py    実機走査・自動設定
Cursor state.vscdb ────────────┤  adapters/   増分・冪等・寛容パース   ┌ mcp_server.py (8ツール)
Codex CLI rollout(JSONL) ──────┼→ schema.py   CES v1 正規化        ──┼ cli.py pack (Markdown)
aider history.md ──────────────┤  cas.py      blob吸出し+参照置換     └ cli.py export (NDJSON)
ブラウザ拡張 → http_inbox.py ──┘  textract.py 添付テキスト抽出
                                  store.py    SQLite+FTS5 永続化
                                  optimize.py 読出時 P1〜P4
```

4つの設計原則（[ARCHITECTURE.md](ARCHITECTURE.md) と同じ。実装上の帰結を添える）:

| 原則 | 実装上の帰結 |
|---|---|
| 取込は無損失、最適化は読出時 | 原文は `raw_records`/`quarantine` に必ず残る。pack/検索はいつでも再構築できる「ビュー」 |
| 収集・保存にLLMを使わない | event_id・要点抽出・文選抜まで全て決定論（同入力→同出力）。意味的蒸留は下流(OmniBrain HITL)の仕事 |
| ドリフト前提 | 壊れた行→quarantine、未知スキーマ→raw温存+metaイベント。「常にパースできる」ではなく「落ちない・失わない」を保証 |
| 添付はメタデータ優先 | 中身が読めなくても種別・名前・サイズを本文に必ず残す。テキスト抽出はボーナス |

## 2. ランタイムと依存方針

| 項目 | 選定 | 理由 |
|---|---|---|
| 言語 | Python **3.11+** (`pyproject.toml` の requires-python) | `X | None` 型構文・高速化されたCPython。WSL2のUbuntuに素で入る |
| コア依存 | **ゼロ**（標準ライブラリのみ） | 社内PC・オフライン環境で `pip install -e .` 一発。供給網リスクと監査コストの最小化 |
| 任意extras | `mcp`=FastMCP配布 / `pdf`=pypdf / `media`=Pillow | 「無くても収集は完全に動く」層にだけ外部依存を許す |
| パッケージング | PEP 621 (`pyproject.toml`) + setuptools | `[project.scripts] tamo = "tamo.cli:main"` でCLI登録 |

使っている標準ライブラリと役割（≒このプロジェクトの本当のスタック）:

`sqlite3`(永続層/FTS5), `zipfile`+`zlib`(OOXML/PDF展開), `hashlib`(sha256=CAS/event_id),
`base64`, `re`(抽出器・要点規則), `html`(XMLエンティティ), `mimetypes`, `difflib`(P2のunified diff),
`http.server`(inbox), `argparse`(CLI), `pathlib`, `json`, `tempfile`+`shutil`(SQLiteスナップショット), `secrets`(トークン生成)

## 3. データ層 — SQLite / WAL / FTS5

**ファイル**: `tamo/store.py`（スキーマ定義は先頭の `_SCHEMA`）

単一ファイル `~/.tamo/tamo.db`、`PRAGMA journal_mode=WAL`。
選定理由: サーバ不要・1ファイルでバックアップ完結・WALで「watch常駐が書きながらMCPが読む」が成立。

テーブル8+仮想1:

| テーブル | 役割 |
|---|---|
| `raw_records` | 原文そのまま (locator UNIQUE で冪等)。ADR-024と同思想 |
| `events` | CES正規化イベント。`event_id` PK が冪等の要 |
| `sessions` | セッション集約（タイトル・件数・期間） |
| `blobs` / `blob_refs` | CASメタと イベント⇔blob の参照 |
| `blob_texts` | 添付から抽出したテキスト（sha256キャッシュ） |
| `cursors` | ソースごとの増分位置 |
| `quarantine` | パース不能行の隔離（原文+エラー付き） |
| `events_fts` (FTS5仮想) | 全文検索インデックス |

**FTS5 trigram を選んだ理由**: 日本語は分かち書きが無いので既定の `unicode61` では
単語検索がほぼ効かない。`tokenize='trigram'`（SQLite 3.34+）は3文字窓の部分一致なので
「甲板カバー」「スナップショット」のような**日本語の部分文字列検索が形態素解析なしで**成立する。
古いSQLiteでは通常FTS5 → それも無ければ `LIKE` に自動フォールバック（`_init_fts`/`search`）。

**FTSに何を載せるか**（`upsert_event`）: `events.text` はイベント本文だけを保ち軽量に、
FTS行には本文+「[添付 name]+抽出テキスト先頭4000字」を連結して載せる。
→ 検索は添付の中身までヒットするが、pack等が読む本文は肥大しない。

**snapshot_sqlite**（`tamo/adapters/__init__.py`）: 稼働中のCursor等のDBは
`shutil.copy2` で本体+`-wal`+`-shm` をテンポラリへコピー →
コピー側で `PRAGMA wal_checkpoint(TRUNCATE)` してから読む。
直接openしない理由: アプリが掴んでいるDBのロック競合と、WSL2の `/mnt/c`（9pファイルシステム）
越しのロック不全を両方回避するため。**ソースには一切書き込まない**。

## 4. CES v1 — 正準イベントと決定論的ID

**ファイル**: `tamo/schema.py`

全ソースの発話を1つの形に正規化する Canonical Event Schema:
`actor ∈ {user, assistant, tool, system}` × `kind ∈ {message, tool_use, tool_result, meta}`、
`content` はブロック配列（`text` / `tool_use` / `tool_result` / `blob` / 中間表現 `image_b64`・`file_b64`）。

**event_id の導出**（冪等の心臓部）:

```
event_id = sha256("source_kind|session_key|native_id または locator|kind|contentの指紋")[:32]
```

タイムスタンプを**入れない**のがポイント（ソース側の時刻表記ゆれで別IDにならない）。
同じ行を何度収集しても `INSERT OR IGNORE` で弾かれる＝再collectが常に安全。

`blocks_text()` は検索・pack用の平文化。blob参照は
`[添付(動画) clip.mp4 video/mp4 76B sha=e8066f89bf3f]` のように
**種別語(日本語)・名前・サイズ**を含む形で描画される（メタデータ優先原則の実装点）。

## 5. CAS — コンテンツアドレス格納とマジックナンバー判定

**ファイル**: `tamo/cas.py`

- 添付・貼付画像のbase64は取込時に `cas/ab/cd/<sha256>.<ext>` へ吸い出し（先頭2+2桁のファンアウトでディレクトリ肥大回避）、
  イベント側は `blob` 参照ブロックに置換。同一バイナリは何度現れても1回しか保存されない
- Claude Code transcriptは貼付画像をbase64で丸抱えして数MBに膨れるため、この置換でログが桁で縮む

**sniff はマジックナンバー優先**（申告mimeは嘘をつく前提）:

| 先頭バイト | 判定 |
|---|---|
| `%PDF` | application/pdf |
| `PK\x03\x04` | ZIPを開いて内部構造で精密判定: `word/`→docx, `xl/`→xlsx, `ppt/`→pptx, それ以外→zip（`textract.office_kind`） |
| `\x89PNG` / `\xff\xd8\xff` / `GIF8` / RIFF+`WEBP` | 画像各種 |
| `ftyp`(4-8バイト目) | ブランド判定: `qt  `→mov, `M4A`→m4a, その他→mp4 |
| `\x1aE\xdf\xa3`(EBML) | webm/mkv → video/webm |
| RIFF+`WAVE` / `OggS` / `fLaC` / `ID3`・`\xff\xfb` | wav / ogg / flac / mp3 |

マジックに当たらないときだけ申告mimeを使う（`octet-stream`等の無意味な申告は無視）。
`application/octet-stream` と嘘をつかれたxlsxが正しく判定されることをE2Eで確認済み。

種別語彙は `tamo/util.py` の `media_kind_ja()`（画像/動画/音声/PDF/Word文書/表計算/スライド/テキスト/圧縮/ファイル）。
検索が「動画」のような日本語種別語で当たるのはこの語彙のおかげ。

## 6. textract — 決定論テキスト抽出

**ファイル**: `tamo/textract.py`。方針は「**読めれば検索資産、読めなくても正常系**」。

| 形式 | 抽出器 | 実装 | 依存 |
|---|---|---|---|
| docx | `docx-xml` | `word/document.xml` の `<w:t>` を `</w:p>` 区切りで連結 | なし |
| xlsx | `xlsx-xml` | シート名(`workbook.xml`) + `sharedStrings.xml` の `<si>` + inlineStrの `<t>` | なし |
| pptx | `pptx-xml` | `ppt/slides/slide*.xml` の `<a:t>` をスライドごとに | なし |
| PDF | `pypdf` → `pdf-naive` | 下記 | pypdf任意 |
| txt/md/csv/json | `plain` | UTF-8 → **CP932** → UTF-16 の順で decode 試行 | なし |
| html | `html-strip` | script/style除去 + タグ剥がし + `html.unescape` | なし |

OOXML(docx/xlsx/pptx)の実体は「ZIP+XML」なので、`zipfile`+正規表現だけで
依存ゼロの抽出が成立する — これがOffice対応をstdlibでやれる理由。

**PDFの二段構え**:
1. `pypdf` があれば最優先。ToUnicode CMap を解釈できるので**日本語PDFが読める**。
   品質ゲート ≥0.5 を通れば採用
2. 無ければ stdlib 素朴抽出: `stream..endstream` を `zlib.decompress`(FlateDecode)で試し、
   `BT..ET` 内のリテラル文字列 `( )` の `Tj/TJ/'/"` だけ読む。
   **16進文字列 `<...>` は最初から読まない** — CIDフォント(日本語PDFの主流)は
   ここに来て素朴抽出では必ず化けるため
3. 最後に**品質ゲート**（可読文字比率: 英数記号+かな+CJK漢字+全角。素朴抽出は≥0.66）。
   通らないテキストは**捨てる**。化けたテキストを検索インデックスに入れて
   誤ヒットさせるくらいなら「抽出なし」の方が正しい、という判断

上限: `MAX_TEXT=200,000字` / `MAX_BYTES=32MB`（それ以上は抽出せずメタのみ）。

抽出結果は `blob_texts` にsha256キャッシュされ、`tamo reindex-blobs` で
旧DBの遡及抽出+FTS再登録ができる（`store.reindex_blob_texts`）。

## 7. アダプタ層とカーソル戦略

**ファイル**: `tamo/adapters/*.py`。全アダプタ共通の約束:
(1) ソースに書かない (2) 壊れた行は `quarantine`+継続 (3) 未知の形は raw温存+`meta`イベント (4) 増分カーソル。

| アダプタ | 実体 | カーソル戦略 | 備考 |
|---|---|---|---|
| `claude_code` | `~/.claude/projects/**/*.jsonl` | **バイトオフセット**（追記専用ログ向き） | 改行で終わらない最終行=書きかけはスキップして次回。CLI/VS Code拡張/JetBrains/Claude Desktop/VS用サードパーティ拡張が**同一保存先** |
| `codex_cli` | `~/.codex/sessions/**/*.jsonl` | バイトオフセット | |
| `aider` | `.aider.chat.history.md` | バイトオフセット | Markdown見出しでターン分割 |
| `cursor_ide` | `state.vscdb` の `cursorDiskKV` | **rowid**（KVは追記的） | 旧`composerData`配列と新`bubbleId`分離の両対応。in-place更新は `--rescan` |
| `generic_jsonl` | 任意（glob+フィールド名を設定） | バイトオフセット | 未対応エージェントの受け皿1 |
| `inbox` | `~/.tamo/inbox/*.json` | 処理後 `done/` へ移動 | 受け皿2。ブラウザ拡張の投函先 |

**inbox v1 形式**（`tamo.inbox.v1`）: `source/session/title/messages[{role,text,ts,attachments[]}]`。
添付は `data_b64` があればCASへ、`url` だけならURLノート、
**`{name, mime, size}` のメタだけでも受けて** `[添付(動画 未取得) … 50.0MB]` として本文に保全する
（メタデータ優先原則。拡張が上限超過で落としたものも文脈は残る）。

## 8. probe — 実機フィンガープリンタ

**ファイル**: `tamo/probe.py`。「どのエージェントにも対応」を仕様書でなく実機で担保する道具。

- **走査対象**: Linux/WSLホーム + WSL2なら `/mnt/c/Users/*`（`TAMO_WIN_ROOT` で差し替え可、テストにも使用）
- **Claude Code系**: `.claude/projects`（WSL/各Windowsユーザー）+ `CLAUDE_CONFIG_DIR`
  （`.claude`ルート指しでも`projects`直指しでも追随）。
  `.vscode-server/extensions/anthropic.claude-code*`（WSLリモート）/`.vscode/extensions`（ネイティブ/Win）を
  検出したら「セッションは上記transcriptに書かれるので追加設定不要」と注記。
  Visual Studio(.NET IDE)の `ClaudeCodeExtension` 痕跡も検出
- **SQLite系(Cursor等)**: スナップショットして**テーブル一覧とキー接頭辞の分布**まで検分
  （`composerData:` が何件、`bubbleId:` が何件…）→ ドリフトしたときに何が変わったか一目でわかる
- **検出のみ**: Windsurf / Gemini CLI / goose / Cline / Copilot Chat は場所を報告するだけ。
  実機のフィンガープリントを見てから `generic_jsonl` 設定か専用アダプタを足す運用
- 結果は `sources.toml` に書き出し（`--write`）。collectはこの設定だけを見る

## 9. optimize — 読出時最適化 P1〜P4

**ファイル**: `tamo/optimize.py`。全段**決定論**（LLM不使用、同入力→同出力）。
「保存はいじらず、読むときに毎回この4段を通す」ので、アルゴリズム改善が過去データにも効く。

| 段 | 名前 | 実装 |
|---|---|---|
| P1 | dedup | 120字以上の**完全一致**再掲を省略参照化（`⟨e:xxxx⟩と同一`） |
| P2 | snapshot折畳 | 同一ファイルへの `tool_result` 群を「最終版フル + 以前は `difflib.unified_diff`」に圧縮（diff上限3500字） |
| P3 | 要点抽出 | 規則ベース正規表現（日英）: 決定/制約・前提/TODO/エラー→修正/触ったファイル の5分類 |
| P4 | 予算詰め | 下記 |

**P4 の選抜ロジック**（`build_pack`）:
- トークン見積り `estimate_tokens`: ASCII単語=1、CJK等の非語文字=1文字1トークンの近似
  （日本語と英語が混ざる実会話で方向が合う、雑だが決定論な見積り）
- TF-IDF: 簡易トークナイザ（ASCII単語 + **CJK文字バイグラム**）でベクトル化
- 基礎スコア = `0.55 × 新しさ((i+1)/n) + 0.45 × cos(TF-IDF, query)`（query無指定なら新しさのみ）
- **MMR** (λ=0.35): `score = base − 0.35 × max(既選抜との類似度)` で冗長な発話を抑制。
  実装は増分max-sim更新のO(n·k)（素朴なO(n·k²)は10万イベントDBで44.7秒→増分化+候補上限800で0.42秒、106倍）。
  ベクトルは事前正規化でコサイン=内積に
- 予算(`--budget`)を超えた時点で打ち切り。要点セクション→会話テールの順に詰める
- 全行に `⟨e:先頭8桁⟩` の出所IDが付き、`events` テーブルの原文へいつでも遡れる

## 10. 配布層 — CLI / HTTP inbox / MCP / export

**CLI**（`tamo/cli.py`, argparse）: `probe / collect / watch / stats / sessions / search /
pack / export / reindex-blobs / mirror / rules / run / serve / mcp / prune / purge /
quarantine / recall / show / token / hook / ingest-hook` の22サブコマンド。
`mirror`（git向けMarkdownミラー, `tamo/derive.py`）と `rules`（導出ルールのマーカー冪等書込）、
`run`（エージェント実行ラッパー）はSpecStory競合調査後に採り入れた還流系。
`serve` は収集スレッド + HTTP inbox + MCP(streamable-http, FastMCP/uvicorn) + 日次自動pruneを
1プロセスに束ねる常駐モード（単体サービスとしての本体）。`mcp` はstdio/HTTPの単体起動。
`show` はセッション単体の表示（`latest`解決・`--tail`・`--since-event`による続き取得）。`prune`/`purge` は保持期間NFRの実装（`~/.tamo/settings.toml` の `[retention] days`、既定0=無期限。
判定はイベント活動時刻でmtime不使用・dry-run必須・無言削除しない — Claude Codeのcleanup事故からの教訓）。
`tamo/redact.py` はコミット/共有前の秘密情報マスク（既知プレフィックス型 + key=value行の保守的マスク、決定論）。
`hook` はClaude Codeの `Stop`/`SessionEnd` フック用スニペットを出力し、
`ingest-hook` がstdinの `transcript_path` を受けて**そのファイルだけ**即時増分取込（`async: true`で本体を塞がない）。

**HTTP inbox**（`tamo/http_inbox.py`, stdlibの`http.server`）:
- `127.0.0.1` バインドのみ（LAN非公開）+ `X-Tamo-Token`（`~/.tamo/inbox.token` 初回自動生成, `secrets`）。
  照合は `secrets.compare_digest`（タイミングセーフ）。`/health` はトークンが付いてきた時だけ検査
  （拡張の「接続確認」が認可までテストできる）
- 認証OK=204 / トークン不一致=403(対処ガイド付き本文) / 非JSON=400 / 上限50MB
- サーバは**検証してファイルに書くだけ**。パースは通常のinboxアダプタに一本化 —
  ネットワークから直接パーサに触らせない（攻撃面の最小化）

**MCP**（`tamo/mcp_server.py`, FastMCP, 任意extras）: 8ツール
`recall（最初にこれ） / search_context / get_context / get_context_pack / list_sessions /
get_session / get_blob_text / get_blob_base64`。
登録は stdio が `claude mcp add tamo -- python -m tamo.mcp_server`（Cursor/Codex CLIも同じ）、
HTTP(streamable-http, `tamo serve`)が `--header "X-Tamo-Token: $(tamo token)"` 付き。
**HTTPは`_TokenGate`(ASGIラッパ)でトークン必須** — 書込(inbox)だけ認証して読出(全会話+blob)が
素通しという非対称を避ける。stdioはクライアント子プロセス=本人なので不要。
`get_blob_text` は添付の抽出テキスト（base64より軽い）、`get_blob_base64` は原物。

**export**: 1行=1セッションの NDJSON（`tamo.session.v1`）。`--include-raw` で
セッションに紐づく原文レコードも同梱 → OmniBrainのchunked distillation→HITLへそのまま流せる。
**tamoが決定論収集、OmniBrainが意味的蒸留**という分業。

## 11. ブラウザ拡張 (MV3)

**ディレクトリ**: `browser-extension/`。Chrome Manifest V3。

```
popup.js ──(scoop要求)──> content/main.js ──> sites/*.js か generic.js
                                │ payload (tamo.inbox.v1)
popup.js <──────────────────────┘
   │ tamo.post
background.js(Service Worker) ──POST──> http://127.0.0.1:8787/inbox
```

| 選定 | 理由 |
|---|---|
| localhost送信は**background SW**で行う | content scriptのfetchはページのCSP/mixed-content(https→http)に引っかかる。SWは `host_permissions`(127.0.0.1のみ)で堂々と送れる |
| claude.ai/ChatGPTは**同一オリジンAPI**方式 | DOMはUI改版のたびに壊れるが、アプリ自身が使うJSON APIは桁違いに安定。claude.ai: `/api/organizations`→`chat_conversations?tree=True&rendering_mode=messages`。ChatGPT: `/api/auth/session`→`backend-api/conversation` の mappingツリーを `current_node` から根へ遡行 |
| 失敗時は**汎用DOMへ自動フォールバック** | payloadに `note: "site adapter failed: …"` を残す。壊れたら直すのは `sites/*.js` 1ファイル |
| Geminiのみ DOM方式 | 会話取得に使える安定JSON APIが無い。`<user-query>`/`<model-response>` カスタム要素を読む |
| 汎用抽出は**3段ヒューリスティック** | ①role属性(`data-message-author-role`等) ②クラス名キーワード(user/assistant/ai…) ③繰り返し兄弟要素を交互推定。最後の砦はmain全文1メッセージ |
| 未対応サイトは**オンデマンド注入** | `activeTab`+`scripting.executeScript`。`<all_urls>` を要求しない=権限最小 |
| 設定は `chrome.storage.sync` | ポート(既定8787)とトークン。popupから保存 |

**添付ポリシー**（`content/lib.js`）: 上限 **6MB/添付・20MB/会話・20個・400メッセージ・10万字/メッセージ**。
- 画像: fetch→b64同梱（48px未満のアイコンは除外）
- **動画/音声: 本体は取らない**。`poster` があればサムネイルだけ同梱し、
  `[添付(動画 未取得) …]` ノートを本文に必ず残す — Geminiの動画添付にも柔軟に対応
- claude.aiのアップロード文書: プラットフォームの**抽出済みテキスト**(`extracted_content`)を同梱
- ChatGPTの画像/ファイル: `files/{id}/download` でURL解決→b64（失敗はノートへ）
- b64化は `TextEncoder`+32KBチャンク（巨大文字列での `btoa` スタック溢れ回避）

## 12. WSL2 / Windows 統合

| 論点 | 対応 |
|---|---|
| Windows側のtranscript | probeが `/mnt/c/Users/*/.claude/projects` を走査（`TAMO_WIN_ROOT`で差替可） |
| `.claude` の移設 | `CLAUDE_CONFIG_DIR` 環境変数に追随（VS/VS Code拡張も同じ場所を読む） |
| ブラウザ(Windows)→tamo(WSL) | WSL2のlocalhostフォワーディング（Win11既定ON）で `127.0.0.1:8787` が届く。`%UserProfile%\.wslconfig` の `localhostForwarding=true` |
| `/mnt/c` のファイル監視 | 9pではinotifyが効かない → `watch` は**ポーリング設計**（`--interval`） |
| `/mnt/c` のSQLiteロック | snapshot_sqlite（コピーしてから読む）で回避 |

## 13. テスト戦略と実測値

**pytest自動テスト**（`tests/test_*.py`, 55+件）+ 生成器方式のfixture（`tests/make_fixtures.py`=
5ソース模擬環境、`tests/make_attachment_fixtures.py`=合成PDF/docx/xlsx）。
CI（`.github/workflows/ci.yml`）は ubuntu/windows × Python 3.11/3.13 のマトリクスで
pytestを回し、拡張JSは `node --check` で構文検証。カバレッジの柱:
冪等性 / encoding耐性(cp932再現) / ロック回収 / CAS自己修復 / inboxのcommit後move /
quarantine運用 / redactパターン / HTTP認可 / MCPツール+認証ゲート(mcp導入時)。
ライブサイトに触れない拡張は**契約テスト**
（lib.jsと同一ロジックでpayload生成→inbox→collect→検索）で担保。

| 検証 | 結果 |
|---|---|
| フレッシュDB E2E（probe→collect） | events=22 / sessions=7 / blobs=4 / blob_texts=3 / quarantine=1 |
| 冪等性 | 2回目collectは全ソース +0 |
| ドリフト実証（正常追記/壊れJSON/未知スキーマの3行追記） | 増分+2 / quarantine+1 / raw温存+metaイベントで**無停止** |
| 添付抽出 | docx-xml 42字（「甲板カバー連動」検索HIT）/ xlsx-xml 20字（嘘mime→magic正判定）/ pdf-naive 64字 / pypdf 53字 |
| メタのみ添付 | `[添付(動画 未取得) berth_trial.mp4 video/mp4 50.0MB]` が本文に保全、種別語「動画」で検索HIT |
| pack | 5ソース18イベント → 1285トークン、全行⟨e:xxxx⟩付き |
| 性能(10万イベント/195MB, tests/bench.py) | 取込8,000件/s / FTS検索0.04〜16ms / tail取得0.1ms / 続き取得4ms / MCP get_session(compact) 3ms / pack(5,000ev) 0.42s |
| HTTP inbox | 204(正token) / 403(偽) / 400(非JSON) → 取込確認 |
| MCP | 8ツールの直接呼出し + list_tools登録確認 |
| 拡張 | 全JS `node --check` PASS / manifest妥当 / 契約テスト（添付テキスト"JIS Z 3183"検索HIT・未取得ノート保全） |

## 14. 壊れたらどこを直すか（早見表）

| 症状 | まず見る | 直す場所 |
|---|---|---|
| `tamo stats` の quarantine が増えた | `quarantine` テーブルの error と原文 | 該当 `tamo/adapters/*.py` の寛容パーサ |
| Cursorの新形式で events が増えない | `tamo probe` のキー接頭辞分布 | `adapters/cursor_ide.py` |
| Claude Code transcriptの形式変更 | quarantine + probeのtranscript件数 | `adapters/claude_code.py`（公式にも「内部形式はバージョン間で変わる」旨あり） |
| claude.ai/ChatGPTで「掬う」失敗 | popupの `⚠ site adapter failed: …` | `browser-extension/content/sites/*.js`（その間もgeneric DOMで動き続ける） |
| 新しいAIチャットサイト対応 | genericで掬えるか試す | `sites/` に1ファイル追加 + manifest の matches |
| 未知のCLIエージェント | `tamo probe` の detected_only | `sources.toml` に `generic_jsonl` 設定 or アダプタ追加 |
| 日本語PDFが検索に載らない | `blob_texts.extractor` が空か | `pip install pypdf` → `tamo reindex-blobs` |
| 添付の抽出器を改善した | — | `tamo reindex-blobs` で遡及再抽出 |

## 15. 用語集

| 語 | 意味 |
|---|---|
| CES | Canonical Event Schema。全ソース共通の正規化イベント形式（v1） |
| CAS | Content-Addressed Storage。sha256でアドレスするblob格納。重複排除が自動で付いてくる |
| FTS5 / trigram | SQLiteの全文検索拡張 / 3文字窓トークナイザ。日本語部分一致の要 |
| WAL | Write-Ahead Logging。読み書き並行とクラッシュ耐性のためのSQLiteジャーナルモード |
| TF-IDF / MMR | 語の希少性重み / Maximal Marginal Relevance（関連性と多様性のバランス選抜） |
| quarantine | パース不能データの隔離場所。原文+エラーを保持し、後日アダプタ修正で救出できる |
| HITL | Human-in-the-Loop。tamoは対象外（決定論収集）で、下流OmniBrainの承認工程を指す |
| MV3 | Chrome拡張 Manifest V3。Service Worker常駐+宣言的権限モデル |
| inbox v1 | `tamo.inbox.v1`。ブラウザ拡張等がtamoへ投函するJSON契約 |
| locator | 原文の出所座標（例 `path::byte-offset` / `inbox::file.json`）。raw_recordsのUNIQUEキー |
