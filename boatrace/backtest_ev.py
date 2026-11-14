"""期待値（予想の確率 × オッズ）で買う戦略の検証。

  python -m boatrace.backtest_ev --fit 2026-11-05:2026-11-11 --test 2026-10-20:2026-11-04

流れ:
  1. 各レースの予想（1着率・2着以内率）を、そのレース日より前のデータだけで計算する
     （data/backtest/preds_*.json にキャッシュ）
  2. 「ルールを決める期間（fit）」で、確率の混ぜ方（自分の予想 vs オッズ）と
     買う条件（期待値のしきい値・オッズの上限）を決める
  3. 決めたルールを「確かめる期間（test）」にそのまま当てはめて回収率を出す

オッズは締切時オッズ（締切の瞬間の最終オッズ）。実際に買うのは締切前なので、
本番の成績はこれより少し悪くなる可能性がある。
"""
import argparse
import json
import math
from dataclasses import dataclass

from .config import DATA_DIR
from .db import connect
from .predict import value
from .predict.model import score_entries

CACHE_DIR = DATA_DIR / "backtest"


# ---- 1. 予想の計算（キャッシュ付き） ----------------------------------------

def build_predictions(start: str, end: str, refresh: bool = False) -> list[dict]:
    path = CACHE_DIR / f"preds_{start}_{end}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    conn = connect()
    races = conn.execute("""
        SELECT p.race_id, p.venue, p.date, ra.weather, ra.wind_speed, ra.wave, ra.temperature
        FROM programs p JOIN races ra ON ra.race_id = p.race_id
        WHERE p.date BETWEEN ? AND ?
          AND EXISTS (SELECT 1 FROM odds o WHERE o.race_id = p.race_id AND o.bet_type = 'win')
        ORDER BY p.race_id""", (start, end)).fetchall()
    out = []
    for i, r in enumerate(races, 1):
        rid = r["race_id"]
        entries = [dict(e) for e in conn.execute(
            "SELECT * FROM program_entries WHERE race_id=? ORDER BY lane", (rid,))]
        fin = {x["lane"]: x["finish"] for x in conn.execute(
            "SELECT lane, finish FROM results WHERE race_id=?", (rid,))}
        if len(entries) != 6 or 1 not in fin.values():
            continue
        odds = {}
        for o in conn.execute("SELECT bet_type, combo, odds, odds_high FROM odds WHERE race_id=?", (rid,)):
            odds.setdefault(o["bet_type"], {})[o["combo"]] = [o["odds"], o["odds_high"]]
        pay = {}
        for p in conn.execute(
                "SELECT bet_type, combo, payout FROM payouts WHERE race_id=? AND bet_type IN ('win','place')", (rid,)):
            pay.setdefault(p["bet_type"], {})[p["combo"]] = p["payout"]
        cond = {"weather": r["weather"], "wind": r["wind_speed"], "wave": r["wave"], "temp": r["temperature"]}
        scores = score_entries(conn, r["venue"], entries, cond, before_date=r["date"])
        out.append({
            "race_id": rid,
            "score": {str(s.lane): s.score for s in scores},
            "win_p": {str(s.lane): s.win_prob for s in scores},
            "top2_p": {str(s.lane): s.top2_prob for s in scores},
            "odds": odds, "pay": pay,
            "winner": str(next(k for k, v in fin.items() if v == 1)),
        })
        if i % 200 == 0:
            print(f"  予想 {i}/{len(races)}", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ---- 2. 確率の混ぜ方 ---------------------------------------------------------

def market_probs(race: dict) -> dict[str, float]:
    return value.market_probs(race["odds"])


def blended_probs(race: dict, a: float, b: float) -> dict[str, float]:
    """p ∝ (自分の予想)^a × (みんなの予想)^b。a=1,b=0 なら自分の予想そのまま。"""
    return value.blended_win(race["win_p"], race["odds"], a, b)


def fit_blend(races: list[dict]) -> tuple[float, float, list]:
    """1着の対数尤度が最大になる (a, b) を探す。"""
    grid = []
    for a in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2]:
        for b in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2]:
            if a == b == 0:
                continue
            ll = 0.0
            for r in races:
                p = blended_probs(r, a, b).get(r["winner"])
                ll += math.log(max(p or 1e-6, 1e-6))
            grid.append((ll / len(races), a, b))
    grid.sort(reverse=True)
    return grid[0][1], grid[0][2], grid


