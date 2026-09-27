"""FTMO priceData adapter: canonical clean bars -> UTC frames and chart specs.

``C:\\personalCode\\priceData`` is the single source of truth for FTMO price
data (set ``PRICEDATA_ROOT`` or pass ``root=`` to point elsewhere). This module
reads its clean parquet directly::

    <root>/data/clean/{SYM}/{SYM}_{TF}.parquet     Open High Low Close Volume SpreadPts

By default each timeframe is the broker's own native bar set, BID, and nothing
is resampled. Because a native M5..MN file can miss bars that M1 has (priceData's
2026-09-23 refresh left holes in EURUSD/GBPUSD/USDJPY M30-D1), ``build_spec``
compares every native frame with the same bars built from M1 and warns
(``DataWarning``) when they differ; ``bars="m1"`` builds the page from M1. It also
reads priceData's own verdict from its manifest (``verification_status``) and puts
both on the page as data notes (spec ``notes``, the badge in the page footer).

CLEAN YEARS ONLY. ``data/clean`` holds only the verified-clean years, from each
symbol's ``clean_from`` (2022-01-01 for all nine, owner rule 2026-09-26), and that
is all a page gets by default. ``pre_clean=True`` adds, for charts and context
only, the earlier years whose PRICES priceData verified clean (``pre_clean_from``:
FX 2020-21, GBPUSD from 2019; spreads were placeholders then) from
``data/pre_clean``. The repaired years before that are never loaded, and a page
showing pre-clean bars says so. Nothing here is a tested number.

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

import json
import os
import warnings
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

# Where a page's bars come from: the native file of each timeframe, or an aggregate of M1.
BARS = ("native", "m1")
# M1 -> higher timeframe on the FTMO SERVER clock (MT5's own grid), as priceData's
# scripts/verify_htf_vs_m1.py does: W1 bars open on Sunday, MN on the 1st.
M1_RULES = {"M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h",
            "D1": "1D", "W1": "W-SUN", "MN": "MS"}
PERIOD_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
M1_TOL = 1e-6                        # an O/H/L/C difference beyond this is a mismatch (as priceData)


class DataWarning(UserWarning):
    """priceData bars that could not be verified, or disagree with M1."""


# --------------------------------------------------------------------------
# priceData's manifest: clean years and its own verification verdict
# --------------------------------------------------------------------------
def manifest(root: str | Path | None = None) -> dict:
    """priceData's ``data/clean/manifest.json`` (``{}`` when there is none). Read, never written.

    Read every time (~5 ms): a cache keyed on the file's mtime missed a rewrite
    inside one clock tick, and a page reads it only a few times.
    """
    path = Path(root or DEFAULT_ROOT) / "data" / "clean" / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def symbol_manifest(symbol: str, root: str | Path | None = None) -> dict | None:
    """The manifest entry of ``symbol``, or None."""
    return (manifest(root).get("symbols") or {}).get(symbol.upper())


def clean_from(symbol: str, root: str | Path | None = None) -> pd.Timestamp | None:
    """First server-time day of ``symbol``'s verified-clean years: ``data/clean`` starts there."""
    info = symbol_manifest(symbol, root) or {}
    return pd.Timestamp(info["clean_from"]) if info.get("clean_from") else None


def pre_clean_from(symbol: str, root: str | Path | None = None) -> pd.Timestamp | None:
    """First server-time day of the pre-clean years whose PRICES priceData verified clean.

    Those years sit in ``data/pre_clean`` because their spreads were placeholders;
    their bars match M1 exactly. The manifest's ``price_clean_from``, moved past
    any whole year before ``clean_from`` whose per-year verdict is not
    ``price_clean``. None when there are none (XAUUSD / XAGUSD / BTCUSD) or no manifest.
    """
    info = symbol_manifest(symbol, root) or {}
    cf, pcf = info.get("clean_from"), info.get("price_clean_from")
    if not cf or not pcf:
        return None
    cf, first = pd.Timestamp(cf), pd.Timestamp(pcf)
    years = info.get("quality_by_year") or {}
    for y in range(first.year, cf.year):
        if (years.get(str(y)) or {}).get("price_clean") is not True:
            first = max(first, pd.Timestamp(f"{y + 1}-01-01"))
    return first if first < cf else None


