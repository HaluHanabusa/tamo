# tamo 実機確認手順書

対象環境: **Windows 11 + WSL2 (Ubuntu)** / CLI: **Claude Code** / ブラウザ: **claude.ai・Gemini・ChatGPT**（Chrome または Edge）
所要時間目安: 40〜60分（Phase 1〜4が本体。5以降は任意）

> 検証の基本テクニック: 各面の会話に**一意のマーカー文字列**（例 `tamo検証CLI-001`）を発言として仕込み、
> あとで `tamo search` で拾えるかを合否判定にします。どの面から来たかが session_key で分かります。

---

## Phase 0. インストール（WSL2側）

```bash
cd ~ && unzip tamo.zip && cd tamo
python3 -m venv ~/.venvs/tamo
source ~/.venvs/tamo/bin/activate
pip install -e ".[mcp,pdf]"        # mcp=MCPサーバ用 / pdf=日本語PDF抽出用(任意)
tamo --help
```

| # | 確認 | 期待結果 | ✓ |
|---|---|---|---|
| 0-1 | `tamo --help` | probe/collect/serve/search/show/pack/... のサブコマンド一覧が出る | ☐ |
| 0-2 | `python3 -c "import sqlite3;print(sqlite3.sqlite_version)"` | **3.34以上**（trigram日本語検索の要件。未満でも動くが検索がLIKE縮退） | ☐ |

> venvを使わない場合は `pip install -e ".[mcp,pdf]" --break-system-packages`。
> 以降のコマンドは venv を activate したシェルで実行してください。

## Phase 1. Claude Code (CLI) の収集

**1-1. マーカーを仕込む**: 普段のプロジェクトで Claude Code を開き、
`このセッションはtamo検証CLI-001のテストです。決定: マーカー方式で検証する。`
と発言してセッションを1往復以上進め、終了する。

**1-2. 検出と収集**:

```bash
tamo probe --write     # 環境走査 → ~/.tamo/sources.toml
tamo collect
tamo collect           # ←2回目
tamo stats
tamo search "tamo検証CLI-001"
tamo show latest:claude_code --tail 3
```

| # | 確認 | 期待結果 | ✓ |
|---|---|---|---|
| 1-1 | probe出力 | `claude_code[wsl]: ~/.claude/projects (transcript N件)` が出る。VS Code拡張があれば「追加設定不要」注記 | ☐ |
| 1-2 | collect 1回目 | `raw+N events+M` で取込（既存履歴の量に比例） | ☐ |
| 1-3 | collect 2回目 | **全ソース +0**（冪等性） | ☐ |
| 1-4 | search | CLI-001 のマーカー発言がヒット、session_key が `claude_code:...` | ☐ |
| 1-5 | show latest | 直近セッションの末尾3件が ⟨e:xxxx⟩ 付きで表示 | ☐ |

## Phase 2. 単体サービス起動と MCP 接続

**2-1. serve 起動**（別ターミナル。以後つけっぱなし）:

```bash
source ~/.venvs/tamo/bin/activate
tamo serve
```

**2-2. Claude Code に登録**（元のターミナル）:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: $(tamo token)"
tamo hook   # 出力の CLAUDE.md スニペットを対象プロジェクトの CLAUDE.md に貼る(推奨)
```

> HTTPはトークン認証（serve起動バナーにコピペ可能な登録例が出ます）。ヘッダ無しは401になります。

**2-3. Claude Code 内から呼ぶ**: 新しいセッションで次を順に発言:

1. `tamoのsearch_contextで「tamo検証CLI-001」を検索して、結果をそのまま見せて`
2. `この前話してたtamo検証CLI-001の件、どうなってたか tamo で調べて`

| # | 確認 | 期待結果 | ✓ |
|---|---|---|---|
| 2-1 | serve起動表示 | inbox(8787)/mcp(8788)のURLと登録例が表示され、常駐する | ☐ |
| 2-2 | `claude mcp list` | tamo が connected | ☐ |
| 2-3 | 発言1 | search_context ツール呼び出しが走り、CLI-001 のヒットが返る | ☐ |
| 2-4 | 発言2 | search → **get_context**（前後の顛末）の連鎖で「決定: マーカー方式で検証する」まで辿って回答 | ☐ |
| 2-5 | ライブ収集 | 2-3のセッション自体が約60秒後に `tamo search "そのまま見せて"` でヒット（serveの自動収集） | ☐ |

> stdio派の代替: `claude mcp add tamo -- ~/.venvs/tamo/bin/tamo mcp`（絶対パス推奨）

## Phase 3. ブラウザ拡張「tamo scoop」（Windows側）

**3-1. 読み込み**: Chrome/Edge → `chrome://extensions`（Edgeは `edge://extensions`）→
デベロッパーモードON → 「パッケージ化されていない拡張機能を読み込む」→
`\\wsl.localhost\Ubuntu\home\<ユーザー名>\tamo\browser-extension` を指定
（WSLパスで読めない場合はフォルダをWindows側にコピー）。

**3-2. ペアリング**: serve稼働中に拡張popupを開くだけで**自動ペアリング**されます
（「tamoと自動ペアリングしました ✓」表示）。手動で貼る場合は `tamo token` の出力を使用。

**3-3. 疎通の切り分け**（うまくいかないとき）:

