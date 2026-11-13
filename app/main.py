"""Web 画面。起動: uvicorn app.main:app --reload"""
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boatrace.analysis.conditions import DIMENSIONS, racer_profile
from boatrace.config import VENUES
from boatrace.data import weather
from boatrace.db import connect
from boatrace.ingest import ingest_program_day
from boatrace.predict.bets import BET_TYPES, finish_order_probs, recommend, top_picks
from boatrace.predict.model import BETA, score_entries

HERE = Path(__file__).parent
app = FastAPI(title="競艇予測")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["VENUES"] = VENUES
templates.env.filters["pct"] = lambda v: f"{v * 100:.1f}%"


@app.get("/", response_class=HTMLResponse)
def index(request: Request, d: str | None = None):
    d = d or date.today().isoformat()
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM programs WHERE date = ? ORDER BY venue, race_no", (d,)).fetchall()
    by_venue: dict[str, list] = {}
    for r in rows:
        by_venue.setdefault(r["venue"], []).append(r)
    stats = conn.execute(
        "SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx, SUM(temperature IS NOT NULL) t FROM races"
    ).fetchone()
    return templates.TemplateResponse(request, "index.html",
                                      {"d": d, "by_venue": by_venue, "stats": stats})


@app.post("/fetch-program")
def fetch_program(d: str = Form(...)):
    ingest_program_day(connect(), date.fromisoformat(d))
    return RedirectResponse(f"/?d={d}", status_code=303)


def _to_int(v: str | None):
    return int(v) if v not in (None, "") else None


def _to_float(v: str | None):
    return float(v) if v not in (None, "") else None


@app.get("/race/{race_id}", response_class=HTMLResponse)
def race(request: Request, race_id: str, wind: str | None = None,
         wave: str | None = None, temp: str | None = None,
         bet: str = "trifecta", points: int = 6):
    # "weather" はモジュール名と被るので query_params から直接読む
    conn = connect()
    prog = conn.execute("SELECT * FROM programs WHERE race_id = ?", (race_id,)).fetchone()
    if prog is None:
        return HTMLResponse("出走表が見つかりません", status_code=404)
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM program_entries WHERE race_id = ? ORDER BY lane", (race_id,))]

    weather_val = request.query_params.get("weather")
    cond = {"weather": weather_val or None, "wind": _to_int(wind),
            "wave": _to_int(wave), "temp": _to_float(temp)}
    scores = score_entries(conn, prog["venue"], entries, cond, before_date=prog["date"])
    order = finish_order_probs({s.lane: s.score for s in scores}, BETA)
    rec = recommend(order, {s.lane: s.win_prob for s in scores})
    bet = bet if bet in BET_TYPES else "trifecta"
    points = max(1, min(points, 30))
    picks = top_picks(order, bet, points)

    entry_by_lane = {e["lane"]: e for e in entries}
    return templates.TemplateResponse(request, "race.html", {
        "prog": prog, "scores": scores, "entries": entry_by_lane, "cond": cond,
        "rec": rec, "bet": bet, "points": points, "picks": picks,
        "bet_types": BET_TYPES, "hit_prob": sum(p.prob for p in picks),
    })


@app.post("/race/{race_id}/beforeinfo")
def race_beforeinfo(race_id: str):
    """公式サイトの直前情報から気温・天候・風・波を取って、条件に入れて再表示する。"""
    conn = connect()
    prog = conn.execute("SELECT * FROM programs WHERE race_id = ?", (race_id,)).fetchone()
    w = weather.fetch("beforeinfo", date.fromisoformat(prog["date"]), prog["venue"], prog["race_no"])
    q = {"weather": w.get("weather", ""), "wind": w.get("wind_speed", ""),
         "wave": w.get("wave", ""), "temp": w.get("temperature", "")}
    return RedirectResponse(f"/race/{race_id}?{urlencode(q)}", status_code=303)


@app.get("/racer/{racer_id}", response_class=HTMLResponse)
def racer(request: Request, racer_id: str):
    conn = connect()
    prof = racer_profile(conn, racer_id)
    name_row = conn.execute(
        "SELECT racer_name FROM results WHERE racer_id = ? LIMIT 1", (racer_id,)).fetchone()
    return templates.TemplateResponse(request, "racer.html", {
        "prof": prof, "name": name_row["racer_name"] if name_row else racer_id,
        "dims": DIMENSIONS,
    })
