# tamo 🎣 — AIエージェント横断のコンテキスト収集器

**タモ網**のように、Claude Code / Cursor / Codex CLI / aider / ブラウザ上のAIチャットから
会話コンテキストを**決定論的に**掬い上げ、正規化し、
**MCP経由でどのエージェントにも引き継ぐ、単体で完結するローカルサービス**です。
依存ゼロ（標準ライブラリのみ）で動き、蒸留や外部サービスは不要 —
横断は生のまま（search / get_session）でき、pack等の最適化は任意の読出時ビューです。
OmniBrain等の下流連携は `export` によるオプションであり前提ではありません。

```
[エージェント達]                 [tamo]                            [引き継ぎ先]
Claude Code transcript ─┐   probe: 自動検出
Cursor state.vscdb ─────┤   collect: 増分・冪等・無損失      ┌→ MCP server (search/pack/blob)
Codex CLI rollout ──────┼→  CES正規化 + CAS(blob重複排除) ──┼→ tamo pack (Markdown貼り付け)
aider history.md ───────┤   SQLite + FTS5(日本語trigram)     └→ export NDJSON (OmniBrain等)
ブラウザ(MV3拡張) ──────┘   optimize: dedup/diff折畳/要点抽出
```

## 設計原則

1. **取込は無損失、最適化は読出時** — 原文(raw)は必ずSQLiteに保存し、要約・圧縮は
   いつでも再構築できる「ビュー」として生成する（OmniBrain ADR-024と同じ思想）。
2. **収集・保存にLLMを使わない** — event_idの決定論的生成、規則ベースの要点抽出、
   TF-IDF+MMRの文選抜まで全て決定論。意味的な蒸留は下流(OmniBrainのHITL)の仕事。
3. **ドリフト前提** — 各社のログ形式は非公開・無保証。壊れた行はquarantineへ、
   未知の将来スキーマはraw温存+metaイベントで**絶対に落ちない**。
4. **添付はメタデータ優先** — 中身を読み切れなくても「何が添付されたか
   （種別・名前・サイズ）」を本文に必ず残す。前後の文脈が取れていれば
   動画やCID埋込PDFのように読めない添付があっても作業内容は伝わる。
   テキスト抽出はあくまでボーナス。

## クイックスタート

```bash
pip install -e .            # 収集系は依存ゼロ。MCP配布は pip install -e ".[mcp]"
tamo probe --write          # 環境走査 → ~/.tamo/sources.toml 自動生成
tamo collect                # 全ソース増分収集（何度実行しても冪等）
tamo stats                  # 収集状況
tamo search "スナップショット"   # FTS5 trigram: 日本語部分一致OK
tamo pack --budget 6000 --query "収集器 設計"   # 引き継ぎパック生成
```

`TAMO_HOME`（既定 `~/.tamo`）配下に `tamo.db` / `cas/` / `inbox/` が作られます。
ソースには**一切書き込みません**（SQLiteはWALチェックポイント込みでスナップショットコピーしてから読む）。

## 対応ソース

| kind | 実体 | 増分方式 |
|---|---|---|
| `claude_code` | `~/.claude/projects/**/*.jsonl` | バイトオフセット（書込途中の最終行はスキップ） |
| `cursor_ide` | `state.vscdb` の `cursorDiskKV`(composerData/bubbleId) | rowidカーソル。in-place更新は `--rescan cursor_ide` |
| `codex_cli` | `~/.codex/sessions/**/*.jsonl` | バイトオフセット |
| `aider` | `.aider.chat.history.md` | バイトオフセット |
| `generic_jsonl` | 任意エージェント（glob+フィールド名を設定） | バイトオフセット |
| `inbox` | `~/.tamo/inbox/*.json`（下記v1形式） | 処理後 `done/` へ移動 |

`tamo probe` はWSL2なら `/mnt/c/Users` のWindows側も走査し、Windsurf / Gemini CLI /
goose / Cline / Copilot Chat は「検出のみ」報告します（実機フィンガープリントを見て
`generic_jsonl` 設定か専用アダプタ追加で対応する方針）。

