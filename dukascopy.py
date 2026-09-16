from __future__ import annotations

import datetime as dt
import lzma
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DataConfig


FEED = "https://datafeed.dukascopy.com/datafeed"
POINT = 1000
RECORD_BYTES = 24
MAX_WORKERS = 1
MIN_INTERVAL = 0.9

CANDLE_URL = "{feed}/{symbol}/{year}/{month0:02d}/{day:02d}/{side}_candles_min_1.bi5"


def is_bi5_body(body: bytes) -> bool:
    return len(body) >= 8 and body[0] in (0x5D, 0xFD)


def _http_get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (research backtest data fetch)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_day_candles(symbol: str, day: dt.date, side: str, retries: int = 8) -> bytes:
    url = CANDLE_URL.format(
        feed=FEED, symbol=symbol, year=day.year,
        month0=day.month - 1, day=day.day, side=side,
    )
    last = None
    for attempt in range(retries):
        try:
            body = _http_get(url)
            if is_bi5_body(body):
                return body
            if b"503" in body[:300]:
                raise RuntimeError("rate-limited")
            raise RuntimeError(f"unexpected body len={len(body)}")
        except Exception as exc:  # noqa: BLE001
            last = exc
            delay = min(1.5 * (2 ** min(attempt, 5)), 45.0)
            time.sleep(delay + 0.25 * attempt)
    raise RuntimeError(f"fetch failed {url}: {last}")


def decode_candle_buffer(data: bytes, day: dt.date) -> pd.DataFrame:
    if not is_bi5_body(data):
        raise ValueError("not a valid bi5 lzma stream")
    dec = lzma.decompress(data)
    n = len(dec) // RECORD_BYTES
    if n == 0:
        return pd.DataFrame()
    vals = np.frombuffer(dec, dtype=">u4").reshape(n, 6).astype(np.int64)
    ts = pd.to_datetime(day, utc=True) + pd.to_timedelta(vals[:, 0], unit="s")
    out = pd.DataFrame(
        {
            "open": vals[:, 1],
            "close": vals[:, 2],
            "low": vals[:, 3],
            "high": vals[:, 4],
            "volume": vals[:, 5],
        },
        index=pd.DatetimeIndex(ts, name="time"),
    )
    out = out.loc[~out.index.duplicated(keep="last")].sort_index()
    out.index = out.index.tz_localize(None)
    return out[["open", "high", "low", "close", "volume"]].astype(
        {"open": "f8", "high": "f8", "low": "f8", "close": "f8", "volume": "f8"}
    ) / np.array([POINT, POINT, POINT, POINT, 1e6])


class _Pacer:
    def __init__(self, min_interval: float = MIN_INTERVAL):
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._min = min_interval

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
            self._next_at = max(now, self._next_at) + self._min


def trading_days(start: dt.date, end: dt.date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def raw_paths(cfg: DataConfig, side: str, day: dt.date) -> Path:
    return (
        cfg.base_dir / "raw" / cfg.symbol / side
        / f"{day.year:04d}-{day.month:02d}-{day.day:02d}.bi5"
    )


def _download_one(cfg: DataConfig, day: dt.date, side: str, pacer: _Pacer) -> tuple:
    out = raw_paths(cfg, side, day)
    if out.exists() and out.stat().st_size > 0 and is_bi5_body(out.read_bytes()[:8]):
        return day, side, "cached", out.stat().st_size
    pacer.wait()
    body = fetch_day_candles(cfg.symbol, day, side)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.rename(out)
    return day, side, "downloaded", len(body)


def download_range(cfg: DataConfig, log=None) -> dict:
    end = dt.date.fromisoformat(cfg.end) if cfg.end else dt.date.today()
    start = dt.date.fromisoformat(cfg.start)
    days = list(trading_days(start, end))
    if not days:
        raise ValueError("empty date range")
    stats = {"downloaded": 0, "cached": 0, "failed": []}
    pacer = _Pacer()
    total = len(days) * 2
    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            pool.submit(_download_one, cfg, day, side, pacer)
            for day in days
            for side in ("BID", "ASK")
        ]
        for fut in as_completed(futures):
            try:
                day, side, kind, size = fut.result()
            except Exception as exc:  # noqa: BLE001
                stats["failed"].append(repr(exc))
                done += 1
                continue
            stats[kind] += 1
            done += 1
            if log is not None and done % 40 == 0:
                elapsed = time.time() - started
                rate = done / max(elapsed, 1e-9)
                eta = (total - done) / max(rate, 1e-9)
                log(f"{done}/{total} ok={stats['downloaded']} cached={stats['cached']} "
                    f"rate={rate:.1f}/s eta={eta/60:.1f}min")
    if log is not None:
        log(f"finished in {(time.time() - started) / 60:.1f}min stats={stats}")
    return stats
