# tamo scoop — ブラウザ拡張（MV3）

[English](../../browser-extension/README.md) | 日本語

AIチャットサイトの会話を **tamo の inbox（localhost）へ投函**する拡張。
データはlocalhost以外へ一切送りません。

## 対応

| サイト | 方式 | 添付 |
|---|---|---|
| claude.ai | 同一オリジンAPI（アプリ自身が使うJSON） | 文書はclaude.aiの抽出済みテキストを同梱、画像はpreviewをb64 |
| chatgpt.com / chat.openai.com | 同一オリジンAPI（mappingツリー復元） | 画像/ファイルはdownload URL解決でb64 |
| gemini.google.com | DOM（user-query/model-response要素） | メッセージ内画像をb64 |
| その他すべて | 汎用DOMヒューリスティック（オンデマンド注入） | メッセージ内画像をb64 |

**添付ポリシー**: b64で取れるものは同梱（6MB/添付・20MB/会話・20個まで）。
取れない/超過したものは `[添付(未取得): 名前 mime サイズ]` を本文に必ず残す —
情報を黙って落とさない。同梱されたPDF/docx/xlsxはtamo側で自動テキスト抽出され全文検索できます。

## インストール

1. `chrome://extensions` → デベロッパーモードON → 「パッケージ化されていない拡張機能を読み込む」→ このフォルダ
2. `tamo serve` を起動（WSL/ネイティブどちらでも。これ1つで投函口+MCP+自動収集が全部立つ）
3. 拡張のpopupを一度開く → **自動ペアリング**されトークン設定は不要（`GET /pair`、
   Hostヘッダ検査付きでlocalhost以外からは読めない）。トークンがズレたら
   popupの**「再ペアリング」**ボタンで即復旧できます（403の対処はこれ）
4. AIチャットの会話ページで**画面右上の🎣ボタン**をワンクリック → その場に結果トースト
   （popupの「この会話を掬う」でも同じ。🎣常駐はpopupのチェックでOFF可。
   長い会話は過去分の自動読込中、ボタンに進捗 n/25 が出ます）

> **WSL2のlocalhost**: Windows 11のWSL2はlocalhostフォワーディングが既定で有効なので、
> Windowsのブラウザから `127.0.0.1:8787` でWSL内のtamoに届きます。
> 届かない場合は `%UserProfile%\.wslconfig` の `localhostForwarding=true` を確認してください。

## 拡張を更新した後に「Cannot read properties of undefined」が出たら

Chromeの仕様で、拡張のリロード後は**更新前から開いていたタブ**の旧スクリプトが
`chrome.runtime` を失います（孤児化）。そのタブを **F5で再読み込み** すれば直ります。
現行版は孤児化を検知して「再読み込みしてください」トーストを出し、
バックグラウンドの巡回も自動停止します。

## ドリフトについて（正直な話）

claude.ai / ChatGPT のAPIは非公開で、予告なく変わります。この拡張は
**サイトアダプタが失敗したら自動で汎用DOM抽出にフォールバック**し、
その場合もpayloadに `note: "site adapter failed: ..."` を残します。
壊れたら直すのはアダプタ1ファイルだけです（`content/sites/*.js`）。