### セッションの扱い

- **単位**: `session_key = source:ネイティブセッションID`。イベントは元ファイルの順序(`seq`)と時刻で並び、
  `tamo sessions` / `list_sessions` は最終活動の新しい順
- **途中からの収集**: カーソルはバイトオフセットなので、導入時点で既存ファイル全体をバックフィルし、
  以降は増分だけ読む。resume済み・compact済みで**途中から始まっているtranscriptもあるがまま**取り込む
  （無損失=「ディスクにあるものは全部、無いものは仮定しない」。導入前にソース側の自動削除で
  消えた過去には遡れない — だからこそ早めに `serve` を常駐させる）
- **時系列は二軸で保存**: 全イベントが `session_key`(所属)・`seq`(セッション内順序)・`ts`(時刻,
  インデックス付き)を持ち、セッション表が `first_ts/last_ts` を集約。ts無しのDOM取得も
  拡張の `captured_at` で時間軸に補完される
- **話題が複数セッションに跨る場合**: `recall` は各セッションのダイジェストを**最新優先**で並べ、
  冒頭に「🕒 3セッションに跨る: 07-01 → 07-05 → 07-08」の変遷サマリを付ける
  （①=現在の状態。予算超過時も最新から残る）
- **途中から・最新だけの取得**: `tamo show latest --tail 5` /
  `tamo show <key> --since-event ⟨e:xxxx⟩の8桁`。MCPの `get_session` も同じ引数を持ち、
  `session_key="latest:claude_code"` のような解決や、packに載っている ⟨e:xxxx⟩ を起点に
  「続きだけ」を渡す引き継ぎができる。不明なIDには空を返す（全量の誤送を防ぐ安全側）

### VS Code / Visual Studio の Claude Code 拡張

CLI・VS Code拡張・JetBrains拡張・Claude Desktopのコーディング統合は、**すべて同じ
`~/.claude/projects`（Windowsは `%USERPROFILE%\.claude\projects`）にJSONL transcriptを書く**ため、
tamoの `claude_code` アダプタがそのまま吸えます。Visual Studio(.NET IDE)向けの
サードパーティ拡張(dliedke)も同じ場所（または `CLAUDE_CONFIG_DIR`）を読み書きします。

- `tamo probe` は `.vscode-server/extensions`（WSLリモート）と `.vscode/extensions`（ネイティブ/Windows側）の
  `anthropic.claude-code*` を検出して「追加設定不要」と報告します
- `CLAUDE_CONFIG_DIR` で `.claude` を移動している場合も probe が自動で追随します（projects直指しも可）
- 公式ドキュメントにある通り transcript の内部形式はバージョン間で変わる前提です。
  tamoは寛容パーサ + quarantine + raw温存で「落ちない・失わない」を守ります

## リアルタイム取込（フック）

```bash
tamo hook          # 設定スニペット表示
```

Claude Code の `Stop` / `SessionEnd` フックは stdin で `transcript_path` を渡してくるので、
`tamo ingest-hook` が**そのファイルだけ**即時増分取込します（`async: true` で本体を
ブロックしない）。Codex CLI・Cursorの同型フックにも流用できます。常駐なら:

```bash
tamo watch --interval 60 --http   # ポーリング収集 + HTTP inbox同時起動
```

WSL2の `/mnt/c` 配下はinotifyが効かないため、watchはポーリング設計です。

## ブラウザ会話の投函（HTTP inbox）

`tamo watch --http` は `127.0.0.1:8787/inbox` を開きます（トークンは `~/.tamo/inbox.token`、
初回自動生成）。MV3拡張やユーザースクリプトから:

```js
fetch("http://127.0.0.1:8787/inbox", {
  method: "POST",
  headers: { "X-Tamo-Token": TOKEN, "Content-Type": "application/json" },
  body: JSON.stringify({
    schema: "tamo.inbox.v1",
    source: "chatgpt_web",            // 任意のソース名
    session: "conv-abc123",           // 会話ID
    title: "任意タイトル",
    messages: [
      { role: "user", text: "...", ts: "2026-07-07T09:00:00Z",
        attachments: [{ name: "memo.txt", mime: "text/plain", data_b64: "..." }] },
      { role: "assistant", text: "..." }
    ]
  })
});
```

