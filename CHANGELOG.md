# Changelog

書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠。

## [0.2.0] - 2026-07-09

市場投入前レビュー（UX・構造・セキュリティの全導線監査）に基づく大規模改善。

### 修正 — データ保全
- CAS書込をアトミック化（temp+rename）。クラッシュ書きかけの破損blobは自動で書き直す
- inboxファイルの `done/` 移動をDBコミット成功後に変更（クラッシュ時のデータ喪失窓を閉鎖）。
  Windowsでの同名衝突でwatchデーモンが死ぬ問題も解消
- Cursor等のスナップショット一時ディレクトリが削除されず`$TEMP`に無限蓄積していたリークを修正
- `PRAGMA user_version` によるスキーマ版数と移行台帳を導入（アップグレードで既存DBを壊さない）

### 修正 — 導入体験（特に日本語Windows）
- `probe --write` / `pack --out` 等のencoding未指定を全域修正。cp932環境で
  sources.tomlが読めなくなり全collectが死ぬ致命バグを解消
- 標準入出力をUTF-8+行バッファへ再構成（リダイレクト時のUnicodeEncodeError、
  サービスログが空に見える問題を解消）
- `.lock` にPIDを記録し、残留ロック（クラッシュ後）を自動回収
- `serve` はmcp依存・ポート使用中を起動前チェック（偽の成功バナー後に落ちない）
- ネイティブWindowsで `%APPDATA%` 配下（Cursor / Windsurf / Cline等）を走査するように

### 追加
- `tamo quarantine list/show/clear` — 隔離データの閲覧・削除コマンド（同一原因の重複蓄積も防止）
- MCP HTTP（streamable-http）に `X-Tamo-Token` / `Authorization: Bearer` 認証を必須化
- serve/watchに1時間毎のハートビートログ
- `search` の0件メッセージ / `prune` の確認プロンプト / `probe --write` の既存設定`.bak`退避+差分表示
- pytestスイート（57件）と GitHub Actions CI（ubuntu/windows × Python 3.11/3.13）
- ブラウザ拡張: アイコン一式 / 再ペアリングボタン / 自動スクロール進捗表示 /
  サーバエラー本文の表示 / 「接続確認」のトークン検証 / 切詰め発生時の警告と記録

### 変更
- `tamo mirror` の秘密情報マスクを既定ONに（`--no-redact` で原文出力）。
  検出パターンを拡充: Stripe / GitHub fine-grained PAT / GitLab / npm / PyPI /
  Hugging Face / SendGrid / Slack・Discord webhook / URL埋込認証情報 ほか
- README を「趣旨と使い方」に再構成し、技術解説を `docs/ARCHITECTURE.md` へ分離
- 拡張の未使用 `http://localhost/*` 権限を削除（実装は127.0.0.1のみ）

### 国際化・保存期間（同版に追補）
- README を英語化（正）し `README.ja.md` を併設。15分チュートリアル
  `docs/TUTORIAL.md` / `TUTORIAL.ja.md` を新設（実データ不要のサンドボックス付き）
- ブラウザ拡張UIを i18n 化（`_locales` en既定 + ja、52キー）
- 無期限保存のリスク調査を `docs/ARCHITECTURE.md` §12.1 に文書化
  （実測: 10万イベント=208MB。真のリスクは秘密情報の平文蓄積・規程との衝突・
  クラウド同期フォルダ事故）。防波堤として日次ディスク使用量警告
  `[retention] warn_db_mb`（既定2048MB）を serve/watch/stats に実装

## [0.1.0] - 初版

- 6ソース（Claude Code / Cursor / Codex CLI / aider / generic_jsonl / inbox）の
  決定論収集、SQLite+FTS5(trigram)、CAS、textract、pack(P1〜P4)、
  recall、mirror/rules、HTTP inbox、MCPサーバ、Chrome MV3拡張
