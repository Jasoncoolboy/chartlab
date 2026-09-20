from __future__ import annotations

import numpy as np
import pandas as pd

from . import chart


def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.as_unit("ns")


def _epochs(index) -> list:
    return [int(x) for x in (np.asarray(index.asi8) // 1_000_000_000)]


def ohlc_columns(df: pd.DataFrame, prefix: str | None = None) -> dict:
    """Map ``open/high/low/close/volume`` to the frame's own column names.

    Understands the three shapes used across this research: priceData's
    ``Open/High/Low/Close/Volume``, plain lowercase, and the Dukascopy
    pipeline's ``bid_*``. An explicit ``prefix`` (e.g. ``"ask_"``) wins.
    ``volume`` is absent from the result when the frame has none.
    """
    cols = set(df.columns)
    if prefix is not None:
        style = lambda n: f"{prefix}{n}"
    elif "bid_open" in cols:
        style = lambda n: f"bid_{n}"
    elif "open" in cols:
        style = lambda n: n
    elif "Open" in cols:
        style = lambda n: n.title()
    else:
        raise KeyError(f"no OHLC columns found in {sorted(cols)}; expected "
                       "Open/High/Low/Close, open/high/low/close or bid_open/...")
    out = {n: style(n) for n in ("open", "high", "low", "close")}
    missing = [c for c in out.values() if c not in cols]
    if missing:
        raise KeyError(f"frame is missing OHLC columns {missing}; has {sorted(cols)}")
    if style("volume") in cols:
        out["volume"] = style("volume")
    return out


def to_bid_frame(df: pd.DataFrame) -> pd.DataFrame:
    """A frame with ``bid_open/high/low/close[/volume]`` columns (rename only).

    The setup finders and strategies read the Dukascopy-style ``bid_*``
    columns; this lets a priceData frame (already BID) go through unchanged.
    """
    if "bid_open" in df.columns:
        return df
    cols = ohlc_columns(df)
    return df[list(cols.values())].rename(columns={v: f"bid_{k}" for k, v in cols.items()})


def bars_block(df: pd.DataFrame, prefix: str | None = None,
               include_volume: bool = False, digits: int = 8) -> dict:
    """Bars frame -> chart spec bar block (vectorized).

    Prices are rounded to ``digits`` decimals (default 8) only to strip float
    noise. ⚠ Never round to fewer decimals than the instrument quotes: 4 dp is
    one whole pip on a 5-digit FX pair and distorts most M1-M15 candles.

    ``include_volume`` defaults to False; pass True to add the volume series
    (the viewer then shows the histogram pane and toggle).
    """
    cols = ohlc_columns(df, prefix)

    def col(name: str):
        return np.round(df[cols[name]].to_numpy(dtype=float), digits).tolist()

    block = {"time": _epochs(_utc_index(df)), "open": col("open"), "high": col("high"),
             "low": col("low"), "close": col("close")}
    if include_volume and "volume" in cols:
        block["volume"] = [float(x) for x in df[cols["volume"]].to_numpy()]
    return block


def _epoch_or_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return chart.to_epoch(pd.Timestamp(value))


def trades_list(df: pd.DataFrame) -> list:
    out = []
    for r in df.to_dict("records"):
        rec = {
            "dir": "long" if int(r["dir"]) == 1 else "short",
            "entryTime": _epoch_or_none(r["entry_time"]),
            "entryPrice": float(r["entry_price"]),
        }
        if "lots" in r and not pd.isna(r["lots"]):
            rec["lots"] = float(r["lots"])
        exit_t = _epoch_or_none(r.get("exit_time"))
        if exit_t is not None:
            rec["exitTime"] = exit_t
            if r.get("exit_price") is not None and not pd.isna(r.get("exit_price")):
                rec["exitPrice"] = float(r["exit_price"])
            if r.get("net") is not None and not pd.isna(r.get("net")):
                rec["net"] = float(r["net"])
            if r.get("exit_reason"):
                rec["reason"] = str(r["exit_reason"])
        out.append(rec)
    return out


def equity_block(equity) -> dict | None:
    if equity is None:
        return None
    if isinstance(equity, pd.Series):
        series = equity
    elif "equity" in getattr(equity, "columns", []):
        series = equity["equity"]
    else:
        return None
    if series.empty:
        return None
    idx = pd.DatetimeIndex(series.index)
    return {
        "time": _epochs(_utc_index(series.to_frame("equity"))),
        "value": [round(float(x), 2) for x in series.to_numpy()],
        "label": "Equity",
    }


def indicator(name: str, values, color: str, width: int = 1, digits: int = 8) -> dict:
    clean = [None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), digits)
             for v in values]
    return {"name": name, "color": color, "width": width, "values": clean}


_STAT_FIELDS = [
    ("total_return_pct", "Return", "pct", "sign"),
    ("cagr_pct", "CAGR", "pct", "sign"),
    ("sharpe", "Sharpe", "num", None),
    ("sortino", "Sortino", "num", None),
    ("max_drawdown_pct", "Max DD", "pct", "sign"),
    ("profit_factor", "Profit factor", "num", None),
    ("win_rate_pct", "Win rate", "pct", None),
    ("trades", "Trades", "int", None),
    ("avg_net_per_trade", "Avg trade", "money", "sign"),
    ("total_swap", "Swap", "money", "sign"),
    ("final_equity", "Final equity", "money", None),
]


def _fmt_stat(value, kind, signed):
    if kind == "pct":
        return f"{value:+.2f}%" if signed else f"{value:.2f}%"
    if kind == "money":
        return f"{value:+,.0f}" if signed else f"{value:,.0f}"
    if kind == "int":
        return f"{int(round(value)):,}"
    return f"{value:.2f}"


def stats_block(metrics: dict) -> list:
    """Turn an engine metrics dict into chart-spec ``stats`` rows."""
    rows = []
    for key, label, kind, tone_kind in _STAT_FIELDS:
        value = metrics.get(key)
        if value is None:
            continue
        signed = tone_kind == "sign"
        row = {"label": label, "value": _fmt_stat(float(value), kind, signed)}
        if signed:
            row["tone"] = "up" if float(value) >= 0 else "down"
        rows.append(row)
    return rows
