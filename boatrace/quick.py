"""レース直後の結果取り込み（仮想舟券・ペーパートレードをその日のうちに精算する）。

仮想舟券かペーパートレードの記録があって、まだ結果の無いレースについて、
締切から WAIT_MIN 分たったら公式の結果ページを見に行き、払戻と返還を取り込む。
まだ確定していなければ、RETRY_MIN 分あけてもう一度見に行く。
"""
import sqlite3
import time
from datetime import date, datetime, timedelta

from .data import result_page

WAIT_MIN = 8           # 締切からこの分数たったら見に行く（レースは締切の約2〜3分後に始まり約2分で終わる）
RETRY_MIN = 2
MAX_PER_RUN = 5

_last_try: dict[str, float] = {}


def pending_races(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    cutoff = (now - timedelta(minutes=WAIT_MIN)).strftime("%H:%M")
    today = now.date().isoformat()
    return conn.execute(
        """SELECT p.* FROM programs p
           WHERE p.race_id IN (SELECT race_id FROM vbets UNION SELECT race_id FROM paper_bets)
             AND NOT EXISTS (SELECT 1 FROM races ra WHERE ra.race_id = p.race_id)
             AND NOT EXISTS (SELECT 1 FROM quick_results q WHERE q.race_id = p.race_id)
             AND (p.date < ? OR (p.date = ? AND p.deadline IS NOT NULL AND p.deadline <= ?))
           ORDER BY p.date, p.deadline""", (today, today, cutoff)).fetchall()


def settle(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    now = now or datetime.now()
    done = 0
    for prog in pending_races(conn, now)[:MAX_PER_RUN * 3]:
        if done >= MAX_PER_RUN:
            break
        if time.monotonic() - _last_try.get(prog["race_id"], -1e9) < RETRY_MIN * 60:
            continue
        _last_try[prog["race_id"]] = time.monotonic()
        res = result_page.fetch(date.fromisoformat(prog["date"]), prog["venue"], prog["race_no"])
        if not res["payouts"]:
            continue                       # まだ確定していない（レースが遅れているなど）
        conn.executemany(
            "INSERT OR REPLACE INTO payouts (race_id, bet_type, combo, payout, popularity) VALUES (?, ?, ?, ?, ?)",
            [(prog["race_id"], p["bet_type"], p["combo"], p["payout"], p["popularity"]) for p in res["payouts"]])
        conn.execute("INSERT OR REPLACE INTO quick_results VALUES (?, ?, ?)",
                     (prog["race_id"], ",".join(res["refunds"]), now.isoformat(timespec="seconds")))
        conn.commit()
        _last_try.pop(prog["race_id"], None)
        done += 1
        print(f"[quick] {prog['race_id']} の結果を取り込み（返還 {res['refunds'] or 'なし'}）", flush=True)
    return done
