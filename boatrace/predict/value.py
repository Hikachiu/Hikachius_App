"""期待値（確率 × オッズ）で買い目を選ぶ。

検証（backtest_ev）・ペーパートレード・画面で同じロジックを使う。
数値は 2026/11/05〜11/11 で決め、2026/10/20〜11/04（2,440 レース）で確かめたもの:
  - 混ぜ方: 自分の予想^0.4 × オッズから見た確率^0.6
  - 戦略 A/B/C: 確かめる期間の回収率 152% / 146% / 148%（締切時オッズでの検証）
"""
import math
from dataclasses import dataclass

BLEND = (0.4, 0.6)   # (自分の予想の重み, オッズの重み)


@dataclass(frozen=True)
class Strategy:
    key: str
    bet_type: str        # win / place
    threshold: float     # 期待値がこれ以上なら買う
    max_odds: float      # これより高いオッズは買わない
    label: str


STRATEGIES = [
    Strategy("A", "place", 1.2, 10, "複勝・期待値1.2以上・10倍まで"),
    Strategy("B", "place", 1.1, 5, "複勝・期待値1.1以上・5倍まで"),
    Strategy("C", "win", 1.1, 20, "単勝・期待値1.1以上・20倍まで"),
]
STRATEGY_BY_KEY = {s.key: s for s in STRATEGIES}

# odds の形: {"win": {"1": [2.8, None], ...}, "place": {"1": [1.8, 2.3], ...}}
Odds = dict[str, dict[str, list]]


def market_probs(odds: Odds) -> dict[str, float]:
    """単勝オッズから見た「みんなの予想」の1着率（控除分を割り戻して合計1にする）。"""
    inv = {k: 1.0 / v[0] for k, v in odds.get("win", {}).items() if v[0] and v[0] > 0}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s else {}


def blended_win(win_p: dict[str, float], odds: Odds, a: float = BLEND[0], b: float = BLEND[1]) -> dict[str, float]:
    """p ∝ (自分の予想)^a × (みんなの予想)^b。"""
    q = market_probs(odds)
    w = {k: math.exp(a * math.log(p) + b * math.log(q[k])) for k, p in win_p.items() if k in q and p > 0}
    s = sum(w.values())
    return {k: v / s for k, v in w.items()} if s else {}


def blended_top2(win_p: dict[str, float], top2_p: dict[str, float], bw: dict[str, float]) -> dict[str, float]:
    """2着以内の確率を、混ぜた1着率と自分の1着率の比で補正する。"""
    return {k: min(0.99, v * (bw.get(k, 0) / win_p[k]) ** 0.5) if win_p.get(k) else v
            for k, v in top2_p.items()}


def probabilities(win_p: dict[str, float], top2_p: dict[str, float], odds: Odds,
                  blend: tuple[float, float] = BLEND) -> dict[str, dict[str, float]]:
    bw = blended_win(win_p, odds, *blend)
    return {"win": bw, "place": blended_top2(win_p, top2_p, bw)}


@dataclass
class ValuePick:
    strategy: str
    bet_type: str
    combo: str
    prob: float
    odds: float          # 判断に使ったオッズ（複勝は下限）
    ev: float


def evaluate_all(win_p, top2_p, odds: Odds, blend=BLEND) -> list[dict]:
    """画面用: 単勝・複勝の全艇について、確率・オッズ・期待値と、当てはまる戦略。"""
    probs = probabilities(win_p, top2_p, odds, blend)
    rows = []
    for bt in ("win", "place"):
        for k, (lo, hi) in sorted(odds.get(bt, {}).items()):
            if lo is None or k not in probs[bt]:
                continue
            p = probs[bt][k]
            ev = p * lo
            hit = [s.key for s in STRATEGIES if s.bet_type == bt and lo <= s.max_odds and ev >= s.threshold]
            rows.append({"bet_type": bt, "combo": k, "prob": p, "odds": lo, "odds_high": hi,
                         "ev": ev, "strategies": hit})
    return rows


def select(win_p, top2_p, odds: Odds, strategies=STRATEGIES, blend=BLEND) -> list[ValuePick]:
    probs = probabilities(win_p, top2_p, odds, blend)
    picks = []
    for s in strategies:
        for k, (lo, _hi) in odds.get(s.bet_type, {}).items():
            if lo is None or lo > s.max_odds or k not in probs[s.bet_type]:
                continue
            ev = probs[s.bet_type][k] * lo
            if ev >= s.threshold:
                picks.append(ValuePick(s.key, s.bet_type, k, probs[s.bet_type][k], lo, ev))
    return picks
