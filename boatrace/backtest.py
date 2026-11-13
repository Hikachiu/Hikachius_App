"""過去レースで「予想どおりに買っていたら」の回収率を測る。

  python -m boatrace.backtest --start 2026-10-27 --end 2026-11-04

- 出走表（programs）と結果（results / payouts）が両方あるレースが対象
- 予想にはそのレース日より前のデータだけを使う（未来の情報は混ぜない）
- 1 点 100 円で買ったとして計算する
- 返還（フライング艇の返還など）は考慮していない（外れ扱い）
"""
import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date

from .config import DATA_DIR
from .db import connect
from .predict.bets import BET_TYPES, finish_order_probs, recommend, top_picks
from .predict.model import BETA, score_entries

# 名前: (券種, 点数)
FIXED_STRATEGIES = {
    "単勝 本命1点": ("win", 1),
    "複勝 本命1点": ("place", 1),
    "2連単 上位2点": ("exacta", 2),
    "2連複 上位2点": ("quinella", 2),
    "拡連複 上位2点": ("wide", 2),
    "3連単 上位6点": ("trifecta", 6),
    "3連複 上位4点": ("trio", 4),
}
# 比較用: 予想を使わない買い方
BASELINES = {
    "【比較】1号艇の単勝": ("win", ["1"]),
    "【比較】3連単 1-2-3": ("trifecta", ["1-2-3"]),
}
WIN_PROB_BUCKETS = [(0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01)]


@dataclass
class Tally:
    races: int = 0
    tickets: int = 0
    hits: int = 0          # 1 点でも当たったレース数
    invested: int = 0
    returned: int = 0
    max_payout: int = 0

    def add(self, bet_type: str, combos: list[str], pay_table: dict) -> None:
        self.races += 1
        self.tickets += len(combos)
        self.invested += 100 * len(combos)
        got = sum(pay_table.get((bet_type, c), 0) for c in combos)
        if got:
            self.hits += 1
        self.returned += got
        self.max_payout = max(self.max_payout, got)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.races if self.races else 0.0

    @property
    def roi(self) -> float:
        return self.returned / self.invested if self.invested else 0.0


@dataclass
class Report:
    start: str
    end: str
    races: int = 0
    strategies: dict[str, Tally] = field(default_factory=lambda: defaultdict(Tally))
    rec_by_type: dict[str, Tally] = field(default_factory=lambda: defaultdict(Tally))
    win_by_conf: dict[str, Tally] = field(default_factory=lambda: defaultdict(Tally))
    # 予想 1 着率と実際の 1 着率の比較（確率が当たっているか）
    calib: dict[str, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0]))


