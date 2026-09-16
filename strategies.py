from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import Strategy, Target


def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h = df["bid_high"]
    l = df["bid_low"]
    c = df["bid_close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def donchian_direction(df: pd.DataFrame, n: int, allow_short: bool = True) -> np.ndarray:
    high = df["bid_high"].to_numpy(dtype=float)
    low = df["bid_low"].to_numpy(dtype=float)
    close = df["bid_close"].to_numpy(dtype=float)
    out = np.zeros(len(df), dtype=np.int8)
    for i in range(n, len(df)):
        up = high[i - n:i].max()
        lo = low[i - n:i].min()
        if close[i] > up:
            out[i] = 1
        elif allow_short and close[i] < lo:
            out[i] = -1
    return out


def ma_cross_direction(df: pd.DataFrame, fast: int, slow: int,
                       allow_short: bool = True) -> np.ndarray:
    c = df["bid_close"].to_numpy(dtype=float)
    out = np.zeros(len(df), dtype=np.int8)
    for i in range(slow, len(df)):
        f = c[i - fast + 1:i + 1].mean()
        s = c[i - slow + 1:i + 1].mean()
        if f > s:
            out[i] = 1
        elif allow_short and f < s:
            out[i] = -1
    return out


def _with_atr(df: pd.DataFrame) -> pd.DataFrame:
    """Attach a precomputed ATR column so decide() stays O(1) per bar."""
    if "_atr" in df.columns:
        return df
    df = df.copy()
    df["_atr"] = atr_wilder(df)
    return df


class _AtrStrategy:
    def prepare(self, signal: pd.DataFrame) -> pd.DataFrame:
        return _with_atr(signal) if (self.sl_atr or self.tp_atr) else signal

    def _atr_last(self, df: pd.DataFrame) -> float:
        if "_atr" in df.columns:
            return float(df["_atr"].iloc[-1])
        return float(atr_wilder(df).iloc[-1]) if (self.sl_atr or self.tp_atr) else 0.0


class DonchianBreakout(_AtrStrategy, Strategy):
    signal_timeframe = "D1"

    def __init__(self, n: int = 20, lots: float = 0.5, sl_atr: float = 0.0,
                 tp_atr: float = 0.0, allow_short: bool = True):
        self.n = n
        self.lots = lots
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.allow_short = allow_short

    def decide(self, closed_bars: pd.DataFrame, equity: float) -> Target:
        df = closed_bars
        if len(df) < self.n + 2:
            return Target()
        prev = df.iloc[:-1]
        upper = prev["bid_high"].tail(self.n).max()
        lower = prev["bid_low"].tail(self.n).min()
        close = df["bid_close"].iloc[-1]
        atr = self._atr_last(df)
        if close > upper:
            return Target(direction=1, lots=self.lots,
                          sl_dist=atr * self.sl_atr, tp_dist=atr * self.tp_atr)
        if self.allow_short and close < lower:
            return Target(direction=-1, lots=self.lots,
                          sl_dist=atr * self.sl_atr, tp_dist=atr * self.tp_atr)
        return Target()


class MovingAverageCross(_AtrStrategy, Strategy):
    signal_timeframe = "D1"

    def __init__(self, fast: int = 20, slow: int = 60, lots: float = 0.5,
                 sl_atr: float = 0.0, tp_atr: float = 0.0, allow_short: bool = True):
        self.fast = fast
        self.slow = slow
        self.lots = lots
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.allow_short = allow_short

    def decide(self, closed_bars: pd.DataFrame, equity: float) -> Target:
        df = closed_bars
        if len(df) < self.slow + 1:
            return Target()
        c = df["bid_close"]
        fast = c.iloc[-self.fast:].mean()
        slow = c.iloc[-self.slow:].mean()
        atr = self._atr_last(df)
        if fast > slow:
            return Target(direction=1, lots=self.lots,
                          sl_dist=atr * self.sl_atr, tp_dist=atr * self.tp_atr)
        if self.allow_short and fast < slow:
            return Target(direction=-1, lots=self.lots,
                          sl_dist=atr * self.sl_atr, tp_dist=atr * self.tp_atr)
        return Target()


STRATEGIES = {"donchian": DonchianBreakout, "macross": MovingAverageCross}


def make_strategy(kind: str, **kw) -> Strategy:
    if kind not in STRATEGIES:
        raise ValueError(f"unknown strategy: {kind}")
    return STRATEGIES[kind](**kw)
