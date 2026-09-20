"""FTMO priceData adapter: canonical clean bars -> UTC frames and chart specs.

``C:\\personalCode\\priceData`` is the single source of truth for FTMO price
data (set ``PRICEDATA_ROOT`` or pass ``root=`` to point elsewhere). This module
reads its clean parquet directly::

    <root>/data/clean/{SYM}/{SYM}_{TF}.parquet     Open High Low Close Volume SpreadPts

Nothing is resampled: each timeframe is the broker's own native bar set, BID.

CLOCK. The raw files are stamped in FTMO server time (UTC+2, UTC+3 during US
DST). ChartLab works in true UTC internally - session timings are UTC based - so
``load_frame`` converts (see ``sources.ftmo_server_to_utc``) and ``build_spec``
writes UTC epochs. The viewer then *displays* Malaysian time (UTC+8) by default
and can switch to UTC; it never rewrites the data.

Any trades / zones / equity you hand in carry times too, so ``build_spec``
requires ``clock=`` ('ftmo' server time, 'utc' or 'myt') whenever you pass them.
``start`` / ``end`` / ``max_bars`` windows are in UTC.

Loading is strict: a file that is unsorted, has duplicate stamps, NaNs,
incoherent OHLC or a timezone raises, because an unverified frame is a broken
frame. (priceData's own ``scripts/audit_raw_dumps.py`` is the full audit.)
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from . import chart, export, sources

DEFAULT_ROOT = Path(os.environ.get("PRICEDATA_ROOT", r"C:\personalCode\priceData"))
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD",
           "XAUUSD", "XAGUSD", "BTCUSD")
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
SOURCE = "ftmo"
EXCHANGE = sources.SOURCES[SOURCE]["label"]
# One page carries every bar of every timeframe it offers; past this the HTML
# is tens of MB and the browser struggles, so make the caller choose a window.
MAX_BARS_PER_TF = 300_000

check_frame = sources.check_frame


def parquet_path(symbol: str, timeframe: str, root: str | Path | None = None) -> Path:
    symbol, timeframe = symbol.upper(), timeframe.upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"unknown symbol {symbol!r}; priceData has {', '.join(SYMBOLS)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; priceData has {', '.join(TIMEFRAMES)}")
    return Path(root or DEFAULT_ROOT) / "data" / "clean" / symbol / f"{symbol}_{timeframe}.parquet"


def load_frame(symbol: str, timeframe: str, *, start=None, end=None,
               max_bars: int | None = None, root: str | Path | None = None,
               clock: str = "utc") -> pd.DataFrame:
    """Native priceData bars (Open/High/Low/Close/Volume/SpreadPts).

    ``clock="utc"`` (default) returns a UTC-indexed frame; ``"server"`` returns
    the raw FTMO server-time labels. ``start`` inclusive / ``end`` exclusive
    (anything ``pd.Timestamp`` reads) are in the returned frame's clock;
    ``max_bars`` keeps only the most recent N bars of what remains.
    """
    if clock not in ("utc", "server"):
        raise ValueError("clock must be 'utc' or 'server'")
    path = parquet_path(symbol, timeframe, root)
    if not path.exists():
        raise FileNotFoundError(f"no priceData file for {symbol.upper()} {timeframe.upper()} at {path}")
    df = pd.read_parquet(path)
    problems = check_frame(df)
    if problems:
        raise ValueError(f"{path.name} failed integrity checks: " + "; ".join(problems))
    if clock == "utc":
        df = sources.to_utc(df, SOURCE)
        problems = check_frame(df)
        if problems:
            raise ValueError(f"{path.name} failed integrity checks after the UTC conversion: "
                             + "; ".join(problems))
    if start is not None:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df.loc[df.index < pd.Timestamp(end)]
    if max_bars is not None:
        df = df.iloc[-int(max_bars):]
    if df.empty:
        raise ValueError(f"{symbol.upper()} {timeframe.upper()} has no bars in the requested window "
                         f"(start={start}, end={end})")
    return df


def build_spec(symbol: str, timeframes=("H4", "D1"), *, default_tf: str | None = None,
               start=None, end=None, max_bars: int | None = None, volume: bool = False,
               trades=None, zones=None, equity=None, clock: str | None = None,
               indicators=None, stats=None, title: str | None = None,
               tz: str | None = None, root: str | Path | None = None) -> dict:
    """A chart spec of native priceData bars in ``timeframes`` plus any overlay.

    Bars are converted to true UTC. ``clock`` names the clock of the times in
    ``trades`` / ``zones`` / ``equity`` ('ftmo', 'utc', 'myt') and is REQUIRED
    when any are given. ``tz`` (UTC or MYT, default MYT) is only the viewer's
    initial display zone. ``default_tf`` defaults to the first timeframe.
    ``indicators`` is a list of indicator dicts or a string like
    ``"SMA50,EMA200,RSI14"`` (drawn on the default timeframe). Pass ``max_bars``
    or ``start`` for fine timeframes: a page refuses more than
    ``MAX_BARS_PER_TF`` bars of any one timeframe.
    """
    tfs = list(dict.fromkeys(t.upper() for t in timeframes))
    if not tfs:
        raise ValueError("timeframes must not be empty")
    default_tf = (default_tf or tfs[0]).upper()
    if default_tf not in tfs:
        raise ValueError(f"default_tf {default_tf!r} is not in timeframes {tfs}")
    trades, zones, equity = sources.convert_overlay(trades, zones, equity, clock)
    blocks = {}
    for tf in tfs:
        df = load_frame(symbol, tf, start=start, end=end, max_bars=max_bars, root=root)
        if len(df) > MAX_BARS_PER_TF:
            raise ValueError(f"{symbol.upper()} {tf} has {len(df):,} bars in that window; a page "
                             f"carries at most {MAX_BARS_PER_TF:,}. Narrow it with start=/max_bars=")
        blocks[tf] = export.bars_block(df, include_volume=volume)
    if isinstance(indicators, str):
        indicators = chart.parse_indicators(indicators)
    s = chart.spec(symbol.upper(), blocks, exchange=EXCHANGE, source=SOURCE,
                   period_label=f"{default_tf} · {symbol.upper()} · BID",
                   default_tf=default_tf, trades=trades, zones=zones, equity=equity,
                   indicators=indicators or None, stats=stats, title=title, tz=tz)
    problems = chart.validate(s)
    if problems:
        raise ValueError("invalid spec: " + "; ".join(problems))
    return s
