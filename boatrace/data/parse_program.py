"""番組表（B ファイル）のパーサー。

場ごとに `24BBGN` 〜 `24BEND`。NFKC 正規化後の見出し例:
 1R  予選          H1800m  電話投票締切予定10:47
選手行の例:
1 4444山田太郎30福岡52A1 7.12 55.32 7.50 60.00 12 38.12 34 35.00 ...
"""
import re
import unicodedata
from datetime import date

from ..config import race_id

RE_VENUE_BEGIN = re.compile(r"^(\d{2})BBGN")
RE_VENUE_END = re.compile(r"^(\d{2})BEND")
RE_RACE = re.compile(r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m")
RE_DEADLINE = re.compile(r"締切予定\s*(\d{1,2}:\d{2})")
# 固定幅の列なので、モーター・ボート番号が 3 桁だと前の値とくっつく（例: 33.33141）。
# 勝率・2連率は必ず小数 2 桁なので、それを区切りにする。
RE_ENTRY = re.compile(
    r"^([1-6])\s+(\d{4})(\D+?)(\d{2})(\D+?)(\d{2})(A1|A2|B1|B2)\s*"
    r"(\d+\.\d{2})\s*(\d+\.\d{2})\s*(\d+\.\d{2})\s*(\d+\.\d{2})\s*"
    r"(\d+)\s*(\d+\.\d{2})\s*(\d+)\s*(\d+\.\d{2})"
)


def parse(text: str, d: date) -> tuple[list[dict], list[dict]]:
    """戻り値: (programs, entries)"""
    ds = f"{d:%Y%m%d}"
    programs, entries = [], []
    venue = None
    cur_race = None

    for raw in text.splitlines():
        line = unicodedata.normalize("NFKC", raw).rstrip()
        if m := RE_VENUE_BEGIN.match(line):
            venue, cur_race = m.group(1), None
            continue
        if RE_VENUE_END.match(line):
            venue, cur_race = None, None
            continue
        if venue is None:
            continue
        if m := RE_RACE.match(line):
            rno = int(m.group(1))
            cur_race = race_id(ds, venue, rno)
            dl = RE_DEADLINE.search(line)
            programs.append({
                "race_id": cur_race, "date": d.isoformat(), "venue": venue, "race_no": rno,
                "race_name": m.group(2).strip(), "distance": int(m.group(3)),
                "deadline": f"{int(dl.group(1).split(':')[0]):02d}:{dl.group(1).split(':')[1]}" if dl else None,
            })
            continue
        if cur_race and (m := RE_ENTRY.match(line)):
            g = m.groups()
            entries.append({
                "race_id": cur_race, "lane": int(g[0]), "racer_id": g[1],
                "racer_name": re.sub(r"\s+", "", g[2]), "age": int(g[3]),
                "branch": g[4].strip(), "weight": int(g[5]), "grade": g[6],
                "nat_win_rate": float(g[7]), "nat_2rate": float(g[8]),
                "local_win_rate": float(g[9]), "local_2rate": float(g[10]),
                "motor_no": int(g[11]), "motor_2rate": float(g[12]),
                "boat_no": int(g[13]), "boat_2rate": float(g[14]),
            })
    return programs, entries
