"""仮想のお財布と仮想舟券（お金を使わずにアプリ内で買う）。

- 最初に START_BALANCE 円の仮想のお金を持つ
- 締切前のレースだけ買える。買った時点で残高から引く
- 結果は、レース直後の結果ページ（quick_results）か、翌日の成績ファイル（races / results）で精算
- フライング・出遅れ・欠場の艇を含む舟券は返還（買った金額が戻る）
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .data.result_page import normalize_combo

START_BALANCE = 10_000
REFUND_MARKS = ("F", "L", "K")
COMBO_LEN = {"win": 1, "place": 1, "exacta": 2, "quinella": 2, "wide": 2, "trifecta": 3, "trio": 3}


class BetError(ValueError):
    """画面にそのまま出すエラー。"""


def ensure_wallet(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM vwallet WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO vwallet VALUES (1, ?, ?)",
                     (START_BALANCE, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        row = conn.execute("SELECT * FROM vwallet WHERE id = 1").fetchone()
    return row


def reset_wallet(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM vbets")
    conn.execute("INSERT OR REPLACE INTO vwallet VALUES (1, ?, ?)",
                 (START_BALANCE, datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def parse_combo(bet_type: str, text: str) -> str:
    """"1-2-3" / "1=2" / "123" / "1 2" などを正規化。おかしければ BetError。"""
    lanes = [c for c in text if c in "123456"]
    if len(lanes) != COMBO_LEN[bet_type] or len(set(lanes)) != len(lanes):
        raise BetError(f"買い目「{text}」の形が正しくないよ（{COMBO_LEN[bet_type]}艇、同じ艇は1回まで）")
    return normalize_combo(bet_type, lanes)


def deadline_passed(prog: sqlite3.Row, now: datetime) -> bool:
    if prog["deadline"]:
        dl = datetime.strptime(f"{prog['date']} {prog['deadline']}", "%Y-%m-%d %H:%M")
        return now >= dl
    return prog["date"] < now.date().isoformat()


def place_bets(conn: sqlite3.Connection, race_id: str, bet_type: str, combos: list[str],
               amount: int, source: str, now: datetime | None = None) -> int:
    now = now or datetime.now()
    prog = conn.execute("SELECT * FROM programs WHERE race_id = ?", (race_id,)).fetchone()
    if prog is None:
        raise BetError("そのレースは見つからないよ")
    if bet_type not in COMBO_LEN:
        raise BetError("券種が正しくないよ")
    if deadline_passed(prog, now):
        raise BetError("このレースはもう締め切ったよ")
    if amount < 100 or amount % 100:
        raise BetError("金額は100円単位で入れてね")
    norm = []
    for c in combos:
        c = parse_combo(bet_type, c)
        if c not in norm:
            norm.append(c)
    if not norm:
        raise BetError("買い目を入れてね")
    total = amount * len(norm)
    bal = balance(conn)
    if total > bal["available"]:
        raise BetError(f"残高が足りないよ（必要 {total:,}円 / 残高 {bal['available']:,}円）")
    placed = now.isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO vbets (race_id, bet_type, combo, amount, source, placed_at) VALUES (?, ?, ?, ?, ?, ?)",
        [(race_id, bet_type, c, amount, source, placed) for c in norm])
    conn.commit()
    return len(norm)


def settled_vbets(conn: sqlite3.Connection, race_id: str | None = None) -> list[dict]:
    """status: pending / hit / miss / refund。returned は戻ってきた金額。"""
    sql = """
        SELECT v.*, p.date, p.venue, p.race_no, p.deadline,
               (SELECT 1 FROM races ra WHERE ra.race_id = v.race_id) AS has_k,
               q.refunds AS quick_refunds,
               pay.payout
        FROM vbets v
        JOIN programs p ON p.race_id = v.race_id
        LEFT JOIN quick_results q ON q.race_id = v.race_id
        LEFT JOIN payouts pay ON pay.race_id = v.race_id AND pay.bet_type = v.bet_type AND pay.combo = v.combo"""
    params: list = []
    if race_id:
        sql += " WHERE v.race_id = ?"
        params.append(race_id)
    sql += " ORDER BY v.placed_at DESC, v.id DESC"
    rows = [dict(r) for r in conn.execute(sql, params)]

    # 返還の判定に使う、成績ファイルの着順表記
    finish = {}
    for rid in {r["race_id"] for r in rows if r["has_k"]}:
        for x in conn.execute("SELECT lane, finish_raw FROM results WHERE race_id=?", (rid,)):
            finish[(rid, str(x["lane"]))] = x["finish_raw"] or ""

    for r in rows:
        lanes = r["combo"].split("-")
        refunded = set((r["quick_refunds"] or "").split(",")) - {""}
        if r["has_k"]:
            refunded |= {ln for ln in lanes if finish.get((r["race_id"], ln), "").startswith(REFUND_MARKS)}
        if not (r["has_k"] or r["quick_refunds"] is not None):
            r["status"], r["returned"] = "pending", 0
        elif refunded & set(lanes):
            r["status"], r["returned"] = "refund", r["amount"]
        elif r["payout"]:
            r["status"], r["returned"] = "hit", r["payout"] * r["amount"] // 100
        else:
            r["status"], r["returned"] = "miss", 0
    return rows


@dataclass
class WalletStats:
    start: int
    bets: int
    settled: int
    hits: int
    invested: int          # 精算済みの購入額
    returned: int
    pending_amount: int

    @property
    def roi(self) -> float:
        return self.returned / self.invested if self.invested else 0.0

    @property
    def profit(self) -> int:
        return self.returned - self.invested


def stats(conn: sqlite3.Connection, rows: list[dict] | None = None) -> WalletStats:
    w = ensure_wallet(conn)
    rows = settled_vbets(conn) if rows is None else rows
    done = [r for r in rows if r["status"] != "pending"]
    return WalletStats(
        start=w["start_balance"], bets=len(rows), settled=len(done),
        hits=sum(r["status"] == "hit" for r in done),
        invested=sum(r["amount"] for r in done), returned=sum(r["returned"] for r in done),
        pending_amount=sum(r["amount"] for r in rows if r["status"] == "pending"))


def balance(conn: sqlite3.Connection) -> dict:
    s = stats(conn)
    available = s.start - s.invested - s.pending_amount + s.returned
    return {"available": available, "start": s.start}
