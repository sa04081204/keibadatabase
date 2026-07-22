# 中京競馬 攻略ボード

中京競馬場のコース別（芝1200/1400/1600/2000/2200m、ダート1200/1400/1800/1900m）データ分析サイトです。
スマホでそのまま閲覧できる静的サイト（HTML/CSS/JSのみ、サーバー不要）になっています。

- コース一覧：馬番ゾーン別・脚質別の勝率、ペース傾向(RPCI)、夏(6-8月)とそれ以外の比較
- ランキング：騎手・調教師別の勝率／複勝率／**単勝回収率／複勝回収率**（コース別・全コース通算、最低出走数で絞り込み可能）
- データ検索：コース・季節・馬場状態・脚質・馬番ゾーン・キーワードで実際のレース明細を絞り込み

コース詳細シートでは、馬番ゾーンだけでなく**実際の馬番ごと**の勝率・回収率、1番人気の回収率なども確認できます。

### 種牡馬（父馬）データについて
現状のCSVには父馬列が含まれていないため、種牡馬別の集計は未対応です。
TARGETの出力設定で「父」列を追加してCSVを出し直せば、`tools/import_chukyo.py`が自動でその列を拾って
`entries.sire`に取り込みます（列名の候補: `父` / `父馬名` / `父馬`）。取り込み後、種牡馬別ランキングが
必要であれば`tools/export_web_data.py`に集計処理を追加します。

## 公開方法（GitHub Pages）

このフォルダの中身がそのままサイトの中身です（`index.html`がトップページ）。

1. GitHubで新しいリポジトリを作成する（例: `chukyo-board`）。Public/Privateどちらでも可（Privateの場合はPagesがPro以上のプランのみ対応な点に注意）。
2. このフォルダで以下を実行してプッシュする。

```bash
cd chukyo-site
git init
git add .
git commit -m "Initial commit: 中京競馬 攻略ボード"
git branch -M main
git remote add origin https://github.com/【あなたのユーザー名】/chukyo-board.git
git push -u origin main
```

すでに`git init`済みの場合は `remote add` 以降だけでOKです。

3. GitHubのリポジトリ画面 → **Settings → Pages** を開く
4. "Build and deployment" の Source で **Deploy from a branch** を選び、Branch を `main` / `/(root)` にして **Save**
5. 数十秒〜数分待つと `https://【あなたのユーザー名】.github.io/chukyo-board/` が使えるようになります

このURLをスマホのホーム画面に追加（Safari/Chromeの「ホーム画面に追加」）しておくと、アプリのように開けます。

## データの更新方法

TARGETから新しいCSVを出力したら、以下の順で再生成してプッシュし直せばサイトに反映されます。

```bash
# 1. CSVをSQLiteに取込む（既存データに追記/上書き）
python tools/import_chukyo.py 新しいCSV.csv --db chukyo.db

# 2. コース別集計（確認用レポート、任意）
python tools/analyze_chukyo.py --db chukyo.db

# 3. サイト用JSONを再生成
python tools/export_web_data.py --db chukyo.db --out-dir data

# 4. コミット＆プッシュ
git add data
git commit -m "データ更新"
git push
```

## フォルダ構成

```
index.html              トップページ
assets/style.css         スタイル
assets/app.js            表示・検索ロジック
data/courses.json        コース別集計値（狙い目まとめ・勝率など）
data/entries/*.json      コースごとの全レース明細（検索用、列配列形式で軽量化）
tools/                   データ加工用Pythonスクリプト（サイトの表示には不要）
```

`chukyo.db`（SQLite本体）と元CSVはリポジトリには含めていません（サイズが大きいため）。
手元の作業フォルダで保管し、更新時だけ使ってください。

## 注意

- 集計値はNが少ないコース・季節では参考値です（サイト上にも "少" マークで表示されます）。
- あくまで過去データの傾向であり、将来の結果を保証するものではありません。
