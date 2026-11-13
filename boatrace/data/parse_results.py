"""競走成績（K ファイル）のパーサー。

1 ファイル = 1 日分・全場。場ごとに `24KBGN` 〜 `24KEND` で囲まれている。
レース見出し例:
   1R       予選                 H1800m  晴　  風  北　　 1m  波　  1cm
選手行の例:
  01  1 4444 山　田　　太　郎 12   34  6.78   1    0.12     1.49.8
"""
import re
import unicodedata
from datetime import date

from ..config import race_id

RE_VENUE_BEGIN = re.compile(r"^(\d{2})KBGN")
RE_VENUE_END = re.compile(r"^(\d{2})KEND")
RE_RACE = re.compile(
    r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m\s+(\S+)\s+風\s+(\S+)\s+(\d+)m\s+波\s+(\d+)cm"
)
RE_ENTRY = re.compile(r"^\s*([0-9A-Z]{1,2})\s+([1-6])\s+(\d{4})\s+(.+?)\s+(\d{1,3})\s+(\d{1,3})(.*)$")
RE_EXHIBIT = re.compile(r"^\d\.\d{2}$")
RE_RACE_TIME = re.compile(r"^\d\.\d{2}\.\d$")

BET_NAMES = {
    "単勝": "win", "複勝": "place", "2連単": "exacta", "2連複": "quinella",
    "拡連複": "wide", "3連単": "trifecta", "3連複": "trio",
}
RE_BET_LINE = re.compile(r"^\s*(単勝|複勝|2連単|2連複|拡連複|3連単|3連複)\s+(.*)$")
RE_BET_CONT = re.compile(r"^\s{8,}([1-6](?:-[1-6]){0,2}\s+\d+.*)$")
RE_COMBO = re.compile(r"([1-6](?:-[1-6]){0,2})\s+(\d+)(?:\s+人気\s+(\d+))?")


def _norm(line: str) -> str:
    # 全角英数・半角カナを統一（「２連単」→「2連単」など）
    return unicodedata.normalize("NFKC", line).rstrip()


def _parse_entry_tail(tail: str) -> dict:
    tokens = tail.split()
    out = {"exhibit_time": None, "course": None, "start_timing": None, "race_time": None}
    i = 0
    if i < len(tokens) and RE_EXHIBIT.match(tokens[i]):
        out["exhibit_time"] = float(tokens[i])
        i += 1
    if i < len(tokens) and tokens[i] in "123456" and len(tokens[i]) == 1:
        out["course"] = int(tokens[i])
        i += 1
        if i < len(tokens):
            out["start_timing"] = tokens[i]
            i += 1
    for t in tokens[i:]:
        if RE_RACE_TIME.match(t):
            out["race_time"] = t
            break
    return out


def parse(text: str, d: date) -> tuple[list[dict], list[dict], list[dict]]:
    """戻り値: (races, results, payouts)"""
    ds = f"{d:%Y%m%d}"
    races, results, payouts = [], [], []
    venue = None
    cur_race = None
    cur_bet = None

    for raw in text.splitlines():
        line = _norm(raw)
        if m := RE_VENUE_BEGIN.match(line):
            venue, cur_race, cur_bet = m.group(1), None, None
            continue
        if RE_VENUE_END.match(line):
            venue, cur_race, cur_bet = None, None, None
            continue
        if venue is None:
            continue

        if m := RE_RACE.match(line):
            rno = int(m.group(1))
            cur_race = race_id(ds, venue, rno)
            cur_bet = None
            races.append({
                "race_id": cur_race, "date": d.isoformat(), "venue": venue, "race_no": rno,
                "race_name": m.group(2).strip(), "distance": int(m.group(3)),
                "weather": m.group(4).strip(), "wind_dir": m.group(5).strip(),
                "wind_speed": int(m.group(6)), "wave": int(m.group(7)),
            })
            continue
        if cur_race is None:
            continue

        if m := RE_ENTRY.match(line):
            cur_bet = None
            finish_raw = m.group(1)
            row = {
                "race_id": cur_race, "lane": int(m.group(2)), "racer_id": m.group(3),
                "racer_name": re.sub(r"\s+", "", m.group(4)),
                "motor_no": int(m.group(5)), "boat_no": int(m.group(6)),
                "finish": int(finish_raw) if finish_raw.isdigit() else None,
                "finish_raw": finish_raw,
            }
            row.update(_parse_entry_tail(m.group(7)))
            results.append(row)
            continue

        if m := RE_BET_LINE.match(line):
            cur_bet = BET_NAMES[m.group(1)]
            rest = m.group(2)
        elif cur_bet and (m := RE_BET_CONT.match(line)):
            rest = m.group(1)
        else:
            if line.strip():
                cur_bet = None
            continue
        for combo, pay, pop in RE_COMBO.findall(rest):
            payouts.append({
                "race_id": cur_race, "bet_type": cur_bet, "combo": combo,
                "payout": int(pay), "popularity": int(pop) if pop else None,
            })

    return races, results, payouts
