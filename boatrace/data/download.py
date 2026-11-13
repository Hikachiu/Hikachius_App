"""公式のダウンロードデータ（LZH 圧縮テキスト）を取得・展開する。

K = 競走成績（結果・天候・風・波・払戻）
B = 番組表（出走表・勝率・モーター）
"""
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

import requests

from ..config import MBRACE_BASE, RAW_DIR, REQUEST_INTERVAL_SEC, USER_AGENT


def file_url(kind: str, d: date) -> str:
    k = kind.upper()
    return f"{MBRACE_BASE}/{k}/{d:%Y%m}/{k.lower()}{d:%y%m%d}.lzh"


def _extract_lzh(archive: Path, out_dir: Path) -> None:
    """Windows 10/11 標準の tar（bsdtar）は LZH を展開できる。無ければ lhafile を使う。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tar = shutil.which("tar")
    if tar:
        r = subprocess.run([tar, "-xf", str(archive), "-C", str(out_dir)],
                           capture_output=True)
        if r.returncode == 0:
            return
    try:
        import lhafile  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "LZH を展開できませんでした。bsdtar か `pip install lhafile` が必要です。"
        ) from e
    lf = lhafile.Lhafile(str(archive))
    for info in lf.infolist():
        (out_dir / Path(info.filename).name).write_bytes(lf.read(info.filename))


def fetch_text(kind: str, d: date, force: bool = False) -> str | None:
    """指定日の K / B ファイルを取得して、テキスト（Shift_JIS をデコード済み）を返す。
    その日に開催が無いなどでファイルが無ければ None。"""
    k = kind.upper()
    stem = f"{k.lower()}{d:%y%m%d}"
    txt_dir = RAW_DIR / k / "txt"
    txt_path = next(iter(txt_dir.glob(f"{stem}.*")), None) if txt_dir.exists() else None
    if txt_path is None or force:
        lzh_path = RAW_DIR / k / "lzh" / f"{stem}.lzh"
        if not lzh_path.exists() or force:
            resp = requests.get(file_url(k, d), headers={"User-Agent": USER_AGENT}, timeout=30)
            time.sleep(REQUEST_INTERVAL_SEC)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            lzh_path.parent.mkdir(parents=True, exist_ok=True)
            lzh_path.write_bytes(resp.content)
        _extract_lzh(lzh_path, txt_dir)
        txt_path = next(iter(txt_dir.glob(f"{stem}.*")), None) \
            or next(iter(txt_dir.glob(f"{stem.upper()}.*")), None)
        if txt_path is None:
            raise RuntimeError(f"{lzh_path} を展開しましたがテキストが見つかりません")
    return txt_path.read_bytes().decode("cp932", errors="replace")
