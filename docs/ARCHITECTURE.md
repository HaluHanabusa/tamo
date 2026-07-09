# tamo アーキテクチャ解説

> **読み分け**: 使い方は [README](../README.md)、技術選定の理由と実装ファイル対応は
> [TECH_STACK.md](TECH_STACK.md)、実機確認手順は [VERIFICATION.md](VERIFICATION.md)。
> この文書は「tamoがどういう考え方で作られているか」— 設計原則・データモデル・
> 最適化・還流・非機能要件 — を説明します。

## 1. 設計原則

1. **取込は無損失、最適化は読出時** — 原文(raw)は必ずSQLiteに保存し、要約・圧縮は
   いつでも再構築できる「ビュー」として生成する。アルゴリズムを改善すれば
   過去データにも遡って効く。
2. **収集・保存にLLMを使わない** — event_idの決定論的生成、規則ベースの要点抽出、
   TF-IDF+MMRの文選抜まで全て決定論（同入力→同出力）。意味的な蒸留は下流
   （OmniBrainのHITL等）の仕事。
3. **ドリフト前提** — 各社のログ形式は非公開・無保証。壊れた行はquarantineへ、
   未知の将来スキーマはraw温存+metaイベントで**絶対に落ちない・失わない**。
4. **添付はメタデータ優先** — 中身を読み切れなくても「何が添付されたか
   （種別・名前・サイズ）」を本文に必ず残す。テキスト抽出はあくまでボーナス。

## 2. 全体アーキテクチャ

```
[エージェント達]                 [tamo]                            [引き継ぎ先]
Claude Code transcript ─┐   probe: 自動検出
Cursor state.vscdb ─────┤   collect: 増分・冪等・無損失      ┌→ MCP server (recall/search/pack/blob)
Codex CLI rollout ──────┼→  CES正規化 + CAS(blob重複排除) ──┼→ tamo pack (Markdown貼り付け)
aider history.md ───────┤   SQLite + FTS5(日本語trigram)     └→ export NDJSON (OmniBrain等)
ブラウザ(MV3拡張) ──────┘   optimize: dedup/diff折畳/要点抽出
```

### モジュール配置

```
tamo/
  util.py        ノイズ除去・トークン見積・CJKトークナイザ・種別語彙
  schema.py      CES v1（正準イベント）・決定論的event_id
  cas.py         コンテンツアドレス格納・マジックナンバー判定
  textract.py    添付テキスト抽出（OOXML/PDF/plain, 決定論）
  store.py       SQLite(WAL)・FTS5 trigram・quarantine・prune・スキーマ版数
  probe.py       環境走査・フィンガープリント・sources.toml生成
  optimize.py    P1〜P4（dedup/diff折畳/要点抽出/予算詰め）
  recall.py      一発調査（セッション単位ダイジェスト）
  derive.py      mirror（git向け履歴）/ rules（導出ルール還流）
  redact.py      秘密情報マスク（mirrorで既定ON）
  config.py      settings.toml（保持期間等NFR）/ inboxトークン
  http_inbox.py  127.0.0.1限定・トークン認証（/inbox /pair /recall /health）
  mcp_server.py  FastMCP（stdio / streamable-http+トークン認証）
  cli.py         probe/collect/serve/search/recall/show/pack/mirror/rules/prune/quarantine… 22コマンド
  adapters/      claude_code / cursor_ide / codex_cli / aider / generic_jsonl / inbox
browser-extension/  MV3拡張 tamo scoop（自動ペアリング・常駐🎣・検索してコピー）
```

## 3. セッションとイベント（CES）

全ソースの発話は Canonical Event Schema (CES v1) に正規化されます:
`actor ∈ {user, assistant, tool, system}` × `kind ∈ {message, tool_use, tool_result, meta}`。

- **単位**: `session_key = source:ネイティブセッションID`。イベントは元ファイルの順序(`seq`)と
  時刻で並び、`tamo sessions` / `list_sessions` は最終活動の新しい順