サーバは**検証してファイルに書くだけ**で、パースは通常のinboxアダプタに一本化しています
（攻撃面の最小化）。

### 同梱のブラウザ拡張 `browser-extension/`（tamo scoop）

上記v1形式を組んで投函するMV3拡張を同梱しています（`chrome://extensions` → 読み込み）。

- **claude.ai / ChatGPT**: DOMではなく**アプリ自身が使う同一オリジンAPI**から会話JSONを取得
  （UI改版に強い）。失敗時は汎用DOM抽出へ自動フォールバックし、`note`に理由を残す
- **Gemini**: `user-query`/`model-response` 要素のDOM読み
- **その他の任意サイト**: popupの「掬う」でその場注入される汎用ヒューリスティック
- **遅延読込対応**: DOM方式(Gemini/汎用)は掬う前に会話を**自動で最上部までスクロール**して
  過去分を展開してから抽出（伸びが止まるまで最大25回。長い会話は十数秒かかることがある）
- **再掬いは差分だけ**: メッセージIDは位置でなく内容ハッシュなので、同じ会話を何度掬っても
  既知の発言は増えず新しい分だけ追加される（スクロール位置がズレていても安全）
- **添付**: 画像/ファイルはb64同梱（6MB/添付・20MB/会話）。claude.aiのアップロード文書は
  **プラットフォーム抽出済みテキスト**を同梱。取れないものは
  `[添付(未取得): 名前 mime サイズ]` を本文に必ず残す — 情報を黙って落とさない

詳細は `browser-extension/README.md`。

## オブジェクト（画像・PDF・Office添付）の扱い — CAS + 決定論テキスト抽出

貼付画像や添付のbase64は取込時に `cas/ab/cd/<sha256>.<ext>` へ吸い出し、会話ログ側は
`[blob image/png 70B sha=c414cd0e204d]` という参照に置換します。

- **重複排除**: 同じスクショ/PDFを何度貼ってもディスクは1回分
- **文脈の肥大防止**: Claude Codeのtranscriptはbase64画像で数MBに膨れがち → 参照化で桁違いに軽く
- **mimeはマジックナンバー優先**: 送信側の申告mimeが嘘でも `PK` ヘッダのZIP内部構造まで見て
  docx/xlsx/pptx を判別（`application/octet-stream` と申告されたxlsxも正しく扱えることを確認済み）。
  動画/音声も mp4/mov/webm/wav/ogg/flac/mp3 をマジックで判定
- **読めない添付も文脈は残る**: 本文には `[添付(動画) clip.mp4 video/mp4 76B sha=…]` の形で
  種別・名前・サイズが必ず入り、種別語（動画/画像/PDF…）やファイル名で検索できる。
  inbox v1はデータ無しのメタだけ添付 `{name, mime, size}` も受け、
  `[添付(動画 未取得) berth_trial.mp4 video/mp4 50.0MB]` として保全する
- **必要時に解決**: MCPの `get_blob_base64(sha)` で原物、`get_blob_text(sha)` で抽出テキスト

### 添付テキスト抽出（textract, すべて決定論・LLM不使用）

| 種別 | 抽出器 | 依存 |
|---|---|---|
| docx / xlsx / pptx | ZIP+XML直読み（`w:t` / `sharedStrings` / `a:t`） | なし（stdlib） |
| PDF | pypdf（あれば。日本語ToUnicode対応）→ 無ければstdlib素朴抽出 | 任意 `pip install pypdf` |
| txt / md / csv / json / html | エンコーディング判定（UTF-8→CP932→UTF-16） | なし |

