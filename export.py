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


def bars_block(df: pd.DataFrame, prefix: str | None = None, include_volume: bool = False) -> dict:
    """Resample-ready bars -> chart spec bar block (vectorized).

    ``include_volume`` defaults to False because the bundled viewer has no
    volume pane; pass True when a downstream consumer needs it.
    """
    if prefix is None:
        prefix = "bid_" if "bid_open" in df.columns else ""

    def col(name: str):
        return df[f"{prefix}{name}"].to_numpy(dtype=float)

    block = {
        "time": _epochs(_utc_index(df)),
        "open": np.round(col("open"), 4).tolist(),
        "high": np.round(col("high"), 4).tolist(),
        "low": np.round(col("low"), 4).tolist(),
        "close": np.round(col("close"), 4).tolist(),
    }
    vol = f"{prefix}volume"
    if include_volume and vol in df.columns:
        block["volume"] = [float(x) for x in df[vol].to_numpy()]
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


def indicator(name: str, values, color: str, width: int = 1) -> dict:
    clean = [None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 4)
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