# ---- 3. 買い方のルールと評価 ------------------------------------------------

@dataclass
class Rule:
    bet_type: str          # win / place
    prob: str              # own / blend
    threshold: float       # 期待値がこれ以上なら買う
    max_odds: float        # これより高いオッズは買わない（大穴は確率がブレやすい）
    one_per_race: bool     # 1レースで期待値が一番高い1点だけ

    def label(self) -> str:
        bt = "単勝" if self.bet_type == "win" else "複勝"
        pr = "自分の予想" if self.prob == "own" else "混ぜた予想"
        one = "・1R1点" if self.one_per_race else ""
        return f"{bt} / {pr} / 期待値≥{self.threshold:.2f} / オッズ≤{self.max_odds:g}{one}"


@dataclass
class Result:
    races: int = 0
    bets: int = 0
    hits: int = 0
    invested: int = 0
    returned: int = 0
    sq: float = 0.0        # 払戻の二乗和（ばらつきの計算用）

    @property
    def roi(self) -> float:
        return self.returned / self.invested if self.invested else 0.0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.bets if self.bets else 0.0

    @property
    def roi_ci(self) -> tuple[float, float]:
        """回収率の95%信頼区間（ざっくり）。幅が広ければ、まだまぐれと区別できない。"""
        if self.bets < 2:
            return (0.0, 0.0)
        mean = self.returned / self.bets
        var = self.sq / self.bets - mean * mean
        se = math.sqrt(max(var, 0) / self.bets)
        return ((mean - 1.96 * se) / 100, (mean + 1.96 * se) / 100)


def evaluate(races: list[dict], rule: Rule, blend: tuple[float, float]) -> Result:
    res = Result()
    for r in races:
        odds_t = r["odds"].get(rule.bet_type, {})
        if not odds_t:
            continue
        if rule.bet_type == "win":
            probs = r["win_p"] if rule.prob == "own" else blended_probs(r, *blend)
        else:
            # 複勝は2着以内の確率。混ぜた予想は1着率の比で自分の2着以内率を補正する
            probs = r["top2_p"]
            if rule.prob == "blend":
                probs = value.blended_top2(r["win_p"], probs, blended_probs(r, *blend))
        cands = []
        for k, (lo, _hi) in odds_t.items():
            if lo is None or lo > rule.max_odds or k not in probs:
                continue
            ev = probs[k] * lo          # 複勝は下限オッズで計算（控えめ）
            if ev >= rule.threshold:
                cands.append((ev, k))
        if not cands:
            continue
        if rule.one_per_race:
            cands = [max(cands)]
        res.races += 1
        for _ev, k in cands:
            got = r["pay"].get(rule.bet_type, {}).get(k, 0)
            res.bets += 1
            res.invested += 100
            res.returned += got
            res.sq += got * got
            res.hits += got > 0
    return res


def all_rules() -> list[Rule]:
    rules = []
    for bt in ("win", "place"):
        for prob in ("own", "blend"):
            for th in (1.0, 1.05, 1.1, 1.2, 1.3, 1.5):
                for mo in ((5, 10, 20, 50) if bt == "win" else (3, 5, 10)):
                    for one in (False, True):
                        rules.append(Rule(bt, prob, th, mo, one))
    return rules


