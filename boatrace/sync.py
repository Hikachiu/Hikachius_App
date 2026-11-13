"""データの自動取り込み。

「足りないものを埋める」方式なので、何回動かしても同じ結果になり、
PC が止まっていた日があっても次に動いたときに追いつく。

- 出走表: 今日の分（無ければ）。17時以降は明日の分も
- 結果:   最後に取り込んだ日の翌日〜昨日。23時以降は今日の分も
- 気温:   直近の結果で気温が空いているレース（1回あたり上限あり・1件1秒）

単体で動かす: python -m boatrace.sync
"""
import sqlite3
import threading
import traceback
from datetime import date, datetime, timedelta

from .db import connect
from .ingest import ingest_program_day, ingest_results_day, ingest_temps

INTERVAL_SEC = 60 * 60          # 1 時間ごと
MAX_BACKFILL_DAYS = 30          # DB が空のときに遡る日数
TEMPS_PER_RUN = 150             # 1 時間ごとに回るので、1 回あたりは控えめに

_lock = threading.Lock()        # 手動更新と定期更新が重ならないように


def _has_program(conn: sqlite3.Connection, d: date) -> bool:
    return conn.execute("SELECT 1 FROM programs WHERE date = ? LIMIT 1", (d.isoformat(),)).fetchone() is not None


def _log(msg: str) -> None:
    print(msg, flush=True)


def sync_once(now: datetime | None = None, log=_log) -> str:
    if not _lock.acquire(blocking=False):
        return "ほかの更新が動いているのでスキップ"
    try:
        return _sync(now or datetime.now(), log)
    finally:
        _lock.release()


def _sync(now: datetime, log) -> str:
    conn = connect()
    started = now.isoformat(timespec="seconds")
    today = now.date()
    msgs = []
    log(f"[sync] 開始 {started}")
    try:
        # 出走表
        targets = [today] + ([today + timedelta(days=1)] if now.hour >= 17 else [])
        for d in targets:
            if not _has_program(conn, d):
                n = ingest_program_day(conn, d)
                if n:
                    msgs.append(f"出走表 {d:%m/%d} {n}R")

        # 結果
        last = conn.execute("SELECT MAX(date) FROM races").fetchone()[0]
        start = date.fromisoformat(last) + timedelta(days=1) if last else today - timedelta(days=MAX_BACKFILL_DAYS)
        end = today if now.hour >= 23 else today - timedelta(days=1)
        d = start
        while d <= end:
            n = ingest_results_day(conn, d)
            if n:
                msgs.append(f"結果 {d:%m/%d} {n}R")
            d += timedelta(days=1)

        # 気温（直近 3 日分の空き）
        temp_start = today - timedelta(days=3)
        missing = conn.execute(
            "SELECT COUNT(*) FROM races WHERE temperature IS NULL AND date >= ?",
            (temp_start.isoformat(),)).fetchone()[0]
        if missing:
            n = ingest_temps(conn, temp_start, today, None, limit=TEMPS_PER_RUN)
            if n:
                msgs.append(f"気温 {n}R")

        message = "、".join(msgs) or "新しいデータなし"
        ok = 1
    except Exception as e:  # noqa: BLE001  落ちても次の回でやり直す
        traceback.print_exc()
        message = f"エラー: {e}"
        ok = 0
    conn.execute(
        "INSERT INTO sync_log (started, finished, ok, message) VALUES (?, ?, ?, ?)",
        (started, datetime.now().isoformat(timespec="seconds"), ok, message))
    conn.commit()
    log(f"[sync] {message}")
    return message


def last_sync(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()


class AutoSync:
    """サーバーと一緒に動く定期取り込み。"""

    def __init__(self, interval: int = INTERVAL_SEC):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="auto-sync", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            sync_once()
            self._stop.wait(self.interval)


if __name__ == "__main__":
    sync_once()
