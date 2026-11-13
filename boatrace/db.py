import sqlite3

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_id     TEXT PRIMARY KEY,   -- YYYYMMDD-場-R
    date        TEXT NOT NULL,      -- YYYY-MM-DD
    venue       TEXT NOT NULL,      -- 場コード 01-24
    race_no     INTEGER NOT NULL,
    race_name   TEXT,
    distance    INTEGER,
    weather     TEXT,               -- 晴 / 曇り / 雨 / 雪 / 霧
    wind_dir    TEXT,
    wind_speed  INTEGER,            -- m
    wave        INTEGER,            -- cm
    temperature REAL,               -- ℃（公式サイトから別途取得）
    water_temp  REAL
);

CREATE TABLE IF NOT EXISTS results (
    race_id      TEXT NOT NULL,
    lane         INTEGER NOT NULL,  -- 艇番
    racer_id     TEXT NOT NULL,     -- 登録番号
    racer_name   TEXT,
    motor_no     INTEGER,
    boat_no      INTEGER,
    exhibit_time REAL,
    course       INTEGER,           -- 進入コース
    start_timing TEXT,
    race_time    TEXT,
    finish       INTEGER,           -- 着順（失格・欠場などは NULL）
    finish_raw   TEXT,              -- 01 / F / L0 / K0 / S1 など元の表記
    PRIMARY KEY (race_id, lane)
);
CREATE INDEX IF NOT EXISTS idx_results_racer ON results(racer_id);

CREATE TABLE IF NOT EXISTS payouts (
    race_id    TEXT NOT NULL,
    bet_type   TEXT NOT NULL,       -- win / place / exacta / quinella / wide / trifecta / trio
    combo      TEXT NOT NULL,       -- 1-2-3 など
    payout     INTEGER,
    popularity INTEGER,
    PRIMARY KEY (race_id, bet_type, combo)
);

CREATE TABLE IF NOT EXISTS programs (
    race_id   TEXT PRIMARY KEY,
    date      TEXT NOT NULL,
    venue     TEXT NOT NULL,
    race_no   INTEGER NOT NULL,
    race_name TEXT,
    distance  INTEGER,
    deadline  TEXT
);

CREATE TABLE IF NOT EXISTS program_entries (
    race_id        TEXT NOT NULL,
    lane           INTEGER NOT NULL,
    racer_id       TEXT NOT NULL,
    racer_name     TEXT,
    age            INTEGER,
    branch         TEXT,
    weight         INTEGER,
    grade          TEXT,            -- A1 / A2 / B1 / B2
    nat_win_rate   REAL,            -- 全国勝率
    nat_2rate      REAL,            -- 全国2連率
    local_win_rate REAL,            -- 当地勝率
    local_2rate    REAL,
    motor_no       INTEGER,
    motor_2rate    REAL,
    boat_no        INTEGER,
    boat_2rate     REAL,
    PRIMARY KEY (race_id, lane)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started  TEXT,
    finished TEXT,
    ok       INTEGER,
    message  TEXT
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 自動取り込みと画面が同時に DB を使うので、ロック待ちを長めにする
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return conn


# あとから足した列（既存の DB にも追加する）
MIGRATIONS = [
    ("programs", "deadline", "TEXT"),   # 締切予定時刻 HH:MM
]