def package(root: str | Path | None = None):
    """priceData's own ``price_data`` package (``COST_MODELS``, ``all_in_pips``, ...), imported from ``root``.

    FTMO costs come from there and nowhere else (``docs/COSTS.md`` in priceData):
    ChartLab keeps no copy of a cost number.
    """
    import importlib.util
    import sys
    base = Path(root or DEFAULT_ROOT).resolve()
    init = base / "price_data" / "__init__.py"
    for name in ("price_data", f"_chartlab_price_data_{abs(hash(str(base)))}"):
        mod = sys.modules.get(name)
        if mod is not None and Path(mod.__file__).resolve() == init:
            return mod
    if not init.exists():
        raise ImportError(f"priceData's package is not at {init}: FTMO costs come from priceData only "
                          "(set PRICEDATA_ROOT, or pass every cost explicitly)")
    name = "price_data" if "price_data" not in sys.modules else f"_chartlab_price_data_{abs(hash(str(base)))}"
    spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(init.parent)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def cost_model(symbol: str, root: str | Path | None = None) -> dict:
    """A copy of priceData's ``COST_MODELS[symbol]`` - the one FTMO cost source."""
    models = package(root).COST_MODELS
    sym = symbol.upper()
    if sym not in models:
        raise ValueError(f"priceData has no cost model for {sym!r}; it has {', '.join(models)}")
    return dict(models[sym])


def server_to_utc(t) -> pd.Timestamp:
    """One FTMO server-time instant in true UTC."""
    return sources.ftmo_server_to_utc(pd.DatetimeIndex([pd.Timestamp(t)]))[0]


def verification_status(symbol: str, timeframes, root: str | Path | None = None) -> dict:
    """priceData's own HTF-vs-M1 verdict per timeframe, from its manifest.

    ``{tf: {"verified": True | False | None, "findings", "verified_at", "dirty_reason"}}``;
    None = no verdict recorded (no manifest, or the check never ran for this
    build). M1 is the reference and is left out.
    """
    tfs = ((symbol_manifest(symbol, root) or {}).get("timeframes")) or {}
    out = {}
    for tf in timeframes:
        tf = tf.upper()
        if tf == "M1":
            continue
        e = tfs.get(tf) or {}
        v = e.get("htf_verified")
        out[tf] = {"verified": v if isinstance(v, bool) else None, "findings": e.get("htf_findings") or {},
                   "verified_at": e.get("verified_at"), "dirty_reason": e.get("dirty_reason")}
    return out


def describe_verification(symbol: str, status: dict) -> list:
    """One line per timeframe priceData did not verify clean (empty when all were)."""
    lines = []
    for tf, st in status.items():
        if st["verified"] is True:
            continue
        found = ", ".join(f"{v} {k}" for k, v in st["findings"].items() if v)
        why = (f"FAILED ({found or 'see data/clean/verification.json'})" if st["verified"] is False
               else "has no verdict (no manifest, or it never ran for this build)")
        lines.append(f"{symbol.upper()} {tf}: priceData's HTF-vs-M1 verification {why}"
                     + (f"; built anyway: {st['dirty_reason']}" if st["dirty_reason"] else ""))
    return lines


def pre_clean_text(symbol: str, until_utc) -> str:
    return (f"{symbol.upper()}: bars before {pd.Timestamp(until_utc):%Y-%m-%d %H:%M} UTC are priceData "
            "PRE-CLEAN years (prices verified against M1, spreads were placeholders) - context only, "
            "never test or quote on them")


def page_notes(symbol: str, timeframes, *, bars: str = "native", status: dict | None = None,
               check_lines=None, checked=(), pre_clean_until=None) -> list:
    """The data notes a page carries (spec ``notes``): what its bars are and how they were verified.

    ``status`` = ``verification_status``; ``check_lines`` = what ChartLab's own
    M1 check found on the page's bars (``describe_check``), ``checked`` the
    timeframes that check actually compared; ``pre_clean_until`` = the UTC
    instant before which the page's bars are pre-clean (None when it shows none).
    """
    sym, tfs = symbol.upper(), [t.upper() for t in timeframes]
    notes = []
    if pre_clean_until is not None:
        notes.append({"level": "warn", "text": pre_clean_text(sym, pre_clean_until)})
    if bars == "m1":
        notes.append({"level": "info", "text": f"{sym} {', '.join(tfs)}: every timeframe built from priceData M1"})
        return notes
    status = status or {}
    lines = describe_verification(sym, status) + list(check_lines or [])
    notes += [{"level": "warn", "text": line} for line in lines]
    native = [tf for tf in tfs if tf != "M1"]
    if not lines and native:
        parts = []
        if status:
            when = sorted(st["verified_at"] for st in status.values() if st.get("verified_at"))
            parts.append("priceData verified these native bars against M1"
                         + (f" ({when[-1].replace('T', ' ')[:16]})" if when else ""))
        checked = [tf for tf in native if tf in {t.upper() for t in checked}]
        if checked:
            parts.append(f"ChartLab re-checked this page's {', '.join(checked)} bars against M1: they match")
        if parts:
            notes.append({"level": "info", "text": f"{sym} {', '.join(native)}: " + "; ".join(parts)})
    return notes


