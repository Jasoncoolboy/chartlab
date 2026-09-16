from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from . import dukascopy as dk
from .config import DataConfig


TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
              "H1": 3600, "H4": 14400, "D1": 86400}
_SIDES = ("bid", "ask")


def _group_index(index: pd.DatetimeIndex, tf: str) -> pd.Index:
    index = index.as_unit("ns")
    if tf == "W1":
        day = index.normalize()
        return day - pd.to_timedelta(index.dayofweek, unit="D")
    secs = TF_SECONDS[tf]
    ns = index.asi8
    floor_ns = (ns // (secs * 1_000_000_000)) * (secs * 1_000_000_000)
    return pd.to_datetime(floor_ns, unit="ns")


def resample(m1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "M1":
        return m1.copy()
    groups = _group_index(m1.index, tf)
    frames = []
    for side in _SIDES:
        sub = m1[[f"{side}_open", f"{side}_high", f"{side}_low",
                  f"{side}_close", f"{side}_volume"]].copy()
        sub["_g"] = groups
        agg = sub.groupby("_g").agg(
            open=(f"{side}_open", "first"),
            high=(f"{side}_high", "max"),
            low=(f"{side}_low", "min"),
            close=(f"{side}_close", "last"),
            volume=(f"{side}_volume", "sum"),
        )
        agg.columns = [f"{side}_{c}" for c in agg.columns]
        frames.append(agg)
    out = frames[0].join(frames[1], how="outer").sort_index()
    out.index.name = "time"
    return out


def build_all(m1: pd.DataFrame, timeframes=("M5", "M15", "M30", "H1", "H4", "D1", "W1")) -> dict:
    return {tf: resample(m1, tf) for tf in timeframes}


def load_m1_parquet(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.DatetimeIndex(df.index).as_unit("ns")
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "time"
    return df


def load_bars(path: str | Path, tf: str | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
    df = load_m1_parquet(path)
    if tf and tf.upper() != "M1":
        df = resample(df, tf.upper())
    if start:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end:
        df = df.loc[df.index < pd.Timestamp(end)]
    return df


def _frame(symbol: str, day: dt.date, side: str, raw_root: Path) -> pd.DataFrame:
    path = raw_root / symbol / side / f"{day.year:04d}-{day.month:02d}-{day.day:02d}.bi5"
    if not path.exists():
        return pd.DataFrame()
    body = path.read_bytes()
    if not dk.is_bi5_body(body):
        return pd.DataFrame()
    df = dk.decode_candle_buffer(body, day)
    if df.empty:
        return df
    return df.loc[df["volume"] > 0]


def load_m1(cfg: DataConfig) -> pd.DataFrame:
    raw_root = cfg.base_dir / "raw"
    end = dt.date.fromisoformat(cfg.end) if cfg.end else dt.date.today()
    frames = []
    for day in dk.trading_days(dt.date.fromisoformat(cfg.start), end):
        bid = _frame(cfg.symbol, day, "BID", raw_root)
        ask = _frame(cfg.symbol, day, "ASK", raw_root)
        if bid.empty:
            continue
        bid.columns = [f"bid_{c}" for c in bid.columns]
        ask.columns = [f"ask_{c}" for c in ask.columns]
        frames.append(bid.join(ask, how="inner"))
    if not frames:
        raise FileNotFoundError(f"no raw data decoded under {raw_root}")
    m1 = pd.concat(frames)
    m1 = m1.loc[~m1.index.duplicated(keep="last")].sort_index()
    return m1.loc[(m1["bid_volume"] > 0) & (m1["ask_volume"] > 0)]


def verify_m1(m1: pd.DataFrame) -> dict:
    spread = (m1["ask_open"] - m1["bid_open"]).to_numpy()
    flat = (
        (m1["bid_open"] == m1["bid_high"])
        & (m1["bid_high"] == m1["bid_low"])
        & (m1["bid_low"] == m1["bid_close"])
        & (m1["bid_volume"] > 0)
    ).sum()
    oh = (m1["bid_high"] >= m1[["bid_open", "bid_close", "bid_low"]].max(axis=1)).all()
    ol = (m1["bid_low"] <= m1[["bid_open", "bid_close", "bid_high"]].min(axis=1)).all()
    gaps = m1.index.to_series().diff().dt.total_seconds()
    return {
        "min_spread": float(spread.min()),
        "pct_negative_spread": float((spread < 0).mean() * 100),
        "pct_flat_real_bars": float(flat / len(m1) * 100),
        "ohlc_high_ok": bool(oh),
        "ohlc_low_ok": bool(ol),
        "bars_with_gap_gt_60s": int((gaps > 60).sum()),
    }


def build_m1(cfg: DataConfig, out_path: str | Path | None = None) -> dict:
    m1 = load_m1(cfg)
    out_path = Path(out_path) if out_path else cfg.m1_parquet
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["bid_open", "bid_high", "bid_low", "bid_close", "bid_volume",
            "ask_open", "ask_high", "ask_low", "ask_close", "ask_volume"]
    m1[cols].to_parquet(out_path)
    return {
        "rows": len(m1),
        "first": str(m1.index[0]),
        "last": str(m1.index[-1]),
        "days": len({ts.date() for ts in m1.index}),
        "years_span": float((m1.index[-1] - m1.index[0]).days) / 365.25,
        "out": str(out_path),
        "integrity": verify_m1(m1),
    }


def build_timeframes(input_path: str | Path, out_dir: str | Path, symbol: str,
                     timeframes=("M5", "M15", "M30", "H1", "H4", "D1", "W1")) -> dict:
    m1 = load_m1_parquet(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = build_all(m1, timeframes)
    for tf, df in frames.items():
        df.to_parquet(out_dir / f"{symbol}_{tf}.parquet")
    return frames