```bash
# (a) Windows PowerShell: curl.exe http://127.0.0.1:8787/health → ok なら経路OK
# (b) WSL側から拡張を介さず投函（サーバ+トークンの単体テスト）:
TOKEN=$(tamo token)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8787/inbox \
  -H "X-Tamo-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"schema":"tamo.inbox.v1","source":"curl_test","session":"t1","messages":[{"role":"user","text":"tamo検証CURL-001"}]}'
# → 204 が出て tamo search "tamo検証CURL-001" が当たればサーバ側は健全。残る容疑は拡張のみ
# (c) 拡張: popupの「接続確認」がOKか / 会話URL上か / タブをリロードしたか / statusの文言を記録
```

**3-4. 3サイトで掬う**: 各サイトで会話を作り、**画面右上の🎣ボタン**（または popupの「この会話を掬う」）:

| サイト | 会話内容（例） | 期待動作 |
|---|---|---|
| claude.ai | `tamo検証WEB-CLA-001。この文章を覚えておいて` ＋ **docxかPDFを1つ添付** | 抽出OK (claude.ai) / attachments≥1 |
| ChatGPT | `tamo検証WEB-GPT-001` ＋ 画像を1枚添付 | 抽出OK (chatgpt.com) |
| Gemini | `tamo検証WEB-GEM-001` | 抽出OK (gemini.google.com)。DOM方式なので `⚠...generic(fallback)` でも**正常系** |

**3-5. WSL側で確認**（serve稼働中なら約60秒待つだけ。急ぐなら `tamo collect --only inbox`）:

```bash
tamo search "tamo検証WEB-CLA-001"
tamo search "tamo検証WEB-GPT-001"
tamo search "tamo検証WEB-GEM-001"
tamo stats
```

| # | 確認 | 期待結果 | ✓ |
|---|---|---|---|
| 3-1 | popup接続確認 | 「tamoに接続OK」 | ☐ |
| 3-2 | 3サイトの掬い | それぞれ「投函完了 ✓ (claude:... / chatgpt:... / web:...)」 | ☐ |
| 3-3 | search×3 | 各マーカーがヒットし、session_key の接頭辞で出所が分かる | ☐ |
| 3-4 | 添付 | statsの `blobs` が増加。claude.aiのdocx/PDFは `tamo search "<文書内の語>"` で**中身がヒット** | ☐ |
| 3-5 | 未取得の保全 | 大きすぎる/取れない添付があった場合、本文に `[添付(種別 未取得) 名前 ...]` が残る | ☐ |

## Phase 4. 横断引き継ぎ（本来のゴール）

Claude Code の**新しいセッション**で:

1. `tamoで「tamo検証WEB-CLA-001」を検索して、どの面の会話か教えて`
   → **ブラウザ(claude.ai)の会話がCLIから引ける**ことを確認
2. `get_context_packをquery="tamo検証"で呼んで、要点を貼って`
   → CLI/ブラウザ横断の決定事項が ⟨e:xxxx⟩ 付きで返る

| # | 確認 | 期待結果 | ✓ |
|---|---|---|---|
| 4-1 | 面越え検索 | claude.ai 由来のマーカーが Claude Code から取得できる | ☐ |
| 4-2 | pack | 複数面の内容が1つのパックに混ざって出る | ☐ |

## Phase 5. 還流（任意）

```bash
cd <普段のプロジェクト>
tamo rules --project <プロジェクト名の一部> --write CLAUDE.md
tamo mirror --project <同上> --redact --out .tamo/history
tamo run -- claude   # 1コマンド差し替えで終了時自動収集
```

期待: CLAUDE.md にマーカー区間 `<!-- tamo:rules:begin/end -->` が追記され再実行しても1個のまま /
`.tamo/history/*.md` が生成されAPIキー等は `[REDACTED:...]` にマスク。

## Phase 6. NFR（任意）

```bash
tamo prune --days 365 --dry-run   # 何が消える対象かの事前確認（実行はしない）
cat ~/.tamo/settings.toml          # 保持期間の設定場所
tamo stats                         # db_bytes / oldest_event
```

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| probeがclaude_codeを見つけない | `ls ~/.claude/projects` を確認。`CLAUDE_CONFIG_DIR` を使っている場合はそのままprobeが追随するはず。Windows側のみ使用なら `/mnt/c/Users/<name>/.claude/projects` が出ているか確認 |
| 検索が日本語で当たらない | Phase 0-2 のSQLiteバージョン確認。3.34未満はOSのsqlite更新を検討 |
| 拡張で「site adapter failed → generic」 | 非公開APIの仕様変更。**generic fallbackで内容が取れていれば合格**。直すのは `browser-extension/content/sites/該当.js` のみ |
| ChatGPTで抽出失敗 | ログイン状態と、URLが `/c/<id>` の会話ページであることを確認 |
| serveのポート衝突 | `tamo serve --mcp-port 8790 --inbox-port 8791` で回避（登録URLも変更） |
| quarantineが増える | `tamo stats` で件数、原文は隔離済みで無損失。`probe` のフィンガープリントを添えて報告してもらえれば該当アダプタを直します |

## 結果の報告テンプレ

```
Phase 1: 1-1[ ] 1-2[ ] 1-3[ ] 1-4[ ] 1-5[ ]
Phase 2: 2-1[ ] 2-2[ ] 2-3[ ] 2-4[ ] 2-5[ ]
Phase 3: 3-1[ ] 3-2[ ] 3-3[ ] 3-4[ ] 3-5[ ]  (CLA/GPT/GEMの内訳: )
Phase 4: 4-1[ ] 4-2[ ]
気づき・エラー全文・probe出力:
```