def aggregate_m1(m1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """``timeframe`` bars from an M1 frame, binned on the M1 frame's own clock.

    Pass priceData M1 in SERVER time (``load_frame(..., clock="server")``) so the
    bins sit on MT5's grid (H4 at 00/04/08 server, D1 at server midnight). Empty
    bins (weekends, breaks) are dropped. ``attrs`` records the first and last M1
    minute (``m1_first`` / ``m1_last``) in the same clock as the index.
    """
    tf = timeframe.upper()
    if tf not in M1_RULES:
        raise ValueError(f"cannot build {tf!r} from M1; use one of {list(M1_RULES)}")
    cols = [c for c in _AGG if c in m1.columns]
    out = (m1[cols].resample(M1_RULES[tf], label="left", closed="left")
           .agg({c: _AGG[c] for c in cols}).dropna(subset=["Open"]))
    out.index.name = m1.index.name
    out.attrs = {"m1_first": m1.index[0], "m1_last": m1.index[-1]}
    return out


def bin_end(label, timeframe: str) -> pd.Timestamp:
    """Where the bar opening at ``label`` ends, on the same clock (W1: 7 days on; MN: the next month's 1st)."""
    tf, t = timeframe.upper(), pd.Timestamp(label)
    if tf == "W1":
        return t + pd.Timedelta(days=7)
    if tf == "MN":
        return t + pd.offsets.MonthBegin(1)
    return t + pd.Timedelta(seconds=PERIOD_SEC[tf])


def complete_until(symbol: str, m1_last, root: str | Path | None = None) -> pd.Timestamp:
    """Server time up to which every bar of ``symbol`` is complete.

    priceData's dumper writes only bars whose period has ended at its cutoff, so
    the latest end of any native bar (M5..MN) is at or before that cutoff; so is
    the minute after the last M1 bar. The later of those - a native file with a
    hole at its tail does not pull it back while M5 is whole.
    """
    ends = [pd.Timestamp(m1_last) + pd.Timedelta(minutes=1)]
    for tf in TIMEFRAMES[1:]:
        path = parquet_path(symbol, tf, root)
        if path.exists():
            ix = pd.read_parquet(path, columns=[]).index
            if len(ix):
                ends.append(bin_end(ix[-1], tf))
    return max(ends)


def _m1_build(m1_server: pd.DataFrame, tf: str, start=None, end=None, clock: str = "utc",
              until=None) -> pd.DataFrame:
    """``tf`` built from server-clock M1 for the window [start, end) in ``clock``.

    M1 is cut one whole bin (+3 h for the clock offset) wider than the window on
    each side, so the bins kept are never truncated by the cut. ``until`` (server
    time, see ``complete_until``) drops the bins that end after it: the bar still
    forming when M1 ends, which a native file never holds.
    """
    pad = pd.Timedelta(days=35 if tf == "MN" else 8 if tf == "W1" else 1) + pd.Timedelta(hours=3)
    m = m1_server
    if start is not None:
        m = m.loc[m.index >= pd.Timestamp(start) - pad]
    if end is not None:
        m = m.loc[m.index < pd.Timestamp(end) + pad]
    if m.empty:
        return m1_server.iloc[0:0][[c for c in _AGG if c in m1_server.columns]]
    out = aggregate_m1(m, tf)
    if until is not None and len(out):
        attrs = out.attrs
        out = out.loc[[bin_end(t, tf) <= until for t in out.index]]
        out.attrs = attrs
    if clock == "utc":
        first, last = sources.ftmo_server_to_utc(pd.DatetimeIndex([out.attrs["m1_first"], out.attrs["m1_last"]]))
        conv = out.copy()
        conv.index = pd.DatetimeIndex(sources.ftmo_server_to_utc(out.index), name=out.index.name)
        conv = conv.sort_index(kind="stable")
        conv, merged = sources._merge_stamps(conv)
        conv.attrs = {"source": SOURCE, "clock": "UTC", "bars": "m1", "m1_first": first, "m1_last": last,
                      "utc_conversion": {"rule": "built from M1 on FTMO server time -> UTC",
                                         "merged_stamps": merged}}
        out = conv
    else:
        out.attrs.update({"source": SOURCE, "bars": "m1"})
    if start is not None:
        out = out.loc[out.index >= pd.Timestamp(start)]
    if end is not None:
        out = out.loc[out.index < pd.Timestamp(end)]
    return out


def m1_mismatches(native: pd.DataFrame, built: pd.DataFrame, timeframe: str,
                  tol: float = M1_TOL, since=None) -> dict:
    """Where native bars disagree with the same bars built from M1 (same clock, same window).

    Compared: every M1-supported bin from ``since`` (default: where both frames
    start) up to the last bin M1 covers completely (W1/MN: the newest bin is left
    out, it may be open). Pass ``since`` when the window starts inside the native
    file's history, so a hole at the very start of the window is not skipped.
    ``missing`` = bins M1 has and the native file lacks; ``mismatched`` = O/H/L/C
    differ by more than ``tol``; ``extra`` = native bars with no M1 behind them.
    Each is a DatetimeIndex; ``status`` is ``clean`` or ``dirty``.
    """
    tf = timeframe.upper()
    res = {"timeframe": tf, "status": "clean", "compared": 0,
           "missing": pd.DatetimeIndex([]), "mismatched": pd.DatetimeIndex([]), "extra": pd.DatetimeIndex([])}
    if built.empty:
        res.update(status="unverified", reason="no bar built from M1 starts in the window", short_window=True)
        return res
    if since is not None:
        lo = pd.Timestamp(since)
    elif native.empty:
        res.update(status="unverified", reason="no native bars to compare")
        return res
    else:
        lo = max(native.index[0], built.index[0])
    b = built.loc[built.index >= lo]
    last = built.attrs.get("m1_last", built.index[-1])
    one = pd.Timedelta(minutes=1)
    if tf in PERIOD_SEC:
        b = b.loc[b.index + pd.Timedelta(seconds=PERIOD_SEC[tf]) <= last + one]
    else:                            # W1/MN: a bin's end is a calendar step on the server clock
        utc = built.attrs.get("clock") == "UTC"
        labels = sources.utc_to_ftmo_server(b.index) if utc else b.index
        last_srv = sources.utc_to_ftmo_server(pd.DatetimeIndex([last]))[0] if utc else last
        ends = labels + (pd.Timedelta(days=7) if tf == "W1" else pd.offsets.MonthBegin(1))
        b = b.loc[(ends <= last_srv + one)]
    if b.empty:
        res.update(status="unverified", reason="M1 covers no whole bar in the window", short_window=True)
        return res
    n = native.loc[(native.index >= lo) & (native.index <= b.index[-1])]
    names = export.ohlc_columns(n)
    nn = n[[names[k] for k in ("open", "high", "low", "close")]]
    nn.columns = ["open", "high", "low", "close"]
    bb = b[["Open", "High", "Low", "Close"]]
    bb.columns = ["open", "high", "low", "close"]
    j = nn.join(bb, rsuffix="_m1", how="inner")
    bad = pd.Series(False, index=j.index)
    for k in ("open", "high", "low", "close"):
        bad |= (j[k] - j[k + "_m1"]).abs() > tol
    res.update(compared=len(b), missing=b.index.difference(nn.index),
               mismatched=j.index[bad.to_numpy()], extra=nn.index.difference(built.index))
    if len(res["missing"]) or len(res["mismatched"]) or len(res["extra"]):
        res["status"] = "dirty"
    return res


def check_vs_m1(symbol: str, frames: dict, *, start=None, end=None,
                root: str | Path | None = None, pre_clean: bool = False) -> dict:
    """``{tf: m1_mismatches(...)}`` for native UTC frames ``{tf: frame}``.

    Each frame is compared over [start, end) (UTC; default: from its first bar
    to the end of the data) with the same bars built from M1. M1 itself is the
    reference and is skipped. Without an M1 file every frame comes back
    ``status="unverified"`` - never silently clean. M1's pre-clean years are read
    too when ``pre_clean`` is set or a frame carries pre-clean bars.
    """
    tfs = [tf.upper() for tf in frames if tf.upper() != "M1"]
    if not tfs:
        return {}
    path = parquet_path(symbol, "M1", root)
    if not path.exists():
        return {tf: {"timeframe": tf, "status": "unverified", "reason": f"no M1 file at {path}"} for tf in tfs}
    pre_clean = pre_clean or any(f.attrs.get("pre_clean_rows") for f in frames.values())
    m1 = load_frame(symbol, "M1", root=root, clock="server", pre_clean=pre_clean)
    out = {}
    for key, native in frames.items():
        tf = key.upper()
        if tf == "M1":
            continue
        lo = pd.Timestamp(start) if start is not None else native.index[0]
        win = native.loc[native.index >= lo]
        if end is not None:
            win = win.loc[win.index < pd.Timestamp(end)]
        # A window that starts inside the native file's history is compared from its start.
        since = lo if len(native) and native.index[0] < lo else None
        out[tf] = m1_mismatches(win, _m1_build(m1, tf, lo, end, "utc"), tf, since=since)
    return out


def covered_by_pricedata(results: dict, status: dict) -> dict:
    """``check_vs_m1`` results without the "NOT verified" ones priceData's own verification covers.

    A short window cannot be checked on W1/MN (M1 covers no whole week or month
    in it); when priceData verified that timeframe against M1, that verdict
    stands. Anything else unverified (no M1 file) is kept.
    """
    return {tf: r for tf, r in results.items()
            if not (r["status"] == "unverified" and r.get("short_window")
                    and (status.get(tf) or {}).get("verified") is True)}


def warn_check(symbol: str, results: dict) -> list:
    """Raise one ``DataWarning`` per line of ``describe_check``; returns the lines."""
    lines = describe_check(symbol, results)
    for line in lines:
        hint = "" if "NOT verified" in line else " - pass bars='m1' (CLI: --bars m1) to build the page from M1"
        warnings.warn(line + hint, DataWarning, stacklevel=3)
    return lines


def describe_check(symbol: str, results: dict) -> list:
    """One line per timeframe that is not clean (empty when every one matches M1)."""
    lines = []
    fmt = lambda t: f"{pd.Timestamp(t):%Y-%m-%d %H:%M} UTC"
    for tf, r in results.items():
        if r["status"] == "clean":
            continue
        if r["status"] == "unverified":
            lines.append(f"{symbol.upper()} {tf}: NOT verified against M1 ({r.get('reason', '')})")
            continue
        parts = [f"{len(r[k])} {k} (first {fmt(r[k][0])})" for k in ("missing", "mismatched", "extra") if len(r[k])]
        lines.append(f"{symbol.upper()} {tf}: native bars disagree with M1 - " + ", ".join(parts)
                     + f" of {r['compared']} bars compared")
    return lines


def parquet_path(symbol: str, timeframe: str, root: str | Path | None = None) -> Path:
    symbol, timeframe = symbol.upper(), timeframe.upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"unknown symbol {symbol!r}; priceData has {', '.join(SYMBOLS)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; priceData has {', '.join(TIMEFRAMES)}")
    return Path(root or DEFAULT_ROOT) / "data" / "clean" / symbol / f"{symbol}_{timeframe}.parquet"


