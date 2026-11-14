"""公式サイトのオッズページを取得・解析する。

終わったレースでは「締切時オッズ」（締切の瞬間の最終オッズ）が残っている。
これからのレースでは、ページを開いた時点のオッズ。

- oddstf: 単勝・複勝（複勝は払戻の幅 "1.8-2.3"）
"""
import re
from datetime import date

from .http import get_race_page

# 艇番のセルから、次の艇番のセルを越えずに、同じ行のオッズのセルまで
RE_ROW = re.compile(
    r'is-boatColor([1-6])">\s*[1-6]\s*</td>(?:(?!is-boatColor).)*?'
    r'class="oddsPoint[^"]*">\s*([^<]*?)\s*</td>', re.S)


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:      # 欠場などは「欠場」「-」になる
        return None


def parse_win_place(page_html: str) -> list[dict]:
    """oddstf の HTML → [{bet_type, combo, odds, odds_high}]"""
    i_win = page_html.find("単勝オッズ</span>")
    i_place = page_html.find("複勝オッズ</span>")
    if i_win < 0 or i_place < 0:
        return []
    rows = []
    for lane, val in RE_ROW.findall(page_html[i_win:i_place]):
        if (o := _to_float(val)) is not None:
            rows.append({"bet_type": "win", "combo": lane, "odds": o, "odds_high": None})
    for lane, val in RE_ROW.findall(page_html[i_place:]):
        lo, _, hi = val.partition("-")
        if (o := _to_float(lo)) is not None:
            rows.append({"bet_type": "place", "combo": lane, "odds": o, "odds_high": _to_float(hi)})
    return rows


def fetch_win_place(d: date, venue: str, race_no: int) -> list[dict]:
    return parse_win_place(get_race_page("oddstf", d, venue, race_no))
