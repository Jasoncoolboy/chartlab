"""The two data sources ChartLab expects - FTMO and Dukascopy - and their clocks.

INTERNALLY EVERYTHING IS UTC. Session timings (Asia / London / New York) are UTC
based, so every frame and every spec time in ChartLab is a true UTC instant.
Malaysian time (MYT, UTC+8, no DST) is only ever a *display* setting in the viewer.

======================  ==============================  ==========================
source                  labels in the raw data          what ChartLab does
======================  ==============================  ==========================
``ftmo`` (priceData)    FTMO server time: UTC+2, and    converts to UTC (below)
                        UTC+3 during **US** DST
``dukascopy``           UTC                             nothing - already UTC
======================  ==============================  ==========================

FTMO server time follows the US calendar (its daily bar opens at 17:00 New York):
UTC+3 from the 2nd Sunday of March 07:00Z to the 1st Sunday of November 06:00Z,
UTC+2 otherwise. This is not assumed: converted FTMO H1 matches real Dukascopy H4
at the same UTC instant to 0.79 % of the bar range (XAUUSD, 9,905 buckets), and in
the weeks where the US and EU DST calendars disagree the US rule is 0.69 % off
against 20.0 % for the EU rule (GBPUSD). ``tests/test_sources.py`` re-checks it.

Identification never guesses silently. ``detect`` reads, in order of strength:
an explicit tag (``df.attrs['source']``), the column shape (``SpreadPts`` = priceData,
``bid_*``/``ask_*`` = the Dukascopy pipeline), and a weekend fingerprint (FTMO
server-time labels of FX/metals never fall on a Sunday; UTC labels do - the Sunday
evening open). When that does not decide, ``resolve`` raises and asks for ``source=``.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import chart, export

SOURCES = {
    "ftmo": {"label": "FTMO", "clock": "FTMO server time (UTC+2, UTC+3 in US DST)"},
    "dukascopy": {"label": "Dukascopy", "clock": "UTC"},
}
# Malaysian time: a fixed UTC+8 offset, no DST. Display only (defined in chart.py).
TIMEZONES = chart.TIMEZONES
DEFAULT_TZ = chart.DEFAULT_TZ

# Input clocks for times handed in with a source's bars (trades, zones, setup rows)
CLOCKS = ("utc", "myt", "ftmo")

_TF_NAMES = {60: "M1", 300: "M5", 900: "M15", 1800: "M30", 3600: "H1",
             14400: "H4", 86400: "D1"}


class SourceError(ValueError):
    """The source or clock of a frame could not be established, or contradicts itself."""


class ClockError(ValueError):
    """Times were handed in without saying which clock they are in (or an unknown one)."""


# --------------------------------------------------------------------------
# FTMO server time <-> UTC
# --------------------------------------------------------------------------
def _us_dst_bounds(year: int):
    """(start, end) of US DST as UTC instants: 2nd Sun Mar 07:00Z, 1st Sun Nov 06:00Z."""
    mar1 = dt.date(year, 3, 1)
    start = mar1 + dt.timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = dt.date(year, 11, 1)
    end = nov1 + dt.timedelta(days=(6 - nov1.weekday()) % 7)
    return (np.datetime64(dt.datetime(start.year, start.month, start.day, 7), "ns"),
            np.datetime64(dt.datetime(end.year, end.month, end.day, 6), "ns"))


def _naive_ns(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.as_unit("ns")


def in_us_dst(utc_index) -> np.ndarray:
    """Boolean array: is each UTC instant inside US daylight-saving time?"""
    idx = _naive_ns(utc_index)
    t = idx.values
    out = np.zeros(len(t), dtype=bool)
    years = idx.year.to_numpy()
    for y in np.unique(years):
        start, end = _us_dst_bounds(int(y))
        m = years == y
        out[m] = (t[m] >= start) & (t[m] < end)
    return out


def ftmo_offset_hours(utc_index) -> np.ndarray:
    """FTMO server clock minus UTC, in hours (2 or 3), at each UTC instant."""
    return np.where(in_us_dst(utc_index), 3, 2)


def utc_to_ftmo_server(utc_index) -> pd.DatetimeIndex:
    idx = _naive_ns(utc_index)
    return idx + pd.to_timedelta(ftmo_offset_hours(idx), unit="h")


def ftmo_server_to_utc(labels) -> pd.DatetimeIndex:
    """FTMO server-time labels -> true UTC instants (naive, ns).

    A label is read as summer time when ``label - 3h`` falls inside US DST, else
    as winter time. On the two switch Sundays this is deterministic: the skipped
    spring hour has no labels, and the repeated autumn hour resolves to its first
    pass. Only BTCUSD (which trades through the switch) has bars there.
    """
    idx = _naive_ns(labels)
    u3 = idx - pd.Timedelta(hours=3)
    return pd.DatetimeIndex(np.where(in_us_dst(u3), u3, idx - pd.Timedelta(hours=2)))


# --------------------------------------------------------------------------
# frame integrity + timeframe helpers
# --------------------------------------------------------------------------
def check_frame(df: pd.DataFrame) -> list:
    """Problems that make a frame unfit to chart (empty list = fine)."""
    problems = []
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return ["index is not a DatetimeIndex"]
    if idx.tz is not None:
        problems.append(f"index is timezone-aware ({idx.tz}); expected a naive index")
    if not idx.is_monotonic_increasing:
        problems.append("index is not sorted ascending")
    elif not idx.is_unique:
        problems.append(f"index has {int(idx.duplicated().sum())} duplicate timestamps")
    cols = export.ohlc_columns(df)
    o, h, l, c = (df[cols[k]].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    nan = int((np.isnan(o) | np.isnan(h) | np.isnan(l) | np.isnan(c)).sum())
    if nan:
        problems.append(f"{nan} bars have NaN in open/high/low/close")
    else:
        bad = int(((h < np.maximum(np.maximum(o, c), l))
                   | (l > np.minimum(np.minimum(o, c), h))).sum())
        if bad:
            problems.append(f"{bad} bars have high < max(open, close, low) or low > min(open, close, high)")
    return problems


def spacing_seconds(index) -> int | None:
    """Median gap between bars in seconds (None for fewer than two bars)."""
    idx = _naive_ns(index)
    if len(idx) < 2:
        return None
    return int(np.median(np.diff(idx.asi8)) // 1_000_000_000)


def timeframe_name(index) -> str | None:
    """M1..D1 from the median bar spacing, or None when it is not a standard one."""
    return _TF_NAMES.get(spacing_seconds(index) or -1)


# --------------------------------------------------------------------------
# identification
# --------------------------------------------------------------------------
@dataclass
class Detection:
    source: str | None            # "ftmo" | "dukascopy" | None
    clock: str | None             # clock of the LABELS: "server" | "utc" | None
    confidence: str               # "high" | "medium" | "low" | "none"
    evidence: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)

    def summary(self) -> str:
        who = SOURCES[self.source]["label"] if self.source else "unknown"
        return (f"{who} ({self.confidence} confidence, labels in {self.clock or 'an unknown clock'}): "
                + "; ".join(self.evidence + [f"CONFLICT {c}" for c in self.conflicts]))


def weekend_fingerprint(index) -> dict:
    """Which clock do these labels look like? Only meaningful for instruments that
    close at the weekend and for at least two weeks of sub-weekly bars.

    ``kind``: ``utc_like`` (Sunday bars: the Sunday-evening open), ``weekday_only``
    (Mon-Fri only), ``trades_weekends`` (Saturday bars: crypto - no fingerprint),
    ``n/a`` (too little data, or weekly/monthly bars).
    """
    idx = _naive_ns(index)
    step = spacing_seconds(idx)
    span_days = (idx[-1] - idx[0]).days if len(idx) > 1 else 0
    if len(idx) < 50 or step is None or step > 86400 or span_days < 14:
        return {"kind": "n/a", "sun": 0.0, "sat": 0.0, "n": len(idx)}
    wd = idx.dayofweek
    sun, sat = float(np.mean(wd == 6)), float(np.mean(wd == 5))
    if sat >= 0.005:
        kind = "trades_weekends"
    elif sun >= 0.005:
        kind = "utc_like"
    elif sun == 0.0:
        kind = "weekday_only"
    else:
        kind = "n/a"
    return {"kind": kind, "sun": sun, "sat": sat, "n": len(idx)}


def detect(df: pd.DataFrame) -> Detection:
    """Identify FTMO vs Dukascopy from a frame, with the evidence. Never raises."""
    ev, conflicts = [], []
    attrs = getattr(df, "attrs", None) or {}
    cols = set(map(str, df.columns))
    tag = " ".join(str(attrs.get(k, "")) for k in ("source", "timezone")).lower()
    fp = weekend_fingerprint(df.index) if isinstance(df.index, pd.DatetimeIndex) and len(df) else \
        {"kind": "n/a", "sun": 0.0, "sat": 0.0, "n": 0}

    source, conf = None, "none"
    if "ftmo" in tag or "eet" in tag.split():
        source, conf = "ftmo", "high"
        ev.append(f"tag says FTMO ({attrs.get('source')!r}, {attrs.get('timezone')!r})")
    elif "dukascopy" in tag:
        source, conf = "dukascopy", "high"
        ev.append(f"tag says Dukascopy ({attrs.get('source')!r})")
    elif "SpreadPts" in cols:
        source, conf = "ftmo", "medium"
        ev.append("has a SpreadPts column (priceData shape)")
    elif any(c.startswith("bid_") for c in cols) and any(c.startswith("ask_") for c in cols):
        source, conf = "dukascopy", "medium"
        ev.append("has bid_*/ask_* columns (the Dukascopy pipeline's shape)")
    elif fp["kind"] == "utc_like":
        source, conf = "dukascopy", "low"
        ev.append(f"{fp['sun']:.1%} of bars fall on a Sunday, which FTMO server-time labels never do "
                  "- so these are UTC labels, and Dukascopy is the only UTC source expected")

    clock = None
    if attrs.get("clock") == "UTC":
        clock = "utc"
        ev.append("already converted: attrs clock is UTC")
    elif source == "dukascopy":
        clock = "utc"
        if fp["kind"] == "weekday_only":
            ev.append("no Sunday bars - Sunday-evening stubs may have been merged away; labels assumed UTC")
    elif source == "ftmo":
        if fp["kind"] == "utc_like":
            clock = "utc"
            conflicts.append(f"labelled FTMO but {fp['sun']:.1%} of bars fall on a Sunday, which server "
                             "time never does - already converted to UTC? Not converting again.")
        else:
            clock = "server"
            if fp["kind"] == "weekday_only":
                ev.append("no Saturday/Sunday bars, as FTMO server-time labels never have")
    if source is None:
        ev.append(f"nothing decides it (weekend fingerprint: {fp['kind']}); pass source='ftmo' or 'dukascopy'")
    return Detection(source, clock, conf, ev, conflicts)


def resolve(df: pd.DataFrame, source: str | None = None) -> Detection:
    """Like ``detect`` but final: honours a declared ``source``, refuses to guess.

    A declared source that contradicts a high/medium-confidence detection raises;
    an undecidable frame with no declared source raises.
    """
    if source is not None and source not in SOURCES:
        raise SourceError(f"unknown source {source!r}; use one of {sorted(SOURCES)}")
    det = detect(df)
    if source is None:
        if det.source is None:
            raise SourceError("cannot tell FTMO from Dukascopy: " + det.summary())
        return det
    if det.source not in (None, source) and det.confidence in ("high", "medium"):
        raise SourceError(f"declared source {source!r} but the frame says {det.source!r}: " + det.summary())
    if det.source == source:
        return det
    attrs_utc = (getattr(df, "attrs", None) or {}).get("clock") == "UTC"
    fp = weekend_fingerprint(df.index) if isinstance(df.index, pd.DatetimeIndex) and len(df) else {"kind": "n/a", "sun": 0.0}
    conflicts = list(det.conflicts)
    if source == "dukascopy" or attrs_utc:
        clock = "utc"
    elif fp["kind"] == "utc_like":
        clock = "utc"
        conflicts.append(f"declared FTMO but {fp['sun']:.1%} of bars fall on a Sunday, which server-time "
                         "labels never do - already converted to UTC? Not converting again.")
    else:
        clock = "server"
    evidence = [e for e in det.evidence if not e.startswith("nothing decides it")]
    return Detection(source, clock, "declared", [f"declared by the caller ({source})"] + evidence,
                     conflicts)


# --------------------------------------------------------------------------
# conversion to UTC
# --------------------------------------------------------------------------
def _merge_stamps(df: pd.DataFrame) -> tuple:
    """Merge bars that share one UTC stamp (BTCUSD at a DST switch); returns (frame, n_merged)."""
    dup = df.index.duplicated(keep=False)
    if not dup.any():
        return df, 0
    names = export.ohlc_columns(df)
    rule = {names["open"]: "first", names["high"]: "max", names["low"]: "min", names["close"]: "last"}
    if "volume" in names:
        rule[names["volume"]] = "sum"
    for c in df.columns:
        rule.setdefault(c, "last")
    merged = df[dup].groupby(level=0, sort=True).agg(rule)[list(df.columns)]
    out = pd.concat([df[~dup], merged]).sort_index(kind="stable")
    return out, int(dup.sum() - len(merged))


def to_utc(df: pd.DataFrame, source: str | None = None) -> pd.DataFrame:
    """A copy of ``df`` indexed by true UTC (naive), tagged in ``attrs``.

    ``ftmo`` labels are converted with the US-DST rule; ``dukascopy`` labels are
    already UTC. Bars that collide on one UTC stamp (BTCUSD at a DST switch) are
    merged and counted in ``attrs['utc_conversion']``. A frame already in UTC is
    returned as is, and an FTMO-tagged frame whose labels look like UTC is refused
    rather than converted twice.
    """
    det = resolve(df, source)
    if det.conflicts and det.source == "ftmo" and det.clock == "utc":
        raise SourceError("refusing to convert: " + det.summary())
    out = df.copy()
    attrs = dict(getattr(df, "attrs", {}) or {})
    info = {"rule": "already UTC", "merged_stamps": 0}
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
        info["rule"] = "tz-aware -> UTC"
    if det.clock == "server":
        original = out.index
        out.index = pd.DatetimeIndex(ftmo_server_to_utc(original), name=original.name)
        info["rule"] = "FTMO server time -> UTC (UTC+3 in US DST, else UTC+2)"
        if not out.index.is_monotonic_increasing:
            out = out.sort_index(kind="stable")   # equal stamps keep label order
        out, info["merged_stamps"] = _merge_stamps(out)
    out.attrs.update(attrs)
    out.attrs.update({"source": det.source, "clock": "UTC", "utc_conversion": info})
    return out


# --------------------------------------------------------------------------
# times handed in with a source's bars (trades, zones, setup rows)
# --------------------------------------------------------------------------
_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def to_utc_epoch(value, clock: str = "utc") -> int:
    """One time -> epoch seconds UTC.

    A timestamp that carries its own zone (aware datetime, ``...Z``, ``+08:00``) is
    absolute and used as is. A bare one - a naive datetime/Timestamp, an ISO string
    without a zone, an epoch number - is a wall-clock label in ``clock``:
    ``utc`` (as is), ``myt`` (minus 8 h) or ``ftmo`` (FTMO server time, converted
    with the US-DST rule).
    """
    if clock not in CLOCKS:
        raise ClockError(f"unknown clock {clock!r}; use one of {list(CLOCKS)}")
    if isinstance(value, bool):
        raise TypeError("boolean is not a timestamp")
    aware = False
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        aware = value.tzinfo is not None
        ts = pd.Timestamp(value)
    elif isinstance(value, str) and any(ch in value for ch in "-T:/"):
        aware = bool(_TZ_SUFFIX.search(value.strip()))
        ts = pd.Timestamp(value.strip())
    elif isinstance(value, (int, float, np.integer, np.floating, str)):
        ts = pd.Timestamp(int(float(value)), unit="s")
    else:
        raise TypeError(f"cannot parse timestamp: {value!r}")
    if aware or ts.tzinfo is not None:
        return int(ts.tz_convert("UTC").timestamp())
    label = ts.as_unit("ns")
    if clock == "ftmo":
        label = ftmo_server_to_utc(pd.DatetimeIndex([label]))[0]
    elif clock == "myt":
        label = label - pd.Timedelta(seconds=TIMEZONES["MYT"])
    return int(label.value // 1_000_000_000)


_TRADE_TIME_KEYS = ("entryTime", "entry_time", "time", "exitTime", "exit_time")
_ZONE_TIME_KEYS = ("start", "end", "s", "e", "from", "to")


def _need_clock(clock, what: str) -> str:
    if clock is None:
        raise ClockError(f"{what} carry times, so say which clock they are in: clock='ftmo' (FTMO "
                         "server time), 'utc' or 'myt' (CLI: --clock). Guessing would put them 2-3 hours off.")
    if clock not in CLOCKS:
        raise ClockError(f"unknown clock {clock!r}; use one of {list(CLOCKS)}")
    return clock


def convert_overlay(trades=None, zones=None, equity=None, clock=None):
    """Copies of trades / zones / equity with every time converted to UTC epochs.

    ``clock`` is REQUIRED whenever any of them is given (see ``to_utc_epoch``).
    """
    if not (trades or zones or equity):
        return trades, zones, equity
    clock = _need_clock(clock, "trades, zones and equity")

    def fix(row, keys):
        out = dict(row)
        for k in keys:
            if out.get(k) is not None and out.get(k) != "":
                out[k] = to_utc_epoch(out[k], clock)
        return out

    t = [fix(r, _TRADE_TIME_KEYS) for r in trades] if trades else trades
    z = [fix(r, _ZONE_TIME_KEYS) for r in zones] if zones else zones
    e = equity
    if equity:
        if isinstance(equity, dict):
            key = "time" if "time" in equity else "t"
            e = dict(equity)
            e[key] = [to_utc_epoch(v, clock) for v in equity[key]]
        else:
            e = [dict(r, time=to_utc_epoch(r["time"], clock)) if isinstance(r, dict)
                 else (to_utc_epoch(r[0], clock), *r[1:]) for r in equity]
    return t, z, e


# --------------------------------------------------------------------------
# files (parquet / CSV) from either source
# --------------------------------------------------------------------------
_TIME_COLS = ("Gmt time", "Datetime", "datetime", "time", "Time", "timestamp", "Timestamp", "date", "Date")


def load_file(path: str | Path) -> pd.DataFrame:
    """Read a parquet or CSV of bars into a frame indexed by its own (untouched) labels.

    Recognises priceData parquet, the Dukascopy pipeline's parquet (``bid_*``/``ask_*``)
    and Dukascopy's website CSV (``Gmt time``, ``dd.mm.yyyy hh:mm:ss.fff``). The result
    is NOT yet converted: pass it through ``to_utc``.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
        if not isinstance(df.index, pd.DatetimeIndex):
            col = next((c for c in _TIME_COLS if c in df.columns), None)
            if col is None:
                raise ValueError(f"{p.name}: no DatetimeIndex and no time column among {list(_TIME_COLS)}")
            df = df.set_index(pd.DatetimeIndex(pd.to_datetime(df.pop(col))))
        return df
    raw = pd.read_csv(p)
    col = next((c for c in _TIME_COLS if c in raw.columns), None)
    if col is None:
        raise ValueError(f"{p.name}: no time column among {list(_TIME_COLS)}; found {list(raw.columns)}")
    if col == "Gmt time":
        stamps = pd.to_datetime(raw.pop(col), format="%d.%m.%Y %H:%M:%S.%f")
    else:
        stamps = pd.to_datetime(raw.pop(col))
    df = raw.set_index(pd.DatetimeIndex(stamps, name=col))
    if col == "Gmt time":
        df.attrs["source"] = "Dukascopy (website CSV, GMT)"
    return df