def _with_pre_clean(symbol: str, timeframe: str, df: pd.DataFrame, root) -> pd.DataFrame:
    """``df`` (server clock, from data/clean) with the price-clean pre-clean bars in front of it."""
    first = pre_clean_from(symbol, root)
    sym, tf = symbol.upper(), timeframe.upper()
    path = Path(root or DEFAULT_ROOT) / "data" / "pre_clean" / sym / f"{sym}_{tf}.parquet"
    if first is None or not path.exists():
        return df
    pre = pd.read_parquet(path)
    stop = df.index[0] if len(df) else clean_from(symbol, root)
    keep = pre.index >= first
    if stop is not None:
        keep &= pre.index < stop
    pre = pre.loc[keep]
    if pre.empty:
        return df
    out = pd.concat([pre[[c for c in df.columns if c in pre.columns]], df])
    out.attrs = dict(df.attrs)
    out.attrs["pre_clean_rows"] = len(pre)
    return out


def _warn_before_clean(symbol: str, timeframe: str, start, clock: str, pre_clean: bool, root) -> None:
    """A ``start`` before the data a page may use gets a ``DataWarning`` saying what was left out."""
    cf = clean_from(symbol, root) if start is not None else None
    if cf is None:
        return
    pcf = pre_clean_from(symbol, root)
    bound = pcf if (pre_clean and pcf is not None) else cf
    if pd.Timestamp(start) >= (server_to_utc(bound) if clock == "utc" else bound):
        return
    sym, tf = symbol.upper(), timeframe.upper()
    if pre_clean and pcf is not None:
        msg = (f"{sym} {tf}: nothing before {pcf:%Y-%m-%d} (server) is loaded - the years before it were "
               "repaired, never verified clean")
    elif pre_clean:
        msg = (f"{sym} {tf}: priceData has no price-verified years before its clean data "
               f"({cf:%Y-%m-%d} server); nothing earlier is loaded")
    else:
        msg = (f"{sym} {tf}: priceData's verified-clean data starts {cf:%Y-%m-%d} (server); nothing earlier "
               "is loaded" + (f" - pre_clean=True (CLI --pre-clean) adds the price-verified years from "
                              f"{pcf:%Y-%m-%d}, for context only" if pcf is not None else ""))
    warnings.warn(msg, DataWarning, stacklevel=3)


