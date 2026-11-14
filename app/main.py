"""Web 画面。起動: start_server.bat（家の Wi-Fi のスマホからも見られる）

環境変数 BOATRACE_AUTO_SYNC=0 で自動取り込みを止められる。
"""
import json
import os
import socket
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boatrace.analysis.conditions import DIMENSIONS, racer_profile
from boatrace.config import DATA_DIR, VENUES
from boatrace.data import weather
from boatrace.db import connect
from boatrace.ingest import ingest_program_day
from boatrace.predict.bets import (BET_GUIDE, BET_TYPES, finish_order_probs, hit_probability,
                                   recommend, top_picks)
from boatrace.predict.model import BETA, score_entries
from boatrace.data import odds as odds_api
from boatrace.paper import PaperTrader, settled_bets, summarize
from boatrace.predict import value
from boatrace.sync import AutoSync, last_sync, sync_once
from boatrace import wallet

HERE = Path(__file__).parent
BACKTEST_DIR = DATA_DIR / "backtest"
# 公式のインターネット投票（テレボート）。購入は本人がログインして行う
TELEBOAT_URL = "https://ib.mbrace.or.jp/"

GRADE_LABEL = {"A1": "トップ級", "A2": "上位", "B1": "中堅", "B2": "新人・下位"}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    workers = []
    if os.environ.get("BOATRACE_AUTO_SYNC", "1") != "0":
        workers.append(AutoSync())
    if os.environ.get("BOATRACE_PAPER", "1") != "0":
        workers.append(PaperTrader())
    for w in workers:
        w.start()
    yield
    for w in workers:
        w.stop()


