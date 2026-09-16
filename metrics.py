from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> tuple:
    cummax = equity.cummax()
    dd = equity / cummax - 1.0
    return dd.min(), dd.idxmin()


def compute_metrics(res) -> dict:
    eq = res.daily_equity
    trades = res.trades
    start = float(res.params["start_capital"])
    final = float(eq.iloc[-1])
    total_ret = final / start - 1.0
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (final / start) ** (1.0 / years) - 1.0

    ret = eq.pct_change().dropna()
    ann_vol = ret.std(ddof=1) * np.sqrt(TRADING_DAYS)
    ret_std = ret.std(ddof=1)
    sharpe = ret.mean() / ret_std * np.sqrt(TRADING_DAYS) if ret_std > 0 else 0.0
    downside = ret[ret < 0]
    sortino = (
        ret.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS)
        if len(downside) > 1 and downside.std() > 0
        else 0.0
    )
    mdd, mdd_date = max_drawdown(eq)
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    stats = {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "years": round(years, 2),
        "start_capital": round(start, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "max_dd_date": str(mdd_date.date()) if mdd < 0 else "",
        "calmar": round(calmar, 2),
    }

    if trades is not None and len(trades):
        wins = trades[trades["net"] > 0]
        losses = trades[trades["net"] <= 0]
        gross_win = wins["net"].sum()
        gross_loss = -losses["net"].sum()
        stats.update({
            "trades": int(len(trades)),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "avg_win": round(wins["net"].mean(), 2) if len(wins) else 0.0,
            "avg_loss": round(losses["net"].mean(), 2) if len(losses) else 0.0,
            "avg_net_per_trade": round(trades["net"].mean(), 2),
            "total_commission": round(trades["commission"].sum(), 2),
            "total_swap": round(trades["swap"].sum(), 2),
            "long_trades": int((trades["dir"] > 0).sum()),
            "short_trades": int((trades["dir"] < 0).sum()),
        })
    return stats


METRIC_ORDER = [
    ("start", "Period start"), ("end", "Period end"), ("years", "Years"),
    ("start_capital", "Start capital"), ("final_equity", "Final equity"),
    ("total_return_pct", "Total return %"), ("cagr_pct", "CAGR %"),
    ("ann_vol_pct", "Ann. volatility %"), ("sharpe", "Sharpe"),
    ("sortino", "Sortino"), ("max_drawdown_pct", "Max drawdown %"),
    ("max_dd_date", "Max DD date"), ("calmar", "Calmar"),
    ("trades", "Trades"), ("win_rate_pct", "Win rate %"),
    ("profit_factor", "Profit factor"), ("avg_win", "Avg win"),
    ("avg_loss", "Avg loss"), ("avg_net_per_trade", "Avg net/trade"),
    ("total_commission", "Total commission"), ("total_swap", "Total swap"),
    ("long_trades", "Long trades"), ("short_trades", "Short trades"),
]


def format_metrics(stats: dict) -> str:
    lines = ["==== Backtest metrics ===="]
    for key, label in METRIC_ORDER:
        if key in stats:
            lines.append(f"{label:<20} {stats[key]}")
    return "\n".join(lines)