def _tag_pre_clean(df: pd.DataFrame, symbol: str, clock: str, loaded, root) -> pd.DataFrame:
    """Record in ``attrs`` how many pre-clean bars the returned window holds, and where they end."""
    attrs = {k: v for k, v in df.attrs.items() if k not in ("pre_clean_rows", "pre_clean_until")}
    cf = clean_from(symbol, root) if loaded else None
    if cf is not None:
        until = server_to_utc(cf) if clock == "utc" else cf
        n = int((df.index < until).sum())
        if n:
            attrs.update(pre_clean_rows=n, pre_clean_until=until)
    df.attrs = attrs
    return df


def load_frame(symbol: str, timeframe: str, *, start=None, end=None,
               max_bars: int | None = None, root: str | Path | None = None,
               clock: str = "utc", bars: str = "native", pre_clean: bool = False) -> pd.DataFrame:
    """priceData bars of one timeframe.

    ``bars="native"`` (default) reads the broker's own file for ``timeframe``
    (Open/High/Low/Close/Volume/SpreadPts). ``bars="m1"`` builds it from the M1
    file instead (Open/High/Low/Close/Volume): M1 is what priceData verifies the
    higher timeframes against, so a hole or a wrong bar in a native M5..MN file
    does not reach the page (see ``check_vs_m1``). Bins are cut on the FTMO
    server clock, MT5's own grid, then converted. A bin still forming when M1
    ends is dropped, as the native files never hold one (``complete_until``).

    ``clock="utc"`` (default) returns a UTC-indexed frame; ``"server"`` returns
    the raw FTMO server-time labels. ``start`` inclusive / ``end`` exclusive
    (anything ``pd.Timestamp`` reads) are in the returned frame's clock;
    ``max_bars`` keeps only the most recent N bars of what remains.

    Only priceData's verified-clean years are read (``data/clean``, from
    ``clean_from``); a ``start`` before them warns (``DataWarning``).
    ``pre_clean=True`` adds the earlier years whose prices are verified clean
    (``pre_clean_from``) - charts and context only, never a tested number. The
    frame then records ``attrs["pre_clean_rows"]`` (in the returned window) and
    ``attrs["pre_clean_until"]`` (``clean_from`` in the frame's clock).
    """
    if clock not in ("utc", "server"):
        raise ValueError("clock must be 'utc' or 'server'")
    if bars not in BARS:
        raise ValueError(f"bars must be one of {BARS}, got {bars!r}")
    _warn_before_clean(symbol, timeframe, start, clock, pre_clean, root)
    if bars == "m1" and timeframe.upper() != "M1":
        parquet_path(symbol, timeframe, root)                     # validates the names
        m1 = load_frame(symbol, "M1", root=root, clock="server", pre_clean=pre_clean)
        df = _m1_build(m1, timeframe.upper(), start, end, clock,
                       until=complete_until(symbol, m1.index[-1], root))
        if max_bars is not None:
            df = df.iloc[-int(max_bars):]
        if df.empty:
            raise ValueError(f"{symbol.upper()} {timeframe.upper()} (built from M1) has no bars in the "
                             f"requested window (start={start}, end={end})")
        return _tag_pre_clean(df, symbol, clock, m1.attrs.get("pre_clean_rows"), root)
    path = parquet_path(symbol, timeframe, root)
    if not path.exists():
        raise FileNotFoundError(f"no priceData file for {symbol.upper()} {timeframe.upper()} at {path}")
    df = pd.read_parquet(path)
    if pre_clean:
        df = _with_pre_clean(symbol, timeframe, df, root)
    loaded = df.attrs.get("pre_clean_rows")
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
    return _tag_pre_clean(df, symbol, clock, loaded, root)


