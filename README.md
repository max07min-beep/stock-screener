# 需給スクリーナー（GitHub Actions版）

東証の「銘柄別信用取引残高」PDFを data/ に置くと、GitHub上で自動実行されて result.csv が更新されます。

## 初期設定（1回だけ）
1. GitHubで新しいリポジトリを作成（Private推奨）。
2. 「Add file > Upload files」で、次のファイルをアップロード：screen.py, requirements.txt, README.md
3. 「Add file > Create new file」で、ファイル名欄に `.github/workflows/screen.yml` と入力し、screen.yml の中身を貼り付けて保存。
4. 「Settings > Actions > General > Workflow permissions」で "Read and write permissions" を選んで保存。

## 毎週の更新
1. JPXサイトから最新の「銘柄別信用取引残高」PDFをダウンロード。
2. リポジトリの data/ フォルダに「Add file > Upload files」でアップロード（ファイル名は 20261030_mtall.pdf のように日付が先頭だと最新が自動選択されます）。
3. アップロードすると自動で実行されます（15〜40分程度）。「Actions」タブで進行状況を確認。
4. 完了後、リポジトリの result.csv（表で表示されます）と summary.txt を確認。

## 注意
- 株価はYahoo Financeから取得します。GitHubのサーバーからのアクセスが制限されて失敗・欠損することがあります。その場合は Actions タブから「Re-run」してください。
- 高値からの月数は、直近約250営業日の最高値からの経過で計算しています（動画の「高値から半年」の代用）。
- 信用評価損益率は入力データがないため、点数に含めていません。
- 点数は動画の考え方を数値化した仮説で、儲かることを保証しません。実資金を入れる前に、過去データで検証してください。