- **冪等の心臓部**: `event_id = sha256(source|session|native_id または locator|kind|内容指紋)[:32]`。
  タイムスタンプを**入れない**ので時刻表記ゆれで別IDにならず、再collectは常に安全
  （`INSERT OR IGNORE` で弾かれる）
- **途中からの収集**: カーソルはバイトオフセットなので、導入時点で既存ファイル全体を
  バックフィルし、以降は増分だけ読む。resume済み・compact済みで途中から始まっている
  transcriptも「あるがまま」取り込む（無損失=「ディスクにあるものは全部、無いものは仮定しない」）
- **時系列は二軸**: `session_key`(所属)・`seq`(セッション内順序)・`ts`(時刻)。ts無しの
  DOM取得も拡張の `captured_at` で時間軸に補完される
- **続きだけの取得**: `tamo show <key> --since-event ⟨e:xxxx⟩の8桁` / MCP `get_session` の
  `since_event_id`。不明なIDには空を返す（全量の誤送を防ぐ安全側）
- **話題が複数セッションに跨る場合**: `recall` は各セッションのダイジェストを最新優先で並べ、
  冒頭に「🕒 3セッションに跨る: 07-01 → 07-05 → 07-08」の変遷サマリを付ける

### スキーマの進化

`PRAGMA user_version` にスキーマ版数を刻印し、旧版DBは接続時に版数台帳（`store._migrate`）で
現行へ引き上げる。列追加は `CREATE TABLE IF NOT EXISTS` では反映されないため、
将来の変更は必ず台帳にALTER手順を足す。新しいDBを古いtamoで開いた場合は
明確なエラーで更新を促す（黙って壊さない）。

## 4. 添付の扱い — CAS + 決定論テキスト抽出

貼付画像や添付のbase64は取込時に `cas/ab/cd/<sha256>.<ext>` へ吸い出し、会話ログ側は
`[blob image/png 70B sha=c414cd0e204d]` という参照に置換します。

- **重複排除**: 同じスクショ/PDFを何度貼ってもディスクは1回分
- **書込はアトミック**: temp+renameで書き、既存でもサイズ不一致（過去のクラッシュ書きかけ）は
  自動で書き直す — 内容整合性が前提の格納庫に破損を残さない
- **mimeはマジックナンバー優先**: 申告mimeが嘘でも `PK` ヘッダのZIP内部構造まで見て
  docx/xlsx/pptx を判別。動画/音声も mp4/mov/webm/wav/ogg/flac/mp3 をマジックで判定
- **読めない添付も文脈は残る**: 本文に `[添付(動画) clip.mp4 video/mp4 76B sha=…]` の形で
  種別・名前・サイズが必ず入り、種別語（動画/画像/PDF…）やファイル名で検索できる
- **テキスト抽出（textract）**: docx/xlsx/pptxはZIP+XML直読み（依存ゼロ）、PDFは
  pypdf（あれば。日本語ToUnicode対応）→ stdlib素朴抽出の二段構え。
  品質ゲート（可読文字比率）を通らないテキストは**捨てて**検索インデックスを汚さない。
  抽出結果は `blob_texts` にキャッシュされ、参照イベントのFTSに載る
  （= `tamo search` が添付の中身までヒットする）。`tamo reindex-blobs` で遡及再抽出可

## 5. 読出時最適化 — `tamo pack` のP1〜P4

保存はいじらず、読むときに毎回4段の決定論的変換を通します:

- **P1 dedup** — 120字以上の完全一致再掲を省略参照化（`⟨e:xxxx⟩と同一`）
- **P2 snapshot折畳** — 同一ファイルへのtool_result群を「最終版フル + 以前はunified diff」に圧縮
- **P3 要点抽出** — 規則ベース（日英）で 決定/制約/TODO/エラー→修正/触ったファイル を抽出
- **P4 予算詰め** — TF-IDF+MMR(λ=0.35)で会話テールを選抜し、指定トークン予算内のMarkdownに整形

