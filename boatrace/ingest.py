"""データ取り込みのコマンド。

  python -m boatrace.ingest history --start 2025-11-01 --end 2026-11-12
  python -m boatrace.ingest program --date 2026-11-14
  python -m boatrace.ingest temps --start 2026-10-01 --end 2026-11-12 [--venue 24]
"""
import argparse
import sqlite3
from datetime import date, timedelta

from .data import download, parse_program, parse_results, weather
from .db import connect


def _upsert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)})")
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def ingest_results_day(conn: sqlite3.Connection, d: date) -> int:
    text = download.fetch_text("K", d)
    if text is None:
        return 0
    races, results, payouts = parse_results.parse(text, d)
    # 気温など、別途取得済みの値を消さないように races は個別に更新する
    for r in races:
        conn.execute(
            """INSERT INTO races (race_id, date, venue, race_no, race_name, distance,
                                  weather, wind_dir, wind_speed, wave)
               VALUES (:race_id, :date, :venue, :race_no, :race_name, :distance,
                       :weather, :wind_dir, :wind_speed, :wave)
               ON CONFLICT(race_id) DO UPDATE SET
                 race_name=excluded.race_name, distance=excluded.distance,
                 weather=excluded.weather, wind_dir=excluded.wind_dir,
                 wind_speed=excluded.wind_speed, wave=excluded.wave""", r)
    _upsert(conn, "results", results)
    _upsert(conn, "payouts", payouts)
    conn.commit()
    return len(races)


def ingest_program_day(conn: sqlite3.Connection, d: date) -> int:
    text = download.fetch_text("B", d, force=True)
    if text is None:
        return 0
    programs, entries = parse_program.parse(text, d)
    _upsert(conn, "programs", programs)
    _upsert(conn, "program_entries", entries)
    conn.commit()
    return len(programs)


def ingest_temps(conn: sqlite3.Connection, start: date, end: date, venue: str | None,
                 limit: int | None = None) -> int:
    sql = "SELECT race_id, date, venue, race_no FROM races WHERE temperature IS NULL AND date BETWEEN ? AND ?"
    params: list = [start.isoformat(), end.isoformat()]
    if venue:
        sql += " AND venue = ?"
        params.append(venue)
    sql += " ORDER BY date DESC, venue, race_no"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    done = 0
    for i, r in enumerate(rows, 1):
        try:
            w = weather.fetch("raceresult", date.fromisoformat(r["date"]), r["venue"], r["race_no"])
        except Exception as e:  # noqa: BLE001  1件失敗しても続ける
            print(f"  {r['race_id']}: 取得失敗 {e}", flush=True)
            continue
        if "temperature" in w:
            conn.execute("UPDATE races SET temperature=?, water_temp=? WHERE race_id=?",
                         (w["temperature"], w.get("water_temp"), r["race_id"]))
            conn.commit()   # 途中で止まっても取れた分は残す
            done += 1
        if i % 50 == 0:
            print(f"  気温 {i}/{len(rows)} 件", flush=True)
    conn.commit()
    return done


def main() -> None:
    p = argparse.ArgumentParser(description="競艇データの取り込み")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("history", help="競走成績（過去の結果・天候）を取り込む")
    h.add_argument("--start", required=True)
    h.add_argument("--end", required=True)
    g = sub.add_parser("program", help="番組表（出走表）を取り込む")
    g.add_argument("--date", default=date.today().isoformat())
    t = sub.add_parser("temps", help="過去レースの気温・水温を公式サイトから補う（1件1秒）")
    t.add_argument("--start", required=True)
    t.add_argument("--end", required=True)
    t.add_argument("--venue")
    a = p.parse_args()

    conn = connect()
    if a.cmd == "history":
        for d in _daterange(date.fromisoformat(a.start), date.fromisoformat(a.end)):
            n = ingest_results_day(conn, d)
            print(f"{d}: {n} レース")
    elif a.cmd == "program":
        n = ingest_program_day(conn, date.fromisoformat(a.date))
        print(f"{a.date}: 出走表 {n} レース")
    elif a.cmd == "temps":
        n = ingest_temps(conn, date.fromisoformat(a.start), date.fromisoformat(a.end), a.venue)
        print(f"気温を {n} レース分取得しました")


if __name__ == "__main__":
    main()
