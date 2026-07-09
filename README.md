# tamo 🎣 — AIエージェント横断のコンテキスト収集器

Claude Code / Cursor / Codex CLI / aider / ブラウザのAIチャット（claude.ai・ChatGPT・Gemini…）に
散らばる会話を、**1つのローカルDBへ自動で貯めて、横断検索して、次のエージェントへ引き継ぐ**ツールです。

- **消える前に貯める** — 各ツールのログは無保証で、例えばClaude Codeは既定30日で自動削除されます。
  tamoはソースより長く残す「受け皿」です（既定は無期限保存）
- **どこからでも探せる** — 日本語部分一致の全文検索。添付（PDF / Word / Excel / テキスト）の中身までヒット
- **そのまま引き継げる** — MCPで各エージェントから直接引ける / トークン予算内のMarkdownパックを貼れる
- **完全ローカル・依存ゼロ** — 標準ライブラリのみ・LLM不使用の決定論処理。データはあなたのPCから出ません

## インストール 〜 最初の5分

```bash
pip install -e .            # 収集だけなら依存ゼロ。常駐サービス(MCP配布)まで使うなら pip install -e ".[mcp]"
tamo probe --write          # 環境を自動走査して ~/.tamo/sources.toml を生成
tamo collect                # 全ソースを増分収集（何度実行しても安全＝冪等）
tamo stats                  # 何がどれだけ入ったか確認
tamo search "スナップショット"   # 日本語部分一致で全文検索
```

WSL2・ネイティブWindows・macOS・Linuxで動きます。データは `~/.tamo`（環境変数 `TAMO_HOME` で変更可）
だけに書き、**収集元のファイルには一切書き込みません**。

## ふだんの使い方

| やりたいこと | コマンド |
|---|---|
| 「あの件どうなってた？」を一発調査 | `tamo recall "甲板カバー 連動"`（`--copy` でクリップボードへ） |
| 全文検索（添付の中身も対象） | `tamo search "語1 語2" --source gemini` |
| 最新セッションの続きを見る | `tamo show latest --tail 5` |
| 引き継ぎパックを作る | `tamo pack --budget 6000 --query "設計" --out pack.md` |
| 履歴をgitにコミットできる形に | `tamo mirror --project myapp`（秘密情報は既定でマスク） |
| 決定・制約をCLAUDE.mdへ還流 | `tamo rules --write CLAUDE.md` |
| パースできなかった行の確認 | `tamo quarantine`（増えていたらソース側の形式変更の兆候） |

## 常駐させる — `tamo serve`（推奨）

1コマンドで「定期収集 + ブラウザ投函口 + MCPサーバ + 日次prune」が全部立ちます:

```bash
tamo serve
```

エージェントへの登録は、起動バナーに出るコマンドをコピペするだけ:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: $(tamo token)"
# stdio派（Cursor / Codex CLI等も同じ。トークン不要）: claude mcp add tamo -- tamo mcp
```

登録後はエージェントに「あの件どうなってた？」と聞くだけでtamoが引かれます
（`tamo hook` が出力するCLAUDE.mdスニペットを貼ると、より自発的に使うようになります）。
常駐化はWSL2なら `systemd --user`、Windowsならタスクスケジューラに `tamo serve` を1行です。

- ターン毎のリアルタイム取込: `tamo hook`（Claude Code等のフック設定を表示）
- フック機構が無いCLI: `tamo run -- <コマンド>`（終了時に自動で増分収集）

## ブラウザの会話も掬う — 同梱拡張「tamo scoop」

1. `chrome://extensions` → デベロッパーモードON → 「パッケージ化されていない拡張機能を読み込む」→ `browser-extension/`
2. `tamo serve` を起動した状態で拡張のpopupを一度開く → **自動ペアリング**完了
3. claude.ai / ChatGPT / Gemini / その他サイトの会話ページで、画面右上の🎣をワンクリック

過去の文脈を**ブラウザAIに渡す**こともできます（popupの「検索してコピー」→ 入力欄に貼るだけ）。
詳細・トラブル対処は [browser-extension/README.md](browser-extension/README.md) へ。

## データの場所・保持・削除

- 実体は `~/.tamo/tamo.db`（SQLite 1ファイル）と `~/.tamo/cas/`（添付）。バックアップはフォルダごとコピーでOK
- 既定は**無期限保存**。`~/.tamo/settings.toml` の `[retention] days = 30` を設定すると常駐時に日次で自動prune
- 手動削除は `tamo prune --days N`（確認プロンプト付き・`--dry-run` で事前確認）、全消去は `tamo purge --yes`
- `tamo stats` がDBサイズ・最古イベント・隔離件数を常時可視化

## 対応ソース

| ソース | 実体 |
|---|---|
| Claude Code（CLI / VS Code / JetBrains / Claude Desktop） | `~/.claude/projects/**/*.jsonl` |
| Cursor | `state.vscdb`（スナップショットコピーしてから読む） |
| Codex CLI | `~/.codex/sessions/**/*.jsonl` |
| aider | `.aider.chat.history.md` |
| ブラウザ（claude.ai / ChatGPT / Gemini / 任意サイト） | 同梱拡張 → HTTP inbox |
| 任意のJSONL吐きエージェント | `generic_jsonl`（sources.tomlに数行書くだけ） |

Windsurf / Cline / Copilot Chat 等は `tamo probe` が場所を報告します（実機の形式を見てから対応する方針）。

## 制限（正直な話）

- 各ツールのログ形式は非公開で予告なく変わります。tamoは「落ちない・失わない」を保証しますが
  （壊れた行は原文ごと `tamo quarantine` へ隔離）、「常に完全にパースできる」は保証しません
- 導入前にソース側の自動削除で消えた過去には遡れません — だからこそ早めに `tamo serve` を
- DBは平文です。OSのディスク暗号化（BitLocker / FileVault）との併用を前提にしています

## もっと詳しく

| ドキュメント | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 設計原則・データモデル・最適化・還流・セキュリティ(NFR)の解説 |
| [docs/TECH_STACK.md](docs/TECH_STACK.md) | 技術選定の理由と実装ファイルの対応表 |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | 実機での動作確認手順 |
| [browser-extension/README.md](browser-extension/README.md) | ブラウザ拡張の詳細（対応サイト・添付ポリシー・ドリフト時の挙動） |

MIT License
