"""ペーパートレード（お金を賭けずに、本番と同じタイミングで買い目を記録する）。

締切の LEAD_MIN〜LEAD_MAX 分前になったレースについて:
  1. 公式の直前情報から天候・風・波・気温を取る
  2. その時点のオッズ（単勝・複勝）を取る
  3. 予想と混ぜて期待値を出し、戦略 A/B/C に当てはまる買い目を paper_bets に記録する
結果（払戻）は翌日の自動取り込みで入るので、画面で見るときに突き合わせる。

単体で 1 回だけ動かす: python -m boatrace.paper
"""
import json
import sqlite3
import threading
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .data import odds as odds_api
from .data import weather
from .db import connect
from .predict import value
from .predict.model import score_entries

# 締切の何分前に判断するか。30 秒ごとに見るので、実際には締切の 2.5〜3 分前ごろに判断する
# （直前ほどオッズが確定値に近いが、人が買う時間も残す）
LEAD_MIN, LEAD_MAX = 1.5, 3.0
INTERVAL_SEC = 30
REFUND_MARKS = ("F", "L", "K")  # フライング・出遅れ・欠場の艇の舟券は返還

_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[paper] {msg}", flush=True)


def due_races(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    lo = (now + timedelta(minutes=LEAD_MIN)).strftime("%H:%M")
    hi = (now + timedelta(minutes=LEAD_MAX)).strftime("%H:%M")
    return conn.execute(
        """SELECT * FROM programs p
           WHERE p.date = ? AND p.deadline IS NOT NULL AND p.deadline >= ? AND p.deadline <= ?
             AND NOT EXISTS (SELECT 1 FROM paper_checks c WHERE c.race_id = p.race_id)
           ORDER BY p.deadline""", (now.date().isoformat(), lo, hi)).fetchall()


def decide(conn: sqlite3.Connection, prog: sqlite3.Row, now: datetime) -> int:
    d = date.fromisoformat(prog["date"])
    deadline = datetime.combine(d, datetime.strptime(prog["deadline"], "%H:%M").time())
    notes = []
    try:
        w = weather.fetch("beforeinfo", d, prog["venue"], prog["race_no"])
    except Exception as e:  # noqa: BLE001
        w = {}
        notes.append(f"直前情報なし: {e}")
    cond = {"weather": w.get("weather"), "wind": w.get("wind_speed"),
            "wave": w.get("wave"), "temp": w.get("temperature")}

    rows = odds_api.fetch_win_place(d, prog["venue"], prog["race_no"])
    od: value.Odds = {}
    for r in rows:
        od.setdefault(r["bet_type"], {})[r["combo"]] = [r["odds"], r["odds_high"]]
    picks = []
    entries = [dict(e) for e in conn.execute(
        "SELECT * FROM program_entries WHERE race_id=? ORDER BY lane", (prog["race_id"],))]
    if not od.get("win"):
        notes.append("オッズなし")
    elif len(entries) != 6:
        notes.append("出走表が6艇でない")
    else:
        scores = score_entries(conn, prog["venue"], entries, cond, before_date=prog["date"])
        win_p = {str(s.lane): s.win_prob for s in scores}
        top2_p = {str(s.lane): s.top2_prob for s in scores}
        picks = value.select(win_p, top2_p, od)

    decided = now.isoformat(timespec="seconds")
    for p in picks:
        conn.execute(
            """INSERT OR IGNORE INTO paper_bets
               (race_id, strategy, bet_type, combo, prob, odds, ev, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (prog["race_id"], p.strategy, p.bet_type, p.combo, p.prob, p.odds, p.ev, decided))
    conn.execute(
        "INSERT OR REPLACE INTO paper_checks VALUES (?, ?, ?, ?, ?, ?)",
        (prog["race_id"], decided, round((deadline - now).total_seconds() / 60, 1),
         json.dumps(cond, ensure_ascii=False), len(picks), "; ".join(notes) or None))
    conn.commit()
    return len(picks)


def run_once(now: datetime | None = None) -> int:
    if not _lock.acquire(blocking=False):
        return 0
    try:
        now = now or datetime.now()
        conn = connect()
        total = 0
        for prog in due_races(conn, now):
            try:
                n = decide(conn, prog, datetime.now())
                total += n
                _log(f"{prog['race_id']} 締切{prog['deadline']} → {n}点")
            except Exception:  # noqa: BLE001  1レース失敗しても次へ
                traceback.print_exc()
        return total
    finally:
        _lock.release()


class PaperTrader:
    """サーバーと一緒に動き、30 秒ごとに締切の近いレースを判断する。"""

    def __init__(self, interval: int = INTERVAL_SEC):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="paper-trader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from . import quick
        while not self._stop.is_set():
            try:
                run_once()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:   # 終わったレースを、その日のうちに精算（仮想舟券・ペーパートレード）
                quick.settle(connect())
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            self._stop.wait(self.interval)


# ---- 成績の集計 -------------------------------------------------------------

@dataclass
class PaperStats:
    bets: int = 0
    settled: int = 0
    hits: int = 0
    refunds: int = 0
    invested: int = 0
    returned: int = 0

    @property
    def roi(self) -> float:
        return self.returned / self.invested if self.invested else 0.0

    @property
    def hit_rate(self) -> float:
        n = self.settled - self.refunds
        return self.hits / n if n else 0.0

    @property
    def profit(self) -> int:
        return self.returned - self.invested


def settled_bets(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """paper_bets に結果を突き合わせる。status: pending / hit / miss / refund"""
    sql = """
        SELECT b.*, p.date, p.venue, p.race_no, p.deadline,
               (SELECT 1 FROM races ra WHERE ra.race_id = b.race_id) AS has_k,
               q.refunds AS quick_refunds,
               r.finish_raw,
               pay.payout,
               o.odds AS closing_odds
        FROM paper_bets b
        JOIN programs p ON p.race_id = b.race_id
        LEFT JOIN quick_results q ON q.race_id = b.race_id
        LEFT JOIN results r ON r.race_id = b.race_id AND r.lane = CAST(b.combo AS INTEGER)
        LEFT JOIN payouts pay ON pay.race_id = b.race_id AND pay.bet_type = b.bet_type AND pay.combo = b.combo
        LEFT JOIN odds o ON o.race_id = b.race_id AND o.bet_type = b.bet_type AND o.combo = b.combo
        ORDER BY b.decided_at DESC, b.race_id, b.strategy"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    out = []
    for row in conn.execute(sql):
        d = dict(row)
        quick_refunds = set((d["quick_refunds"] or "").split(",")) - {""}
        if not (d["has_k"] or d["quick_refunds"] is not None):
            d["status"], d["returned"] = "pending", None
        elif (d["finish_raw"] and d["finish_raw"].startswith(REFUND_MARKS)) or d["combo"] in quick_refunds:
            d["status"], d["returned"] = "refund", d["stake"]
        elif d["payout"]:
            d["status"], d["returned"] = "hit", d["payout"] * d["stake"] // 100
        else:
            d["status"], d["returned"] = "miss", 0
        out.append(d)
    return out


def summarize(rows: list[dict]) -> dict[str, PaperStats]:
    stats = {s.key: PaperStats() for s in value.STRATEGIES}
    stats["合計"] = PaperStats()
    for r in rows:
        for key in (r["strategy"], "合計"):
            t = stats.setdefault(key, PaperStats())
            t.bets += 1
            if r["status"] == "pending":
                continue
            t.settled += 1
            t.invested += r["stake"]
            t.returned += r["returned"]
            t.hits += r["status"] == "hit"
            t.refunds += r["status"] == "refund"
    return stats


if __name__ == "__main__":
    n = run_once()
    print(f"{n} 点を記録しました")
