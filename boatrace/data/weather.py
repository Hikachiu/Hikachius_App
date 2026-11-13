"""公式サイトのレースページから気温・水温・天候を取得する。

K ファイルには気温が入っていないので、ここで補う。
- raceresult: 終わったレース（過去データの気温）
- beforeinfo: これからのレースの直前情報（展示後の気温・風・波）
"""
import html
import re
import time
from datetime import date

import requests

from ..config import BOATRACE_BASE, REQUEST_INTERVAL_SEC, USER_AGENT

RE_TAG = re.compile(r"<[^>]+>")
RE_TEMP = re.compile(r"気温\s*([\d.]+)\s*℃")
RE_WATER = re.compile(r"水温\s*([\d.]+)\s*℃")
RE_WIND = re.compile(r"風速\s*(\d+)\s*m")
RE_WAVE = re.compile(r"波高\s*(\d+)\s*cm")
WEATHERS = ("晴", "曇り", "雨", "雪", "霧")

# 接続を使い回す（毎回つなぎ直すと 1 件 10 秒近くかかる）
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def fetch(page: str, d: date, venue: str, race_no: int) -> dict:
    url = f"{BOATRACE_BASE}/{page}"
    params = {"rno": race_no, "jcd": venue, "hd": f"{d:%Y%m%d}"}
    resp = _session.get(url, params=params, timeout=30)
    time.sleep(REQUEST_INTERVAL_SEC)
    resp.raise_for_status()
    text = html.unescape(RE_TAG.sub(" ", resp.text))
    text = re.sub(r"\s+", " ", text)

    out: dict = {}
    if m := RE_TEMP.search(text):
        out["temperature"] = float(m.group(1))
    if m := RE_WATER.search(text):
        out["water_temp"] = float(m.group(1))
    if m := RE_WIND.search(text):
        out["wind_speed"] = int(m.group(1))
    if m := RE_WAVE.search(text):
        out["wave"] = int(m.group(1))
    # 天候は「気温」の近くに出てくる
    idx = text.find("気温")
    if idx >= 0:
        window = text[max(0, idx - 40): idx + 80]
        for w in WEATHERS:
            if w in window:
                out["weather"] = w
                break
    return out