全行に `⟨e:xxxx⟩` の出所IDが付き、いつでも `events` テーブルの原文へ遡れます。

## 6. 還流 — 収集した履歴をエージェントに返す

「収集→蒸留→還流」ループ（tamoでは全段決定論）:

- **mirror**: セッションを `./.tamo/history/*.md` へミラー。gitにコミットでき、PRでAIとの
  検討経緯をレビューでき、grepできる。正はtamoのDBで、mirrorは毎回全文再生成されるビュー。
  **秘密情報マスクは既定ON**（`--no-redact` で原文。redact.pyは既知プレフィックス型
  20種以上 + key=value行 + URL埋込認証情報を決定論マスク）
- **rules**: P3要点抽出による導出コンテキストをCLAUDE.md等へ。
  `<!-- tamo:rules:begin/end -->` マーカー区間だけを冪等更新するので手書き部分を壊さない。
  収集器なので人手レビューのゲートは置かない — 決定論・出所ID・毎回全再生成という
  性質自体が安全機構で、誤抽出は出力でなく抽出規則を直して再生成する
- **run**: フックの無いエージェントでも「1コマンド差し替え」で取込が回るラッパー

## 7. リアルタイム取込（フック）

Claude Code の `Stop` / `SessionEnd` フックは stdin で `transcript_path` を渡してくるので、
`tamo ingest-hook` が**そのファイルだけ**即時増分取込します（`async: true` で本体を
ブロックしない）。設定スニペットは `tamo hook` が表示。Codex CLI・Cursorの同型フックにも
流用できます。常駐は `tamo serve`（または `tamo watch`）のポーリングが受け持ちます。
WSL2の `/mnt/c` 配下はinotifyが効かないため、監視はポーリング設計です。

## 8. HTTP inbox — ブラウザ会話の投函口

`tamo serve`（または `tamo watch --http`）が `127.0.0.1:8787/inbox` を開きます。
トークンは `~/.tamo/inbox.token`（初回自動生成）。MV3拡張やユーザースクリプトから:

```js
fetch("http://127.0.0.1:8787/inbox", {
  method: "POST",
  headers: { "X-Tamo-Token": TOKEN, "Content-Type": "application/json" },
  body: JSON.stringify({
    schema: "tamo.inbox.v1",
    source: "chatgpt_web",            // 発生面（検索のsource絞り込みに使われる）
    session: "conv-abc123",           // 会話ID
    title: "任意タイトル",
    note: "任意の注記（切詰め・fallback理由等。metaイベントとして保全される）",
    messages: [
      { role: "user", text: "...", ts: "2026-07-07T09:00:00Z",
        attachments: [{ name: "memo.txt", mime: "text/plain", data_b64: "..." }] },
      { role: "assistant", text: "..." }
    ]
  })
});
```

- サーバは**検証してファイルに書くだけ**。パースは通常のinboxアダプタに一本化
  （ネットワークから直接パーサに触らせない = 攻撃面の最小化）
- 添付は `data_b64` があればCASへ、`url` だけならURLノート、`{name, mime, size}` の
  メタだけでも受けて `[添付(動画 未取得) … 50.0MB]` として本文に保全する
- inboxファイルの `done/` への移動は**DBコミット成功後**（クラッシュしても失わない）

## 9. MCP — 各エージェントへの配布

ツール8種: `recall`（一発調査・最初にこれ） / `search_context` / `get_context`（前後読み） /
`get_context_pack` / `list_sessions` / `get_session`（latest解決・since_event_id続き取得） /
`get_blob_text` / `get_blob_base64`。

- **stdio**（既定）: クライアントが子プロセスとして起動 = 実行者本人なので認証なし
- **streamable-http**（`tamo serve`）: `X-Tamo-Token`（または `Authorization: Bearer`）必須。
  書込(inbox)だけ認証して読出（収集した全会話+blob原物）が素通しでは非対称すぎるため。
  127.0.0.1バインドは「LANに出さない」であって認証ではない
