from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "boatrace.db"

# 公式のダウンロードデータ（競走成績 K / 番組表 B）
MBRACE_BASE = "https://www1.mbrace.or.jp/od2"
# 公式サイト（気温・水温などの取得用）
BOATRACE_BASE = "https://www.boatrace.jp/owpc/pc/race"

USER_AGENT = "Mozilla/5.0 (personal boatrace research tool)"
# 公式サイトへの負荷を避けるため、1リクエストごとに待つ秒数
REQUEST_INTERVAL_SEC = 1.0

VENUES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川",
    "06": "浜名湖", "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国",
    "11": "びわこ", "12": "住之江", "13": "尼崎", "14": "鳴門", "15": "丸亀",
    "16": "児島", "17": "宮島", "18": "徳山", "19": "下関", "20": "若松",
    "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}


def race_id(date_str: str, venue: str, race_no: int) -> str:
    """date_str は YYYYMMDD。例: 20241101-24-01"""
    return f"{date_str}-{venue}-{race_no:02d}"
