from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import sources
from .config import BacktestConfig, CostConfig


@dataclass
class Target:
    direction: int = 0
    lots: float = 0.0
    sl_dist: float = 0.0
    tp_dist: float = 0.0


@dataclass
class Trade:
    dir: int
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: Optional[pd.Timestamp]
    exit_price: Optional[float]
    exit_reason: str
    gross: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    net: float = 0.0

    def to_dict(self):
        return {
            "dir": self.dir,
            "lots": self.lots,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "gross": self.gross,
            "commission": self.commission,
            "swap": self.swap,
            "net": self.net,
        }


@dataclass
class Results:
    equity: pd.Series
    trades: pd.DataFrame
    params: dict = field(default_factory=dict)

    @property
    def daily_equity(self) -> pd.Series:
        return self.equity.resample("1D").last().dropna()


class _Position:
    __slots__ = ("dir", "lots", "entry_time", "entry_price", "entry_i",
                 "entry_day", "sl", "tp", "commission", "swap")

    def __init__(self, direction, lots, entry_time, entry_price, entry_i,
                 entry_day, sl, tp, commission):
        self.dir = direction
        self.lots = lots
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.entry_i = entry_i
        self.entry_day = entry_day
        self.sl = sl
        self.tp = tp
        self.commission = commission
        self.swap = 0.0


class Strategy:
    signal_timeframe = "D1"

    def prepare(self, signal: pd.DataFrame) -> pd.DataFrame:
        """Precompute reusable columns once; return the frame to iterate over."""
        return signal

    def decide(self, closed_bars: pd.DataFrame, equity: float) -> Target:
        raise NotImplementedError