- 応答は既定でcompact（本文1500字スリム化+応答全体60k字ガード、超過は古い側から間引き
  `truncated:true` 明示）— 受け手のコンテキストウィンドウを圧迫しない

## 10. VS Code / Visual Studio の Claude Code 拡張

CLI・VS Code拡張・JetBrains拡張・Claude Desktopのコーディング統合は、**すべて同じ
`~/.claude/projects`（Windowsは `%USERPROFILE%\.claude\projects`）にJSONL transcriptを書く**ため、
`claude_code` アダプタがそのまま吸えます。`CLAUDE_CONFIG_DIR` での移設にもprobeが追随します。
`tamo probe` は拡張のインストール痕跡を検出すると「追加設定不要」と注記します。

## 11. OmniBrain連携（任意）

```bash
tamo export --format omnibrain --include-raw --out sessions.ndjson
```

1行=1セッションのNDJSON（`tamo.session.v1`）。`--include-raw` で原文レコードも同梱し、
下流の chunked distillation → HITL承認パイプラインへそのまま流せます。
**tamoが決定論収集、下流が意味的蒸留**という分業です（exportは無損失契約のためマスクしません）。

## 12. 非機能要件（NFR）

| 項目 | 方針 |
|---|---|
| **保持期間** | 既定 **無期限**（`settings.toml` の `[retention] days = 0`）。ソース側は消える前提 — tamoの存在意義は「ソースより長く残す受け皿」 |
| **削除** | `days>0` 設定時は `serve`/`watch` が日次で自動prune。判定は**イベント活動時刻**（ファイルmtimeは見ない）、ts不明は安全側で保持、`--dry-run` と確認プロンプト、削除内容は必ず報告（無言で消さない）。全消去は `tamo purge --yes` |
| **透明性** | `tamo stats` が `db_bytes` / `oldest_event` / 隔離件数を常時可視化。`tamo quarantine` で隔離の原文まで確認可能 |
| **応答速度** | 10万イベント/195MB DBで実測: FTS検索 0.04〜16ms / tail取得 0.1ms / 続き取得 4ms / pack(5,000ev) 0.42秒 / 取込 約8,000件/秒 |
| **コンテキスト経済** | `search`はスニペット、`pack`はトークン予算内、`get_session`は既定compact。生CESが要るときだけ `compact=false` |
| **アクセス制御** | inbox/MCP(HTTP)ともに `127.0.0.1` バインド + **X-Tamo-Tokenトークン認証**（タイミングセーフ比較）。`~/.tamo` は 0700、トークンは 0600（POSIX。Windowsはユーザープロファイル+OSのアカウント分離に依存） |
| **同時実行** | `.lock`（PID記録・残留自動回収）による単一書込み。読取(MCP/検索)はWALで並行可 |
| **暗号化** | OSのディスク暗号化（BitLocker/FileVault）に委任。平文で持つ以上、保持期間と権限で守る設計 |
| **バックアップ** | `~/.tamo` のコピー、または `tamo export --include-raw`（NDJSON） |

## 13. 正直な限界

- 各エージェントのログ形式は**非公開・予告なく変わる**。tamoは「落ちない・失わない」を
  保証するが「常に完全にパースできる」は保証しない。`tamo quarantine` が増えたら
  `tamo probe` のフィンガープリントを見てアダプタを直す運用
- Cursorの旧`composerData.conversation`配列と新`bubbleId`分離の両対応だが、さらに新しい
  内部形式が来たら追随が要る
- ブラウザ系のclaude.ai/ChatGPTは同一オリジンAPI方式で改版に強いが、GeminiのDOM読みと
  汎用フォールバックはUI改版で浅くなり得る（その場合も取れた分+noteは残る）
- Windsurf / Cline / Copilot Chat は検出のみ。実機のフィンガープリント待ち
