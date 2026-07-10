# tamoチュートリアル — 15分でエージェント横断リコールまで

[English](../TUTORIAL.md)

インストールから「**別のツール**でした会話を、いま使っているエージェントが
『あの件どうなってた？』で引ける」状態までを通しで体験します。

前提: Python 3.11+ と pip、ブラウザ編はChrome。
Windows（ネイティブ/WSL2）・macOS・Linuxで動きます。

---

## 0. AI履歴がまだ無い？ — サンドボックスで試す

以下の全手順は**生成したデモ環境**でも動きます。実データを触らずに試せます:

```bash
python tests/make_fixtures.py /tmp/demo/home /tmp/demo/state
export TAMO_HOME=/tmp/demo/state          # PowerShell: $env:TAMO_HOME="..."
tamo probe --home /tmp/demo/home --write  # 通常の `tamo probe --write` の代わり
```

Claude Code / Cursor / Codex CLI / aider / ブラウザの擬似データが作られます。
試し終わったら `TAMO_HOME` を外して、実環境でやり直してください。

## 1. インストールと初回収集

```bash
git clone https://github.com/HaluHanabusa/tamo && cd tamo
pip install -e ".[mcp]"     # 収集自体は依存ゼロ。[mcp] は配布側の追加
tamo probe --write          # 環境走査 → ~/.tamo/sources.toml 生成
tamo collect                # 全ソースを増分・冪等に収集
tamo stats
```

`probe` は見つかったもの（Claude Codeのtranscript、CursorのDB…）と「検出のみ」
（設定が要るツール）を報告します。`collect` はディスクにある分を全部バック
フィルし、以後は増分だけ読みます。2回実行してみてください — 2回目は `+0` です
（全イベントが決定論的IDを持つため）。

`tamo stats` はこんな形になります:

```json
{ "events": 1234, "sessions": 42, "quarantine": 0,
  "per_source": { "claude_code": 900, "cursor_ide": 300, "...": 34 } }
```

> `quarantine` はパースできなかった行の隔離場所 — **原文ごと**保持され、
> 決して捨てられません。ツールの更新後に増えたら `tamo quarantine` で
> 何が変わったか見られます。

## 2. 検索とリコール

```bash
tamo search "スナップショット"        # 全文検索（日本語の部分一致OK）
tamo search "トルク" --source cursor  # ツールで絞る
tamo recall "収集器の設計どうなってた"
```

`search` は `e:<id>` の出所ポインタ付きでヒット行を返します。`recall` は一発版:
該当セッションを探し、「★一致箇所 + 要点 + どう終わったか」を1つのMarkdownに
合成します。全行に `⟨e:xxxx⟩` が付くので、いつでも原文に遡れます
（`tamo show <セッション>` / `--since-event`）。

`tamo recall ... --copy` で結果がクリップボードに入ります — どのチャットにも貼れます。

## 3. 引き継ぎパックを作る

```bash
tamo pack --budget 6000 --query "収集器 設計" --out pack.md
```

決定論の4段最適化（dedup → スナップショット折畳 → 要点抽出 → トークン予算内
選抜）が走り、指定予算に収まるMarkdownパックができます。新しいセッションの
冒頭に貼ってください。

## 4. サービスとして動かす（推奨）

```bash
tamo serve
```

1プロセスで 定期収集 + ブラウザ投函口 + MCPサーバ + 日次prune が立ちます。
起動バナーにコピペ可能な登録コマンドが出ます:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp --header "X-Tamo-Token: <あなたのトークン>"
# stdio派（Cursor / Codex CLIも同形。トークン不要）:
claude mcp add tamo -- tamo mcp
```

登録できたらClaude Codeに「**あの件どうなってた？**」と聞いてみてください —
`recall` ツールが呼ばれます。`tamo hook` が出すスニペットをCLAUDE.mdに貼ると
エージェントがより自発的にtamoを引くようになり、同コマンドが出すフック設定で
ターン毎のリアルタイム取込もできます。

常駐化はWSL2/Linuxなら `systemd --user`、Windowsならタスクスケジューラに
`tamo serve` を1行です。

## 5. ブラウザの会話を掬う

1. `chrome://extensions` → デベロッパーモードON → 「パッケージ化されていない
   拡張機能を読み込む」→ `browser-extension/` を選択
2. `tamo serve` を起動した状態で拡張のpopupを一度開く → **自動ペアリング**
   （トークンがズレたら「再ペアリング」ボタン）
3. claude.ai / ChatGPT / Gemini / 任意のチャットサイトで右上の🎣をクリック
   （popupの「この会話を掬う」でも同じ）

逆方向もできます: popupの「検索してコピー」に語を入れて、過去の文脈を
Webチャットに貼り込めます。

## 6. 履歴をリポジトリへ還流する

```bash
tamo mirror --project myapp        # セッション → ./.tamo/history/*.md（秘密情報は既定でマスク）
tamo rules --write CLAUDE.md       # 決定/制約/エラー対処 → CLAUDE.mdのマーカー区間へ
```

`mirror` の出力はコミットでき、PRでレビューできます（マスクは既定ON、
`--no-redact` で原文）。`rules` は自分のマーカー区間だけを書き換えるので、
手書きのCLAUDE.mdを壊しません。

## 7. お手入れ

```bash
tamo stats                       # サイズ・最古イベント・隔離件数
tamo prune --days 90 --dry-run   # 何が消えるか事前確認（活動時刻基準）
tamo prune --days 90             # 削除前に確認プロンプト
tamo quarantine                  # パース不能行の点検（増加=形式ドリフトの兆候）
tamo purge --yes                 # 全データ削除（設定とトークンは残る）
```

既定の保持は**無期限**です — tamoはソース側の自動削除より長く残すために
あります。何日が適切かは [ARCHITECTURE.md](ARCHITECTURE.md) の保存期間リスクの
節を読んでから決めてください。仕事マシンでは `~/.tamo/settings.toml` に
`[retention] days = 90`（または社内規程の日数）を推奨します。

## 8. 困ったら

| 症状 | 対処 |
|---|---|
| `ポート 8787/8788 は使用中` | 別のtamoが動作中。または `--inbox-port` / `--mcp-port` で変更 |
| 拡張が `403` | popupの**再ペアリング**を押す |
| `別のtamoが実行中 (PID …)` | 生きているプロセスがロック保持中。残留ロックは自動回収されます |
| 添付の中身が検索に出ない | `pip install pypdf` → `tamo reindex-blobs` |
| `quarantine` が増える | `tamo quarantine` と `tamo probe` の指紋を確認してissueへ |