抽出テキストは `blob_texts` に保存され、**参照イベントのFTSに載る**ため
`tamo search` で「添付の中身」まで日本語部分一致検索できます。
PDFの素朴抽出は16進文字列（CIDフォント＝日本語PDFで化ける原因）を最初から読まず、
品質ゲート（可読文字比率）を通らないテキストは**捨てて誤情報を入れない**設計です。
既存DBや抽出器更新後は `tamo reindex-blobs` で遡及抽出＋FTS再登録できます。

## 最適化パイプライン（`tamo pack`）

読出時に4段の決定論的変換を通します:

- **P1 dedup** — 120字以上の完全一致再掲を省略参照化
- **P2 snapshot折畳** — 同一ファイルのtool_result群を「最終版フル + 以前はunified diff」に圧縮
- **P3 要点抽出** — 規則ベース（日英）で 決定 / 制約 / TODO / エラー→修正 / 触ったファイル を抽出
- **P4 予算詰め** — TF-IDF+MMR(λ=0.35)で会話テールを選抜し、指定トークン予算内のMarkdownに整形。
  全行に `⟨e:xxxx⟩` の出所IDが付き、いつでも原文へ遡れます

## 還流 — 収集した履歴をエージェントに返す

SpecStoryの競合調査から採り入れた「収集→蒸留→還流」ループ（tamoでは全段決定論）:

```bash
tamo mirror --project omnibrain --redact   # セッションを ./.tamo/history/*.md へミラー
tamo rules --project omnibrain --write CLAUDE.md   # 決定/制約/エラー対処をCLAUDE.mdへ還流
tamo watch --interval 60 --rules CLAUDE.md  # 常駐: 新イベント収集のたびにルールも自動再生成
tamo run -- claude                          # エージェントをそのまま実行→終了時に増分収集
```

- **mirror**: gitにコミットでき、PRでAIとの検討経緯をレビューでき、grepできるMarkdown。
  正はtamoのDBで、mirrorは毎回全文再生成されるビュー（手編集しない）。
  `--redact` でAPIキー・トークン・秘密鍵を `[REDACTED:種類]` にマスクしてから書く
- **rules**: P3要点抽出による導出コンテキスト。各行に ⟨e:xxxx⟩ の出所IDが付き、
  `--write` は `<!-- tamo:rules:begin/end -->` マーカー区間だけを冪等更新するので
  CLAUDE.mdの手書き部分を壊さない。**収集器なので人手レビューのゲートは置かない** —
  決定論・出所ID・毎回全再生成という性質自体が安全機構で、誤抽出は出力でなく
  抽出規則を直して再生成する。意味的な蒸留と承認（HITL）は下流OmniBrainの責務
- **run**: フックの無いエージェントでも「1コマンド差し替え」で取込が回るラッパー
- `tamo hook` はCLAUDE.mdに貼る「tamo MCPを能動的に使わせる」スニペットも出力する

## サービスとして動かす — `tamo serve`

```bash
tamo serve            # 収集ポーリング + HTTP inbox + MCP(streamable-http) + 日次自動prune を1プロセスで
```

起動後にやることは登録だけ:

```bash
claude mcp add --transport http tamo http://127.0.0.1:8788/mcp   # Claude Code
# stdio派（クライアントが子プロセス起動）: claude mcp add tamo -- tamo mcp
```

ポート・間隔は `~/.tamo/settings.toml` で変更できます。常駐化は
WSL2なら `systemd --user`、Windowsならタスクスケジューラに `tamo serve` を1行登録するだけです。
収集が失敗してもサービス全体は止まりません（エラーはstderrに出して継続）。

## MCPで配る

```bash
pip install -e ".[mcp]"
claude mcp add tamo -- python -m tamo.mcp_server   # Cursor/Codex CLI等も同じstdioコマンド
```

ツール: `search_context` / `get_context_pack` / `list_sessions` / `get_session` / `get_blob_base64`。
新しいセッション冒頭で `get_context_pack(query="...")` を呼べば、別エージェントでの
作業文脈をそのまま引き継げます。

## OmniBrain連携

```bash
tamo export --format omnibrain --include-raw --out sessions.ndjson
```