def run_backtest(
    m1: pd.DataFrame,
    signal: pd.DataFrame,
    strategy: Strategy,
    cost: CostConfig,
    bt: BacktestConfig,
    log=None,
) -> Results:
    bo = m1["bid_open"].to_numpy()
    bh = m1["bid_high"].to_numpy()
    bl = m1["bid_low"].to_numpy()
    bc = m1["bid_close"].to_numpy()
    ao = m1["ask_open"].to_numpy()
    ah = m1["ask_high"].to_numpy()
    al = m1["ask_low"].to_numpy()
    ac = m1["ask_close"].to_numpy()
    ts = m1.index
    ns = ts.asi8

    # Vectorized calendar fields: avoids boxing a Timestamp per M1 row.
    day_a = np.asarray(ts.normalize(), dtype="datetime64[ns]")
    # Swap rolls over at 00:00 FTMO SERVER time, whatever clock the bars are in, and is found as a
    # change of server day between two bars: gold has no bar at 00:00 server (daily break
    # 23:50-01:05), so testing for a bar at midnight would never charge it on FTMO data.
    clock = getattr(bt, "data_clock", "utc")
    if clock not in ("utc", "ftmo"):
        raise ValueError(f"BacktestConfig.data_clock must be 'utc' or 'ftmo', got {clock!r}")
    server = sources.utc_to_ftmo_server(ts) if clock == "utc" else sources._naive_ns(ts)
    server_day = server.asi8 // 86_400_000_000_000          # whole days since 1970-01-01 (a Thursday)

    signal = strategy.prepare(signal)
    tf_open_ns = signal.index.asi8
    j_of = np.searchsorted(tf_open_ns, ns, side="right") - 1
    n_signal = len(signal)

    slippage = cost.slippage_per_side_usd
    add = cost.spread_add_per_side_usd
    comm_lot = cost.commission_per_side_per_lot
    swap_long = cost.swap_long_per_lot_per_day
    swap_short = cost.swap_short_per_lot_per_day
    triple = cost.triple_swap_on_wednesday

    cash = bt.start_capital_usd
    pending: Optional[list] = None
    pos: Optional[_Position] = None
    trades: list[Trade] = []
    daily_eq: dict[pd.Timestamp, float] = {}

    def close_pos(px: float, reason: str, i: int):
        nonlocal cash, pos
        t = pos
        gross = (px - t.entry_price) * t.dir * t.lots * cost.contract_size_oz
        trades.append(Trade(
            dir=t.dir, lots=t.lots,
            entry_time=t.entry_time, entry_price=t.entry_price,
            exit_time=ts[i], exit_price=px, exit_reason=reason,
            gross=gross, commission=t.commission, swap=t.swap,
            net=gross - t.commission + t.swap,
        ))
        cash += gross
        pos = None

    def open_pos(direction: int, lots: float, sl_dist: float, tp_dist: float, i: int):
        nonlocal cash, pos
        if direction > 0:
            px = ao[i] + add + slippage
            sl = px - sl_dist if sl_dist > 0 else None
            tp = px + tp_dist if tp_dist > 0 else None
        else:
            px = bo[i] - add - slippage
            sl = px + sl_dist if sl_dist > 0 else None
            tp = px - tp_dist if tp_dist > 0 else None
        comm_pct = getattr(cost, "commission_pct_side", 0.0) / 100.0 * px * cost.contract_size_oz
        commission = (comm_lot + comm_pct) * lots * 2.0          # both sides, charged at entry
        cash -= commission
        pos = _Position(direction, lots, ts[i], px, i, day_a[i], sl, tp, commission)

    def check_swap(i: int):
        """Charge every server day that ended between bar i-1 and bar i: Monday to Friday one
        night each, Wednesday (the Wed->Thu rollover) three, Saturday and Sunday none."""
        nonlocal cash
        if pos is None or i == 0 or server_day[i] == server_day[i - 1]:
            return
        nights = 0
        for d in range(int(server_day[i - 1]), int(server_day[i])):
            wd = (d + 3) % 7                                  # Monday = 0
            if wd < 5:
                nights += 3 if (triple and wd == 2) else 1
        if not nights:
            return
        rate = swap_long if pos.dir > 0 else swap_short
        amt = rate * pos.lots * nights
        pos.swap += amt
        cash += amt

    n = len(m1)
    cur_day = None
    last_mark = cash
    for i in range(n):
        # Before this bar's orders: a position closed at this open was still held over the rollover
        # (it pays); one opened at this open was not (it does not).
        check_swap(i)
        if pending is not None:
            for action in pending:
                if action == "close" and pos is not None:
                    px = bo[i] - add - slippage if pos.dir > 0 else ao[i] + add + slippage
                    close_pos(px, "signal", i)
                elif isinstance(action, dict):
                    open_pos(action["dir"], action["lots"],
                             action.get("sl", 0.0), action.get("tp", 0.0), i)
            pending = None

        if pos is not None:
            hit = None
            if pos.dir > 0:
                if pos.sl is not None and bl[i] <= pos.sl:
                    px = bo[i] if bo[i] <= pos.sl else pos.sl
                    hit = (px - slippage, "sl")
                elif pos.tp is not None and bh[i] >= pos.tp:
                    px = bo[i] if bo[i] >= pos.tp else pos.tp
                    hit = (px - slippage, "tp")
            else:
                if pos.sl is not None and ah[i] >= pos.sl:
                    px = ao[i] if ao[i] >= pos.sl else pos.sl
                    hit = (px + slippage, "sl")
                elif pos.tp is not None and al[i] <= pos.tp:
                    px = ao[i] if ao[i] <= pos.tp else pos.tp
                    hit = (px + slippage, "tp")
            if hit is not None:
                close_pos(*hit, i)

        if i < n - 1 and j_of[i + 1] > j_of[i]:
            j = j_of[i]
            if 0 <= j < n_signal:
                closed = signal.iloc[: j + 1]
                target = strategy.decide(closed, cash)
                if target.direction == 0:
                    if pos is not None:
                        pending = ["close"]
                elif pos is None:
                    pending = [{"dir": target.direction, "lots": target.lots,
                                "sl": target.sl_dist, "tp": target.tp_dist}]
                elif pos.dir != target.direction:
                    pending = ["close", {"dir": target.direction, "lots": target.lots,
                                         "sl": target.sl_dist, "tp": target.tp_dist}]

        mark = cash
        if pos is not None:
            if pos.dir > 0:
                mark += (bc[i] - pos.entry_price) * pos.lots * cost.contract_size_oz
            else:
                mark += (pos.entry_price - ac[i]) * pos.lots * cost.contract_size_oz
        d = day_a[i]
        if d != cur_day:
            if cur_day is not None:
                daily_eq[cur_day] = last_mark
            cur_day = d
        last_mark = mark

    if cur_day is not None:
        daily_eq[cur_day] = last_mark
    if pos is not None:
        px = bo[n - 1] - add - slippage if pos.dir > 0 else ao[n - 1] + add + slippage
        close_pos(px, "end", n - 1)

    eq = pd.Series(daily_eq, name="equity")
    eq.index = pd.DatetimeIndex(eq.index, name="time")
    trade_df = pd.DataFrame([t.to_dict() for t in trades])
    return Results(eq, trade_df, params={
        "signal_timeframe": strategy.signal_timeframe,
        "symbol": cost.symbol,
        "start_capital": bt.start_capital_usd,
    })
