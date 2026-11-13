"""各艇の強さスコアを出して、着順の確率に変換する。

v1 はルールベース＋統計（機械学習は後で差し替える前提）:
  1. その場・その枠の平均 3 連対率を出発点にする
  2. 選手のその枠での実績で補正（走数が少ないほど平均に寄せる）
  3. 今回の天候・風・波・気温と同じ条件での実績で補正
  4. 全国勝率・モーター 2 連率で補正
  5. スコアを Plackett-Luce モデルで「1 着・2 着・3 着の確率」に変換
"""
import math
import sqlite3
from dataclasses import dataclass, field

from ..analysis.conditions import DIMENSIONS, lane_prior, racer_profile

SHRINK_LANE = 20      # 枠別実績を平均に寄せる強さ（この走数で半々）
SHRINK_COND = 15      # 条件別実績を平均に寄せる強さ
COND_WEIGHT = 0.6     # 条件補正の効き具合
WIN_RATE_WEIGHT = 0.25
MOTOR_WEIGHT = 1.5
BETA = 1.1            # スコア差を確率差に変換する強さ（2026/11/5-11 の 984 レースで対数尤度最大）


def _logit(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


@dataclass
class ConditionNote:
    label: str          # 例: 天候「雨」
    starts: int
    top3_rate: float
    delta: float        # スコアへの寄与（+ なら得意）


@dataclass
class EntryScore:
    lane: int
    racer_id: str
    racer_name: str
    grade: str | None
    score: float
    base_top3: float                    # 枠 × 選手実績からの 3 連対率
    notes: list[ConditionNote] = field(default_factory=list)
    win_prob: float = 0.0
    top2_prob: float = 0.0
    top3_prob: float = 0.0


def score_entries(conn: sqlite3.Connection, venue: str, entries: list[dict],
                  cond: dict, before_date: str | None = None) -> list[EntryScore]:
    """entries: program_entries の行。cond: {"weather": "雨", "wind": 4, "wave": 5, "temp": 18.5}"""
    scores: list[EntryScore] = []
    for e in entries:
        lane = e["lane"]
        prof = racer_profile(conn, e["racer_id"], before_date)
        prior = lane_prior(conn, venue, lane)

        lane_t = prof.by_lane.get(lane)
        lane_n = lane_t.starts if lane_t else 0
        lane_top3 = lane_t.top3 if lane_t else 0
        base = (lane_top3 + SHRINK_LANE * prior) / (lane_n + SHRINK_LANE)
        s = _logit(base)

        # 条件別の補正: 選手の「その条件での 3 連対率」と「全体の 3 連対率」の差
        overall = prof.overall.top3_rate if prof.overall.starts else prior
        notes = []
        for dim, (label, bucket_fn) in DIMENSIONS.items():
            b = bucket_fn(cond.get(dim))
            if not b:
                continue
            t = prof.dims[dim].get(b)
            n = t.starts if t else 0
            if n == 0:
                notes.append(ConditionNote(f"{label}「{b}」", 0, 0.0, 0.0))
                continue
            shrunk = (t.top3 + SHRINK_COND * overall) / (n + SHRINK_COND)
            delta = COND_WEIGHT * (_logit(shrunk) - _logit(overall))
            s += delta
            notes.append(ConditionNote(f"{label}「{b}」", n, t.top3_rate, delta))

        if e.get("nat_win_rate"):
            s += WIN_RATE_WEIGHT * (e["nat_win_rate"] - 5.5)
        if e.get("motor_2rate"):
            s += MOTOR_WEIGHT * (e["motor_2rate"] - 33.0) / 100.0

        scores.append(EntryScore(lane, e["racer_id"], e.get("racer_name") or "",
                                 e.get("grade"), s, base, notes))

    _fill_finish_probs(scores)
    return scores


def _fill_finish_probs(scores: list[EntryScore]) -> None:
    from .bets import finish_order_probs
    probs = finish_order_probs({x.lane: x.score for x in scores}, BETA)
    for x in scores:
        x.win_prob = sum(p for o, p in probs.items() if o[0] == x.lane)
        x.top2_prob = sum(p for o, p in probs.items() if x.lane in o[:2])
        x.top3_prob = sum(p for o, p in probs.items() if x.lane in o)