1行=1セッションのNDJSON（`tamo.session.v1`）。正規化イベントに加えて `--include-raw` で
原文レコードも同梱するので、OmniBrainのchunked distillation → HITL承認パイプラインに
そのまま流せます。**tamoが決定論収集、OmniBrainが意味的蒸留**という分業です。

## 非機能要件（NFR）

| 項目 | 方針 |
|---|---|
| **保持期間** | 既定 **無期限**（`settings.toml` の `[retention] days = 0`）。ソース側は消える前提 — Claude Codeはローカルtranscriptを**既定30日で起動時に自動削除**し（`cleanupPeriodDays`）、mtime基準の想定外削除の報告まである。tamoの存在意義は「ソースより長く残す受け皿」なので既定では消さない |
| **削除** | `days>0` 設定時は `serve`/`watch` が日次で自動prune。判定は**イベント活動時刻**（ファイルmtimeは見ない）、ts不明は安全側で保持、`--dry-run` で事前確認、削除内容は必ず報告（無言で消さない）。`0` は「消さない」の意味で**保存を止める副作用は無い**。全消去は `tamo purge --yes`（設定とトークンは残す） |
| **透明性** | `tamo stats` が `db_bytes` / `oldest_event` / 添付サイズを常時可視化 |
| **応答速度** | 10万イベント/195MB DBで実測: FTS検索 0.04〜16ms / 最新tail取得 0.1ms / 続き取得 4ms / MCP `get_session`(compact200件) 3ms / pack(5,000イベント→6k tok) 0.42秒 / 取込 約8,000件/秒。`events(session_key,seq)`・`events(ts)` インデックス + WAL + `synchronous=NORMAL` |
| **コンテキスト経済** | 溜めた文脈で受け手のコンテキストウィンドウを圧迫しない: `search`はスニペット、`pack`はトークン予算内、`get_session`は既定compact(本文1500字スリム化+応答全体60k字ガード、超過は古い側から間引き `truncated:true` 明示)。生CESが要るときだけ `compact=false` |
| **アクセス制御** | `~/.tamo` は 0700、トークンは 0600。inbox/MCPは `127.0.0.1` バインドのみ（LAN非公開） |
| **同時実行** | `.lock` による単一書込み。読取(MCP/検索)はWALで並行可 |
| **暗号化** | OSのディスク暗号化（BitLocker/FileVault）に委任。平文で持つ以上、保持期間と権限で守る設計 |
| **バックアップ** | `~/.tamo` のコピー、または `tamo export --include-raw`（NDJSON） |

## 正直な限界

- 各エージェントのログ形式は**非公開・予告なく変わる**。tamoは「落ちない・失わない」を
  保証するが「常に完全にパースできる」は保証しない。`tamo stats` のquarantineが増えたら
  `probe` のフィンガープリントを見てアダプタを直す運用。
- Cursorの旧`composerData.conversation`配列と新`bubbleId`分離の両対応だが、さらに新しい
  内部形式が来たら追随が要る。
- ブラウザ系はDOM抽出を自前実装しない（サイト改版に勝てない）。inbox v1形式への変換層だけ持つ。
- Windsurf / Cline / Copilot Chat は検出のみ。実機のフィンガープリント待ち。

## レイアウト

```
tamo/
  util.py 	   ノイズ除去・トークン見積・CJKトークナイザ
  schema.py 	 CES v1（正準イベント）・決定論的event_id
  cas.py 	     コンテンツアドレス格納・b64吸出し
  store.py 	   SQLite(WAL)・FTS5 trigram・quarantine
  probe.py 	   環境走査・フィンガープリント・sources.toml生成
  optimize.py  P1〜P4（dedup/diff折畳/要点抽出/予算詰め）
  cli.py 	     probe/collect/watch/stats/sessions/search/pack/export/hook
  http_inbox.py 127.0.0.1限定・トークン認証の投函口
  mcp_server.py FastMCP配布層（任意依存）
  adapters/    claude_code / cursor_ide / codex_cli / aider / generic_jsonl / inbox
tests/make_fixtures.py  5ソース模擬環境の生成（E2Eデモ用）
```

MIT License
