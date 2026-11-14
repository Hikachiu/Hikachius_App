"""公式サイトのレース結果ページ（raceresult）から、払戻と返還をその日のうちに取る。

競走成績ファイル（K ファイル）は翌日にならないと出ないので、
仮想舟券やペーパートレードをレース直後に精算するために使う。
"""
import html as html_lib
import re
from datetime import date

from .http import get_race_page

BET_NAMES = {
    "単勝": "win", "複勝": "place", "2連単": "exacta", "2連複": "quinella",
    "拡連複": "wide", "3連単": "trifecta", "3連複": "trio",
}
UNORDERED = {"quinella", "wide", "trio"}   # 順番が関係ない券種は小さい順にそろえる

RE_TBODY = re.compile(r"<tbody>(.*?)</tbody>", re.S)
RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
RE_BET = re.compile(r'<td rowspan="\d+">\s*([^<]+?)\s*</td>')
# 単勝の行だけ <span class="numberSet1_number is-type1" nbsp="true"> のように属性が増える
RE_NUM = re.compile(r'numberSet1_number[^>]*>\s*([1-6])\s*<')
RE_PAY = re.compile(r"is-payout1\">\s*&yen;([\d,]+)")
RE_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def normalize_combo(bet_type: str, lanes: list[str]) -> str:
    if bet_type in UNORDERED:
        lanes = sorted(lanes)
    return "-".join(lanes)


def parse(page_html: str) -> dict:
    """→ {"payouts": [{bet_type, combo, payout, popularity}], "refunds": ["5", ...]}
    レースが終わっていなければ payouts は空。"""
    payouts = []
    i = page_html.find("<th>勝式</th>")
    if i >= 0:
        table = page_html[i: page_html.find("</table>", i)]
        bet = None
        for tb in RE_TBODY.findall(table):
            for tr in RE_TR.findall(tb):
                if m := RE_BET.search(tr):
                    bet = BET_NAMES.get(html_lib.unescape(m.group(1)).strip())
                lanes = RE_NUM.findall(tr)
                pay = RE_PAY.search(tr)
                if not (bet and lanes and pay):
                    continue
                tds = RE_TD.findall(tr)
                pop = re.sub(r"<[^>]+>|&nbsp;|\s", "", tds[-1]) if tds else ""
                payouts.append({"bet_type": bet, "combo": normalize_combo(bet, lanes),
                                "payout": int(pay.group(1).replace(",", "")),
                                "popularity": int(pop) if pop.isdigit() else None})
    refunds = []
    k = page_html.find("<th>返還</th>")
    if k >= 0:
        refunds = sorted(set(RE_NUM.findall(page_html[k: page_html.find("</table>", k)])))
    return {"payouts": payouts, "refunds": refunds}


def fetch(d: date, venue: str, race_no: int) -> dict:
    return parse(get_race_page("raceresult", d, venue, race_no))
