# 中京競馬 攻略ボード

中京競馬場のコース別（芝1200/1400/1600/2000/2200m、ダート1200/1400/1800/1900m）データ分析サイトです。
スマホでそのまま閲覧できる静的サイト（HTML/CSS/JSのみ、サーバー不要）になっています。

- コース一覧：馬番ゾーン別・脚質別の勝率、ペース傾向(RPCI)、夏(6-8月)とそれ以外の比較
- ランキング：騎手・調教師別の勝率／複勝率／**単勝回収率／複勝回収率**（コース別・全コース通算、最低出走数で絞り込み可能）
- データ検索：コース・季節・馬場状態・脚質・馬番ゾーン・キーワードで実際のレース明細を絞り込み

コース詳細シートでは、馬番ゾーンだけでなく**実際の馬番ごと**の勝率・回収率、1番人気の回収率なども確認できます。

### 血統・前走データについて
父馬名・母馬名・母父馬名、および前走情報（間隔・前走場所・前走距離・前走着順など）に対応しています。
`tools/import_extra.py`が、TARGETの詳細出力（「前走距離」等の名前で出力される105列形式のCSV）から
これらを取り込み、`(日付, レース番号, 馬番)`をキーに既存データへマージします。

## AI予想（netkeiba出馬表 × コースデータ）

`tools/predict_race.py`は、netkeibaの出馬表(race_id)を取得し、`data/courses.json`と
`data/rankings/*.json`の実績データ（馬番ゾーン・脚質・騎手/調教師のコース別勝率・回収率など）
と掛け合わせて**ルールベースでスコア化**する予想スクリプトです。

機械学習ではなくルールベースにしているのは、中京はコース別に見るとサンプル数が
数百件程度で機械学習だとすぐ過学習するのと、このサイト自体が「勝率・回収率を根拠に
説明できる攻略ボード」というコンセプトだからです。スコアの内訳（どの要素が高評価/低評価か）
がそのまま表示されるので、当たり外れだけでなく「なぜその順位なのか」が追えます。

### 使い方

```bash
pip install requests beautifulsoup4 --break-system-packages

# race_idはnetkeibaのレースURLの race_id=XXXXXXXXXXXX の部分
python tools/predict_race.py 202607020101

# 芝コースでA/Bどちらの施行か分かっている場合
python tools/predict_race.py 202607020101 --variant B

# 前走情報の取得（間隔・距離変化の判定に使用）をスキップして高速化したい場合
python tools/predict_race.py 202607020101 --no-past
```

実行すると `data/predictions/<race_id>.json` と `data/predictions/index.json` が
生成/更新されます。あとはいつもの更新手順と同じく

```bash
git add data/predictions
git commit -m "AI予想を追加"
git push
```

とすれば、サイトの「AI予想」タブから見られるようになります。

### 仕組み・注意点

- 芝コースはA/Bコース（開催が進むと使うコースが替わる）の区別が出馬表からは自動判定できないため、
  `--variant`を省略した場合はA/Bコースを出走数で加重平均した値を使います。正確な判定が必要な場合は
  JRAの発表を確認して`--variant A`または`--variant B`を指定してください。
- 前走情報（間隔・距離変化・脚質の推定）は`db.netkeiba.com`の馬個別ページから
  ベストエフォートで取得します。取得できなかった項目は自動的に「中立(50点)」として
  スコアに影響しないよう処理されるので、失敗してもエラーにはなりません。
- 騎手・調教師名は出馬表側が姓のみ表示（見習いマーク`☆△▲◇★`つきのことも）なのに対し、
  ランキング側はフルネームなので、あいまいマッチングで対応しています。同姓の騎手/調教師が
  該当コースに複数いる場合、意図しない方にマッチする可能性があるため、出力の`reasoning`欄で
  マッチ結果（フルネーム・N数）を必ず確認してください。
- あくまで過去の傾向を点数化した参考情報であり、着順を保証するものではありません
  （サイト内にも毎回この注記が表示されます）。
- スコアの重みは`tools/predict_race.py`冒頭の`WEIGHTS`辞書で調整できます。

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

# 1.5 血統・前走情報を追加で取り込みたい場合(TARGETの「父馬名」「前走○○」を含む
#     詳細出力=105列形式のCSV)は、1の後にこちらも実行する
python tools/import_extra.py 中京*前走距離.csv --db chukyo.db

# 2. コース別集計（確認用レポート、任意）
python tools/analyze_chukyo.py --db chukyo.db

# 3. サイト用JSONを再生成
python tools/export_web_data.py --db chukyo.db --out-dir data

# 4. コミット＆プッシュ
git add data
git commit -m "データ更新"
git push
```

### キャッシュに注意

`index.html`が読み込む`assets/style.css`と`assets/app.js`には `?v=3` のようなバージョン番号を付けています。
HTML・CSS・JSを更新したのに反映されない場合は、この番号を1つ上げてください（`index.html`内の2箇所、
および`assets/app.js`先頭の`DATA_VERSION`）。番号を上げないと、ファイルごとにブラウザ/GitHubのキャッシュの
新旧がズレて、例えば「HTMLは新しいのにJSだけ古い」状態になり、タブ切り替えなどが正しく動かなくなることがあります。

## フォルダ構成

```
index.html              トップページ
assets/style.css         スタイル
assets/app.js            表示・検索ロジック
data/courses.json        コース別集計値（狙い目まとめ・勝率など）
data/entries/*.json      コースごとの全レース明細（検索用、列配列形式で軽量化）
data/predictions/*.json  AI予想結果（tools/predict_race.pyが生成、無くてもサイトは動く）
tools/                   データ加工用Pythonスクリプト（サイトの表示には不要）
tools/predict_race.py    netkeiba出馬表 × コースデータ のAI予想スクリプト
```

`chukyo.db`（SQLite本体）と元CSVはリポジトリには含めていません（サイズが大きいため）。
手元の作業フォルダで保管し、更新時だけ使ってください。

## 注意

- 集計値はNが少ないコース・季節では参考値です（サイト上にも "少" マークで表示されます）。
- あくまで過去データの傾向であり、将来の結果を保証するものではありません。
