"""公式サイトへのアクセス（接続の使い回しと、アクセス間隔の制御）。

- 公式サイトへの負荷を避けるため、リクエストの開始は全体で REQUEST_INTERVAL_SEC 秒に 1 回まで
- ウイルス対策ソフトが通信を検査する PC では 1 件に数秒かかるので、
  複数スレッドで「待ち時間」を重ねられるよう、セッションはスレッドごとに持つ
"""
import threading
import time

import requests

from ..config import BOATRACE_BASE, REQUEST_INTERVAL_SEC, USER_AGENT

_local = threading.local()
_rate_lock = threading.Lock()
_last_start = 0.0


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.session = s
    return s


def _wait_turn() -> None:
    """前のリクエストの開始から REQUEST_INTERVAL_SEC 秒たつまで待つ（全スレッド共通）。"""
    global _last_start
    with _rate_lock:
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - _last_start)
        if wait > 0:
            time.sleep(wait)
        _last_start = time.monotonic()


def get_race_page(page: str, d, venue: str, race_no: int, retries: int = 2) -> str:
    """boatrace.jp のレース別ページ（raceresult / beforeinfo / oddstf など）の HTML。

    しばらく通信が空くと、サーバーが使い回していた接続を切ることがある
    （ConnectionResetError）。そのときは接続を作り直してやり直す。
    """
    params = {"rno": race_no, "jcd": venue, "hd": f"{d:%Y%m%d}"}
    for attempt in range(retries + 1):
        _wait_turn()
        try:
            resp = _session().get(f"{BOATRACE_BASE}/{page}", params=params, timeout=30)
        except requests.ConnectionError:
            _local.session = None          # 切れた接続を捨てる
            if attempt == retries:
                raise
            continue
        resp.raise_for_status()
        return resp.text
    raise RuntimeError("unreachable")
