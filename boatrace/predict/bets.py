"""着順確率から券種ごとの的中確率を出し、買い目をおすすめする。"""
import math
from dataclasses import dataclass
from itertools import combinations, permutations

BET_TYPES = {
    "win": "単勝",
    "place": "複勝",
    "exacta": "2連単",
    "quinella": "2連複",
    "wide": "拡連複",
    "trifecta": "3連単",
    "trio": "3連複",
}


def finish_order_probs(scores: dict[int, float], beta: float) -> dict[tuple[int, int, int], float]:
    """Plackett-Luce: 強さ w の艇から順に 1 着・2 着・3 着が決まるとみなした確率。"""
    w = {lane: math.exp(beta * s) for lane, s in scores.items()}
    total = sum(w.values())
    out = {}
    for a, b, c in permutations(w, 3):
        p1 = w[a] / total
        p2 = w[b] / (total - w[a])
        p3 = w[c] / (total - w[a] - w[b])
        out[(a, b, c)] = p1 * p2 * p3
    return out


def combo_probs(order_probs: dict[tuple[int, int, int], float], bet_type: str) -> dict[str, float]:
    lanes = sorted({x for o in order_probs for x in o})
    acc: dict[str, float] = {}

    def add(key: str, p: float) -> None:
        acc[key] = acc.get(key, 0.0) + p

    for (a, b, c), p in order_probs.items():
        if bet_type == "win":
            add(f"{a}", p)
        elif bet_type == "place":      # 6 艇立ては 2 着まで
            add(f"{a}", p)
            add(f"{b}", p)
        elif bet_type == "exacta":
            add(f"{a}-{b}", p)
        elif bet_type == "quinella":
            add("-".join(map(str, sorted((a, b)))), p)
        elif bet_type == "wide":
            for x, y in combinations(sorted((a, b, c)), 2):
                add(f"{x}-{y}", p)
        elif bet_type == "trifecta":
            add(f"{a}-{b}-{c}", p)
        elif bet_type == "trio":
            add("-".join(map(str, sorted((a, b, c)))), p)
        else:
            raise ValueError(bet_type)
    if bet_type in ("win", "place"):
        for lane in lanes:
            acc.setdefault(str(lane), 0.0)
    return acc


@dataclass
class Pick:
    combo: str
    prob: float
    odds: float | None = None

    @property
    def expected_value(self) -> float | None:
        return self.prob * self.odds if self.odds else None


def top_picks(order_probs, bet_type: str, points: int, odds: dict[str, float] | None = None) -> list[Pick]:
    probs = combo_probs(order_probs, bet_type)
    picks = [Pick(c, p, (odds or {}).get(c)) for c, p in probs.items()]
    if odds:
        picks.sort(key=lambda x: (x.expected_value or 0), reverse=True)
    else:
        picks.sort(key=lambda x: x.prob, reverse=True)
    return picks[:points]


@dataclass
class Recommendation:
    bet_type: str
    title: str
    reason: str
    picks: list[Pick]

    @property
    def hit_prob(self) -> float:
        return sum(p.prob for p in self.picks)


def recommend(order_probs, win_probs: dict[int, float]) -> Recommendation:
    """レースの「堅さ」を見て、券種と買い方を 1 つおすすめする。"""
    ranked = sorted(win_probs.items(), key=lambda x: x[1], reverse=True)
    (top, p1), (second, p2) = ranked[0], ranked[1]

    if p1 >= 0.55:
        picks = [p for p in top_picks(order_probs, "trifecta", 20) if p.combo.startswith(f"{top}-")][:6]
        return Recommendation(
            "trifecta", f"3連単 {top}号艇 1着固定・6点",
            f"{top}号艇の1着率が{p1:.0%}と抜けている。頭を固定して2・3着を広めに押さえる。",
            picks)
    if p1 + p2 >= 0.65:
        return Recommendation(
            "quinella", f"2連複 {min(top, second)}={max(top, second)} 中心・3点",
            f"{top}号艇と{second}号艇の2強（1着率 {p1:.0%} / {p2:.0%}）。どちらが勝っても拾える2連複が手堅い。",
            top_picks(order_probs, "quinella", 3))
    if p1 >= 0.35:
        return Recommendation(
            "trio", "3連複 上位4艇ボックス・4点",
            f"本命は{top}号艇（{p1:.0%}）だが抜けてはいない。着順を問わない3連複で上位4艇を押さえる。",
            top_picks(order_probs, "trio", 4))
    return Recommendation(
        "wide", "拡連複 上位3点",
        "混戦で1着の予想が難しいレース。3着以内に2艇入ればいい拡連複で的中率を優先する。",
        top_picks(order_probs, "wide", 3))