def baselines(races: list[dict]) -> dict[str, Result]:
    out = {"【比較】単勝 1番人気": Result(), "【比較】単勝 自分の本命": Result(), "【比較】複勝 1番人気": Result()}
    for r in races:
        win = r["odds"].get("win", {})
        if not win:
            continue
        fav = min(win, key=lambda k: win[k][0])
        mine = max(r["win_p"], key=r["win_p"].get)
        for name, bt, k in (("【比較】単勝 1番人気", "win", fav), ("【比較】単勝 自分の本命", "win", mine),
                            ("【比較】複勝 1番人気", "place", fav)):
            got = r["pay"].get(bt, {}).get(k, 0)
            t = out[name]
            t.races += 1; t.bets += 1; t.invested += 100; t.returned += got; t.sq += got * got; t.hits += got > 0
    return out


def _row(name: str, t: Result) -> str:
    lo, hi = t.roi_ci
    return (f"{name:<52}{t.bets:>6}{t.hit_rate:>8.1%}{t.roi:>9.1%}"
            f"   [{lo:>6.1%} 〜 {hi:>6.1%}]{t.returned - t.invested:>+10,}")


HEAD = f"{'買い方':<52}{'点数':>6}{'的中率':>8}{'回収率':>9}   {'95%の範囲':^20}{'収支(円)':>10}"


def main() -> None:
    ap = argparse.ArgumentParser(description="期待値で買う戦略の検証")
    ap.add_argument("--fit", required=True, help="ルールを決める期間 START:END")
    ap.add_argument("--test", required=True, help="確かめる期間 START:END")
    ap.add_argument("--min-bets", type=int, default=150, help="fit で採用するルールの最低点数")
    ap.add_argument("--refresh", action="store_true", help="予想のキャッシュを作り直す")
    a = ap.parse_args()

    fit = build_predictions(*a.fit.split(":"), refresh=a.refresh)
    test = build_predictions(*a.test.split(":"), refresh=a.refresh)
    print(f"\nルールを決める期間 {a.fit}: {len(fit)} レース / 確かめる期間 {a.test}: {len(test)} レース")

    ba, bb, grid = fit_blend(fit)
    print(f"\n--- 確率の混ぜ方（fit で決定）: 自分の予想^{ba} × みんなの予想^{bb}")
    for ll, x, y in grid[:3]:
        print(f"    a={x} b={y}  平均対数尤度 {ll:.4f}")
    own_ll = sum(math.log(max(r['win_p'][r['winner']], 1e-6)) for r in fit) / len(fit)
    mk_ll = sum(math.log(max(market_probs(r).get(r['winner'], 1e-6), 1e-6)) for r in fit) / len(fit)
    print(f"    参考: 自分の予想だけ {own_ll:.4f} / みんなの予想（オッズ）だけ {mk_ll:.4f}")

    print("\n=== 予想を使わない買い方との比較 ===")
    print(HEAD)
    for name, t in baselines(test).items():
        print(_row(name, t))

    scored = []
    for rule in all_rules():
        f = evaluate(fit, rule, (ba, bb))
        if f.bets >= a.min_bets:
            scored.append((f.roi, rule, f))
    scored.sort(key=lambda x: x[0], reverse=True)

    print(f"\n=== fit の成績が良かったルール上位10（{a.min_bets}点以上買ったもの） → test にそのまま当てはめ ===")
    print("（fit で良く見えても test で崩れたら、それはまぐれ）")
    for roi, rule, f in scored[:10]:
        t = evaluate(test, rule, (ba, bb))
        print(f"\n  {rule.label()}")
        print("    fit  " + _row("", f)[52:])
        print("    test " + _row("", t)[52:])

    report = {
        "fit": a.fit, "test": a.test, "blend": [ba, bb],
        "top": [{"rule": r.label(), "fit_roi": f.roi, "fit_bets": f.bets,
                 "test_roi": (t := evaluate(test, r, (ba, bb))).roi, "test_bets": t.bets,
                 "test_ci": t.roi_ci} for _, r, f in scored[:10]],
    }
    out = CACHE_DIR / f"ev_{a.fit.replace(':', '_')}__{a.test.replace(':', '_')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n結果を保存: {out}")


if __name__ == "__main__":
    main()