app = FastAPI(title="競艇予測", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(VENUES=VENUES, BET_TYPES=BET_TYPES, BET_GUIDE=BET_GUIDE,
                             GRADE_LABEL=GRADE_LABEL, TELEBOAT_URL=TELEBOAT_URL)
templates.env.filters["pct"] = lambda v: f"{v * 100:.1f}%"
templates.env.filters["pct0"] = lambda v: f"{v * 100:.0f}%"
templates.env.filters["yen"] = lambda v: f"{int(v):,}円"


def _render(request: Request, template: str, tab: str, /, **ctx):
    return templates.TemplateResponse(
        request, template, {"tab": tab, "sync": last_sync(connect()), **ctx})


def lan_ip() -> str | None:
    """家の Wi-Fi の中でのこの PC のアドレス（実際には通信しない）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _to_int(v):
    return int(v) if v not in (None, "") else None


def _to_float(v):
    return float(v) if v not in (None, "") else None


def load_backtests() -> list[dict]:
    if not BACKTEST_DIR.exists():
        return []
    # backtest.py の結果だけ（YYYY-MM-DD_YYYY-MM-DD.json）。preds_* / ev_* は別の形式
    reps = [json.loads(p.read_text(encoding="utf-8"))
            for p in BACKTEST_DIR.glob("????-??-??_????-??-??.json")]
    # レース数の多い（＝信頼できる）検証を先に
    return sorted(reps, key=lambda r: r["races"], reverse=True)


def load_ev_report() -> dict | None:
    """backtest_ev.py の最新の結果。"""
    files = sorted(BACKTEST_DIR.glob("ev_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if BACKTEST_DIR.exists() else []
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None


# ---- 今日のレース ------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, d: str | None = None, ended: int = 0):
    today = date.today().isoformat()
    d = d or today
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM programs WHERE date = ? ORDER BY venue, race_no", (d,)).fetchall()
    by_venue: dict[str, list] = {}
    for r in rows:
        by_venue.setdefault(r["venue"], []).append(r)

    soon = []
    if d == today:
        now = datetime.now().strftime("%H:%M")
        soon = [r for r in rows if r["deadline"] and r["deadline"] > now]
        soon.sort(key=lambda r: r["deadline"])
        soon = soon[:6]
    stats = conn.execute(
        "SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM races").fetchone()
    return _render(request, "index.html", "home", d=d, today=today,
                   by_venue=by_venue, soon=soon, stats=stats, ended=ended)


@app.post("/fetch-program")
def fetch_program(d: str = Form(...)):
    ingest_program_day(connect(), date.fromisoformat(d))
    return RedirectResponse(f"/?d={d}", status_code=303)


@app.get("/next")
def next_race():
    """「レース予想」タブ: 次に締切が来るレースへ。"""
    conn = connect()
    now = datetime.now().strftime("%H:%M")
    r = conn.execute(
        "SELECT race_id FROM programs WHERE date = ? AND deadline > ? ORDER BY deadline LIMIT 1",
        (date.today().isoformat(), now)).fetchone()
    if r is None:  # 今日のレースが全部終わっていたら、読み込み済みの次の開催日の最初のレース
        r = conn.execute(
            "SELECT race_id FROM programs WHERE date > ? ORDER BY date, deadline LIMIT 1",
            (date.today().isoformat(),)).fetchone()
    return RedirectResponse(f"/race/{r['race_id']}" if r else "/?ended=1", status_code=303)


# ---- レース予想 --------------------------------------------------------------

def _race_type(p1: float, p2: float) -> tuple[str, str, str]:
    """(ラベル, 説明, CSS クラス)"""
    if p1 >= 0.55:
        return "堅いレース", "本命がはっきりしていて、順当に決まりやすい。そのぶん配当は低め。", "solid"
    if p1 + p2 >= 0.65:
        return "2強のレース", "2艇が抜けている。どちらが勝つかがカギ。", "two"
    if p1 >= 0.35:
        return "やや混戦", "本命はいるけど絶対ではない。広めに買うのがおすすめ。", "mixed"
    return "大混戦", "どの艇にもチャンスがある難しいレース。見送るのもアリ。", "chaos"


@app.get("/race/{race_id}", response_class=HTMLResponse)
def race(request: Request, race_id: str, wind: str | None = None, wave: str | None = None,
         temp: str | None = None, bet: str = "trifecta", points: int = 6, budget: int = 1000,
         check_value: int = 0):
    conn = connect()
    prog = conn.execute("SELECT * FROM programs WHERE race_id = ?", (race_id,)).fetchone()
    if prog is None:
        return HTMLResponse("出走表が見つかりません", status_code=404)
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM program_entries WHERE race_id = ? ORDER BY lane", (race_id,))]

    # "weather" はモジュール名と被るので query_params から直接読む
    cond = {"weather": request.query_params.get("weather") or None, "wind": _to_int(wind),
            "wave": _to_int(wave), "temp": _to_float(temp)}
    scores = score_entries(conn, prog["venue"], entries, cond, before_date=prog["date"])
    order = finish_order_probs({s.lane: s.score for s in scores}, BETA)
    win_probs = {s.lane: s.win_prob for s in scores}
    rec = recommend(order, win_probs)

    ranked = sorted(win_probs.values(), reverse=True)
    race_type = _race_type(ranked[0], ranked[1])

    bet = bet if bet in BET_TYPES else "trifecta"
    points = max(1, min(points, 20))
    budget = max(100, min(budget, 1_000_000))
    picks = top_picks(order, bet, points)

    def per_ticket(n: int) -> int:
        return max(100, budget // n // 100 * 100)

    # 同じレースの前後
    siblings = conn.execute(
        "SELECT race_id, race_no FROM programs WHERE date = ? AND venue = ? ORDER BY race_no",
        (prog["date"], prog["venue"])).fetchall()

    # このおすすめの券種が、過去の検証でどうだったか
    past = None
    reps = load_backtests()
    if reps:
        t = reps[0]["rec_by_type"].get(BET_TYPES[rec.bet_type])
        if t and t["races"]:
            past = {"roi": t["roi"], "hit_rate": t["hit_rate"], "races": t["races"]}

    # 期待値チェック: 今のオッズ（締切後なら締切時オッズ）を取って、単勝・複勝の期待値を出す
    value_rows, value_error, value_at = None, None, None
    if check_value:
        try:
            od: value.Odds = {}
            for o in odds_api.fetch_win_place(date.fromisoformat(prog["date"]), prog["venue"], prog["race_no"]):
                od.setdefault(o["bet_type"], {})[o["combo"]] = [o["odds"], o["odds_high"]]
            if od.get("win"):
                value_rows = value.evaluate_all({str(s.lane): s.win_prob for s in scores},
                                                {str(s.lane): s.top2_prob for s in scores}, od)
                value_at = datetime.now().strftime("%H:%M:%S")
            else:
                value_error = "オッズがまだ出ていないみたい。発売が始まってからもう一度押してね。"
        except Exception as e:  # noqa: BLE001
            value_error = f"オッズを取れなかった: {e}"
    paper_here = [r for r in settled_bets(conn) if r["race_id"] == race_id]
    vbets_here = wallet.settled_vbets(conn, race_id)

    return _render(
        request, "race.html", "race",
        vbets_here=vbets_here, wallet_balance=wallet.balance(conn)["available"],
        closed=wallet.deadline_passed(prog, datetime.now()),
        bought=request.query_params.get("bought"), bet_error=request.query_params.get("err"),
        value_rows=value_rows, value_error=value_error, value_at=value_at,
        paper_here=paper_here, strategies=value.STRATEGY_BY_KEY,
        prog=prog, scores=scores, entries={e["lane"]: e for e in entries}, cond=cond,
        rec=rec, rec_each=per_ticket(len(rec.picks)), race_type=race_type, past=past,
        bet=bet, points=points, budget=budget, picks=picks, pick_each=per_ticket(len(picks)),
        hit_prob=hit_probability(order, bet, [p.combo for p in picks]),
        siblings=siblings,
    )


@app.post("/race/{race_id}/beforeinfo")
def race_beforeinfo(race_id: str):
    """公式サイトの直前情報から気温・天候・風・波を取って、条件に入れて再表示する。"""
    conn = connect()
    prog = conn.execute("SELECT * FROM programs WHERE race_id = ?", (race_id,)).fetchone()
    w = weather.fetch("beforeinfo", date.fromisoformat(prog["date"]), prog["venue"], prog["race_no"])
    q = {"weather": w.get("weather", ""), "wind": w.get("wind_speed", ""),
         "wave": w.get("wave", ""), "temp": w.get("temperature", "")}
    return RedirectResponse(f"/race/{race_id}?{urlencode(q)}", status_code=303)


# ---- 選手をしらべる ----------------------------------------------------------

@app.get("/racers", response_class=HTMLResponse)
def racers(request: Request, q: str = ""):
    conn = connect()
    found = []
    q = q.strip()
    if q:
        like = f"%{q.replace(' ', '').replace('　', '')}%"
        found = conn.execute(
            """SELECT racer_id, racer_name, COUNT(*) starts, SUM(finish = 1) wins,
                      SUM(finish <= 3) top3
               FROM results WHERE racer_id = ? OR racer_name LIKE ?
               GROUP BY racer_id ORDER BY starts DESC LIMIT 50""", (q, like)).fetchall()
    return _render(request, "racers.html", "racers", q=q, found=found)


@app.get("/racer/{racer_id}", response_class=HTMLResponse)
def racer(request: Request, racer_id: str):
    conn = connect()
    prof = racer_profile(conn, racer_id)
    name_row = conn.execute(
        "SELECT racer_name FROM results WHERE racer_id = ? LIMIT 1", (racer_id,)).fetchone()
    latest = conn.execute(
        "SELECT grade, branch, age, nat_win_rate FROM program_entries WHERE racer_id = ? "
        "ORDER BY race_id DESC LIMIT 1", (racer_id,)).fetchone()
    return _render(request, "racer.html", "racers", prof=prof, latest=latest,
                   name=name_row["racer_name"] if name_row else racer_id, dims=DIMENSIONS)


# ---- 成績検証 / はじめての方へ -------------------------------------------------

@app.get("/backtest", response_class=HTMLResponse)
def backtest(request: Request):
    conn = connect()
    rows = settled_bets(conn)
    checks = conn.execute("SELECT COUNT(*) n, MIN(checked_at) first FROM paper_checks").fetchone()
    return _render(request, "backtest.html", "backtest", reports=load_backtests(),
                   ev=load_ev_report(), paper=summarize(rows), paper_rows=rows[:60],
                   paper_checks=checks, strategies=value.STRATEGIES)


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return _render(request, "guide.html", "guide")


# ---- 仮想舟券 ----------------------------------------------------------------

@app.post("/vbet")
def place_vbet(race_id: str = Form(...), bet_type: str = Form(...), combos: str = Form(...),
               amount: int = Form(...), source: str = Form("manual"), back: str = Form("")):
    """combos はカンマ区切り（"1-2-3,1-3-2"）。"""
    conn = connect()
    if not back.startswith("/") or back.startswith("//"):   # 外部へ飛ばさない
        back = f"/race/{race_id}"
    parts = urlsplit(back)
    # 前回の結果表示（bought / err）は消してから付け直す
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("bought", "err")]
    try:
        n = wallet.place_bets(conn, race_id, bet_type, [c for c in combos.split(",") if c.strip()],
                              amount, source)
        query.append(("bought", f"{BET_TYPES[bet_type]} {n}点・{n * amount:,}円"))
    except wallet.BetError as e:
        query.append(("err", str(e)))
    url = urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))
    return RedirectResponse(url, status_code=303)


@app.get("/wallet", response_class=HTMLResponse)
def wallet_page(request: Request):
    conn = connect()
    rows = wallet.settled_vbets(conn)
    w = wallet.ensure_wallet(conn)
    # レースごとにまとめる
    races: dict[str, dict] = {}
    for r in rows:
        g = races.setdefault(r["race_id"], {"race_id": r["race_id"], "date": r["date"], "venue": r["venue"],
                                             "race_no": r["race_no"], "deadline": r["deadline"], "bets": [],
                                             "amount": 0, "returned": 0, "pending": False})
        g["bets"].append(r)
        g["amount"] += r["amount"]
        g["returned"] += r["returned"]
        g["pending"] |= r["status"] == "pending"
    return _render(request, "wallet.html", "wallet", stats=wallet.stats(conn, rows),
                   balance=wallet.balance(conn)["available"], races=list(races.values()),
                   started_at=w["started_at"], start_balance=wallet.START_BALANCE)


@app.post("/wallet/reset")
def wallet_reset():
    wallet.reset_wallet(connect())
    return RedirectResponse("/wallet", status_code=303)


# ---- データ更新 / スマホで開く -------------------------------------------------

@app.post("/sync")
def sync_now():
    sync_once()
    return RedirectResponse("/settings", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    import segno

    ip = lan_ip()
    port = request.url.port or 8000
    phone_url = f"http://{ip}:{port}/" if ip else None
    qr_svg = segno.make(phone_url).svg_inline(scale=6, dark="#0b1020", light="#ffffff") if phone_url else None
    conn = connect()
    logs = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 10").fetchall()
    counts = conn.execute(
        """SELECT (SELECT COUNT(*) FROM races) races, (SELECT MIN(date) FROM races) mn,
                  (SELECT MAX(date) FROM races) mx,
                  (SELECT COUNT(*) FROM races WHERE temperature IS NOT NULL) temps,
                  (SELECT MAX(date) FROM programs) prog""").fetchone()
    listening_all = os.environ.get("BOATRACE_LAN", "0") == "1"
    return _render(request, "settings.html", "settings", phone_url=phone_url, qr_svg=qr_svg,
                   logs=logs, counts=counts, listening_all=listening_all,
                   auto=os.environ.get("BOATRACE_AUTO_SYNC", "1") != "0")
