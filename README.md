# 競艇予測（app/boatrace）

出走表と過去の成績から、各選手が **どんな天候・気温・風・波のときに上位に来ているか** を集計して、
1着・2連対・3連対の確率と買い目を出す Web アプリ。

## できること（v1）

- 公式データ（競走成績・番組表）を取り込んで SQLite に貯める
- 過去レースの気温・水温を公式サイトから補う
- 選手ページ: 天候別・風別・波別・気温別・枠別・場別の成績
- レースページ:
  - 条件（天候・風速・波高・気温）を手入力するか、直前情報から自動入力
  - 各艇の 1着 / 2連対 / 3連対 確率と「今回の条件での実績」
  - **おすすめの買い方**（レースの堅さで 3連単頭固定 / 2連複 / 3連複ボックス / 拡連複 を自動で選ぶ）
  - 券種と点数を自分で選んで買い目を表示

## セットアップ

1. Python 3.11 以上を入れる（https://www.python.org/downloads/ 、インストール時に「Add python.exe to PATH」にチェック）
2. このフォルダで:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## データを取り込む

```bash
# 過去の成績（まずは1〜3か月分から。1日1ファイルなのですぐ終わる）
python -m boatrace.ingest history --start 2026-08-01 --end 2026-11-12

# 気温・水温（1レース1秒かかる。場を絞るのがおすすめ）
python -m boatrace.ingest temps --start 2026-08-01 --end 2026-11-12 --venue 24

# 出走表（画面のボタンからでも取れる）
python -m boatrace.ingest program --date 2026-11-14
```

## 起動

```bash
uvicorn app.main:app --reload
```

ブラウザで http://127.0.0.1:8000 を開く。

## 構成

```
boatrace/
  config.py            場コード・URL・保存先
  db.py                SQLite のテーブル定義
  ingest.py            取り込みコマンド
  data/
    download.py        公式 LZH の取得と展開（Windows 標準の tar を使う）
    parse_results.py   競走成績（K ファイル）のパーサー
    parse_program.py   番組表（B ファイル）のパーサー
    weather.py         公式サイトから気温・水温・直前情報
  analysis/
    conditions.py      条件の区分けと選手ごとの集計
  predict/
    model.py           強さスコア → 着順確率（Plackett-Luce）
    bets.py            券種ごとの的中確率とおすすめ
app/
  main.py              FastAPI の画面
  templates/, static/
```

## 予測のしくみ（v1）

1. その場・その枠の平均 3連対率を出発点にする
2. 選手のその枠での実績で補正（走数が少ないほど平均に寄せる）
3. 今回と同じ天候・風・波・気温での実績で補正
4. 全国勝率・モーター 2連率で補正
5. スコアを Plackett-Luce モデルで着順の確率に変換

予測には「そのレースの日より前」のデータだけを使うので、過去のレースで検証しても未来の情報は混ざらない。

## この先やること

- [ ] 実データでパーサーを検証
- [ ] 過去レースで回収率をバックテスト
- [ ] オッズを取り込んで期待値で買い目を選ぶ
- [ ] LightGBM でモデルを学習して係数を自動調整
- [ ] 風向き（追い風・向かい風）を場ごとに考慮