def run(start: str, end: str, progress: bool = True) -> Report:
    conn = connect()
    races = conn.execute("""
        SELECT p.race_id, p.venue, p.date, ra.weather, ra.wind_speed, ra.wave, ra.temperature
        FROM programs p JOIN races ra ON ra.race_id = p.race_id
        WHERE p.date BETWEEN ? AND ? ORDER BY p.race_id""", (start, end)).fetchall()

    rep = Report(start, end)
    for i, r in enumerate(races, 1):
        entries = [dict(e) for e in conn.execute(
            "SELECT * FROM program_entries WHERE race_id=? ORDER BY lane", (r["race_id"],))]
        finishes = {row["lane"]: row["finish"] for row in conn.execute(
            "SELECT lane, finish FROM results WHERE race_id=?", (r["race_id"],))}
        if len(entries) != 6 or 1 not in finishes.values():
            continue
        pay = {(p["bet_type"], p["combo"]): p["payout"] for p in conn.execute(
            "SELECT bet_type, combo, payout FROM payouts WHERE race_id=?", (r["race_id"],))}

        # 天候などはレース後の記録値を使う（＝直前情報で入力した想定）
        cond = {"weather": r["weather"], "wind": r["wind_speed"],
                "wave": r["wave"], "temp": r["temperature"]}
        scores = score_entries(conn, r["venue"], entries, cond, before_date=r["date"])
        order = finish_order_probs({s.lane: s.score for s in scores}, BETA)
        win_probs = {s.lane: s.win_prob for s in scores}
        rep.races += 1

        rec = recommend(order, win_probs)
        combos = [p.combo for p in rec.picks]
        rep.strategies["★おすすめ"].add(rec.bet_type, combos, pay)
        rep.rec_by_type[BET_TYPES[rec.bet_type]].add(rec.bet_type, combos, pay)

        for name, (bt, n) in FIXED_STRATEGIES.items():
            rep.strategies[name].add(bt, [p.combo for p in top_picks(order, bt, n)], pay)
        for name, (bt, fixed) in BASELINES.items():
            rep.strategies[name].add(bt, fixed, pay)

        top_lane, top_p = max(win_probs.items(), key=lambda x: x[1])
        for lo, hi in WIN_PROB_BUCKETS:
            if lo <= top_p < hi:
                rep.win_by_conf[f"{lo:.0%}〜{min(hi, 1):.0%}"].add("win", [str(top_lane)], pay)

        for s in scores:
            b = min(int(s.win_prob * 10), 9)
            c = rep.calib[f"{b * 10}〜{b * 10 + 10}%"]
            c[0] += 1
            c[1] += s.win_prob
            c[2] += finishes.get(s.lane) == 1

        if progress and i % 100 == 0:
            print(f"  {i}/{len(races)} レース", flush=True)
    return rep


def _row(name: str, t: Tally) -> str:
    return (f"{name:<16}{t.races:>6}{t.tickets:>7}{t.hit_rate:>9.1%}"
            f"{t.invested:>11,}{t.returned:>11,}{t.roi:>9.1%}{t.max_payout:>10,}")


def print_report(rep: Report) -> None:
    head = f"{'買い方':<16}{'レース':>6}{'点数':>7}{'的中率':>9}{'投資(円)':>11}{'払戻(円)':>11}{'回収率':>9}{'最高払戻':>10}"
    print(f"\n=== バックテスト {rep.start} 〜 {rep.end}（{rep.races} レース・1点100円） ===\n")
    print(head)
    for name, t in rep.strategies.items():
        print(_row(name, t))
    print("\n--- おすすめの内訳（どの券種を選んだときに勝てているか） ---")
    print(head)
    for name, t in sorted(rep.rec_by_type.items()):
        print(_row(name, t))
    print("\n--- 本命の単勝を、予想1着率の高さ別に ---")
    print(head)
    for name, t in rep.win_by_conf.items():
        print(_row(name, t))
    print("\n--- 確率の当たり具合（予想1着率 vs 実際の1着率） ---")
    print(f"{'予想1着率':<12}{'艇数':>7}{'予想平均':>10}{'実際':>10}")
    for k in sorted(rep.calib, key=lambda x: int(x.split('〜')[0])):
        n, p, w = rep.calib[k]
        print(f"{k:<12}{n:>7}{p / n:>10.1%}{w / n:>10.1%}")


def save_report(rep: Report) -> None:
    out = DATA_DIR / "backtest" / f"{rep.start}_{rep.end}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    def tallies(d):
        return {k: {**asdict(v), "hit_rate": v.hit_rate, "roi": v.roi} for k, v in d.items()}

    out.write_text(json.dumps({
        "start": rep.start, "end": rep.end, "races": rep.races,
        "strategies": tallies(rep.strategies), "rec_by_type": tallies(rep.rec_by_type),
        "win_by_conf": tallies(rep.win_by_conf), "calib": rep.calib,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果を保存: {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="過去レースで回収率を測る")
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=date.today().isoformat())
    a = p.parse_args()
    rep = run(a.start, a.end)
    print_report(rep)
    save_report(rep)


if __name__ == "__main__":
    main()
