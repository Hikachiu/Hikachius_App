"""選手ごとに「どんな天候・気温・風・波のときに上位に来ているか」を集計する。"""
import sqlite3
from dataclasses import dataclass, field

# ---- 条件の区分け --------------------------------------------------------

def weather_bucket(w: str | None) -> str | None:
    if not w:
        return None
    for key, label in (("晴", "晴"), ("曇", "曇"), ("雨", "雨"), ("雪", "雪"), ("霧", "霧")):
        if key in w:
            return label
    return None


def wind_bucket(speed: int | None) -> str | None:
    if speed is None:
        return None
    if speed <= 2:
        return "弱風(0-2m)"
    if speed <= 4:
        return "中風(3-4m)"
    return "強風(5m以上)"


def wave_bucket(cm: int | None) -> str | None:
    if cm is None:
        return None
    if cm <= 3:
        return "静水(0-3cm)"
    if cm <= 7:
        return "やや波(4-7cm)"
    return "荒水(8cm以上)"


def temp_bucket(t: float | None) -> str | None:
    if t is None:
        return None
    if t < 10:
        return "寒い(10℃未満)"
    if t < 20:
        return "涼しい(10-20℃)"
    if t < 28:
        return "暖かい(20-28℃)"
    return "暑い(28℃以上)"


DIMENSIONS = {
    "weather": ("天候", weather_bucket),
    "wind": ("風", wind_bucket),
    "wave": ("波", wave_bucket),
    "temp": ("気温", temp_bucket),
}
# 各条件の列名（races テーブル）
DIM_COLUMN = {"weather": "weather", "wind": "wind_speed", "wave": "wave", "temp": "temperature"}


@dataclass
class Tally:
    starts: int = 0
    wins: int = 0
    top2: int = 0
    top3: int = 0

    def add(self, finish: int | None) -> None:
        self.starts += 1
        if finish is not None:
            self.wins += finish == 1
            self.top2 += finish <= 2
            self.top3 += finish <= 3

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.starts if self.starts else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.starts if self.starts else 0.0


@dataclass
class RacerProfile:
    racer_id: str
    overall: Tally = field(default_factory=Tally)
    by_lane: dict[int, Tally] = field(default_factory=dict)
    by_venue: dict[str, Tally] = field(default_factory=dict)
    # dims["weather"]["雨"] = Tally
    dims: dict[str, dict[str, Tally]] = field(default_factory=dict)


def racer_profile(conn: sqlite3.Connection, racer_id: str, before_date: str | None = None) -> RacerProfile:
    """before_date (YYYY-MM-DD) を渡すと、その日より前のレースだけで集計する（検証用）。"""
    sql = """SELECT r.finish, r.lane, ra.venue, ra.weather, ra.wind_speed, ra.wave, ra.temperature
             FROM results r JOIN races ra ON ra.race_id = r.race_id
             WHERE r.racer_id = ? AND r.finish_raw NOT LIKE 'K%'"""  # 欠場は除外
    params: list = [racer_id]
    if before_date:
        sql += " AND ra.date < ?"
        params.append(before_date)
    prof = RacerProfile(racer_id, dims={k: {} for k in DIMENSIONS})
    for row in conn.execute(sql, params):
        f = row["finish"]
        prof.overall.add(f)
        prof.by_lane.setdefault(row["lane"], Tally()).add(f)
        prof.by_venue.setdefault(row["venue"], Tally()).add(f)
        for dim, (_, bucket_fn) in DIMENSIONS.items():
            b = bucket_fn(row[DIM_COLUMN[dim]])
            if b:
                prof.dims[dim].setdefault(b, Tally()).add(f)
    return prof


_lane_prior_cache: dict[tuple, float] = {}


def lane_prior(conn: sqlite3.Connection, venue: str | None, lane: int) -> float:
    """その場・その枠の平均 3 連対率。データが少なければ全国の値、それも無ければ目安値。"""
    key = (venue, lane)
    if key in _lane_prior_cache:
        return _lane_prior_cache[key]
    fallback = {1: 0.75, 2: 0.55, 3: 0.50, 4: 0.45, 5: 0.35, 6: 0.25}[lane]
    value = fallback
    for v in (venue, None):
        sql = """SELECT COUNT(*) n, SUM(r.finish <= 3) t FROM results r
                 JOIN races ra ON ra.race_id = r.race_id WHERE r.lane = ?"""
        params: list = [lane]
        if v:
            sql += " AND ra.venue = ?"
            params.append(v)
        row = conn.execute(sql, params).fetchone()
        if row["n"] and row["n"] >= 300:
            value = row["t"] / row["n"]
            break
    _lane_prior_cache[key] = value
    return value