def build_spec(symbol: str, timeframes=("H4", "D1"), *, default_tf: str | None = None,
               start=None, end=None, max_bars: int | None = None, volume: bool = False,
               trades=None, zones=None, equity=None, clock: str | None = None,
               indicators=None, stats=None, title: str | None = None,
               tz: str | None = None, root: str | Path | None = None, view=None,
               bars: str = "native", verify_m1: bool = True, pre_clean: bool = False) -> dict:
    """A chart spec of priceData bars in ``timeframes`` plus any overlay.

    ``bars="native"`` (default) uses each timeframe's own file and, with
    ``verify_m1`` (default), compares every M5..MN frame with the same bars built
    from M1 over the page's window: a hole or a wrong bar raises a ``DataWarning``
    naming it (as does a frame that cannot be verified, or one priceData's own
    verification did not pass). ``bars="m1"`` builds every timeframe from M1
    instead (see ``load_frame``). The page carries these verdicts as data notes
    (spec ``notes``, the badge in its footer).

    Only priceData's verified-clean years are used; ``pre_clean=True`` adds the
    earlier price-verified years for context and labels them on the page.

    Bars are converted to true UTC. ``clock`` names the clock of the times in
    ``trades`` / ``zones`` / ``equity`` / ``view`` ('ftmo', 'utc', 'myt') and is
    REQUIRED when any are given. ``view`` (``{from, to}`` or a pair) is the time
    range the page opens on (default: every bar). ``tz`` (UTC or MYT, default MYT) is only the viewer's
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
    if view is not None:
        lo, hi = ((view.get("from", view.get("start")), view.get("to", view.get("end")))
                  if isinstance(view, dict) else view)
        vclock = sources._need_clock(clock, "view")
        view = (sources.to_utc_epoch(lo, vclock), sources.to_utc_epoch(hi, vclock))
    blocks, frames = {}, {}
    for tf in tfs:
        df = load_frame(symbol, tf, start=start, end=end, max_bars=max_bars, root=root, bars=bars,
                        pre_clean=pre_clean)
        if len(df) > MAX_BARS_PER_TF:
            raise ValueError(f"{symbol.upper()} {tf} has {len(df):,} bars in that window; a page "
                             f"carries at most {MAX_BARS_PER_TF:,}. Narrow it with start=/max_bars=")
        blocks[tf] = export.bars_block(df, include_volume=volume)
        frames[tf] = df
    status, check_lines, compared = {}, None, []
    if bars == "native":
        status = verification_status(symbol, tfs, root)
        for line in describe_verification(symbol, status):
            warnings.warn(line, DataWarning, stacklevel=2)
        if verify_m1:
            check = check_vs_m1(symbol, frames, start=start, end=end, root=root, pre_clean=pre_clean)
            check_lines = warn_check(symbol, covered_by_pricedata(check, status))
            compared = [tf for tf, r in check.items() if r["status"] != "unverified"]
    until = [f.attrs["pre_clean_until"] for f in frames.values() if f.attrs.get("pre_clean_until") is not None]
    notes = page_notes(symbol, tfs, bars=bars, status=status, check_lines=check_lines,
                       checked=compared, pre_clean_until=max(until) if until else None)
    if isinstance(indicators, str):
        indicators = chart.parse_indicators(indicators)
    s = chart.spec(symbol.upper(), blocks, exchange=EXCHANGE, source=SOURCE,
                   period_label=f"{default_tf} · {symbol.upper()} · BID",
                   default_tf=default_tf, trades=trades, zones=zones, equity=equity,
                   indicators=indicators or None, stats=stats, title=title, tz=tz, view=view,
                   notes=notes)
    problems = chart.validate(s)
    if problems:
        raise ValueError("invalid spec: " + "; ".join(problems))
    return s
