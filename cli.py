from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import chart, data, dukascopy, export, metrics, setups
from . import strategies as strat
from .config import ROOT, BacktestConfig, CostConfig, DataConfig
from .engine import run_backtest

DATA = ROOT / "data"
OUT = ROOT / "out"


def _log(msg):
    print(msg, flush=True)


def cmd_download(args):
    cfg = DataConfig(start=args.start, end=args.end, base_dir=DATA)
    stats = dukascopy.download_range(cfg, log=_log)
    print("stats:", json.dumps(stats))
    if stats["failed"]:
        print("failures:", len(stats["failed"]), stats["failed"][:5], file=sys.stderr)
        sys.exit(1)


def cmd_build(args):
    cfg = DataConfig(start=args.start, end=args.end, base_dir=DATA)
    print(json.dumps(data.build_m1(cfg), indent=2))


def cmd_resample(args):
    frames = data.build_timeframes(DATA / "parquet" / "XAUUSD_M1.parquet",
                                   DATA / "parquet" / "tfs", "XAUUSD")
    for tf, df in frames.items():
        print(f"{tf}: rows={len(df)} first={df.index[0]} last={df.index[-1]}")


def cmd_backtest(args):
    cost = CostConfig.from_json(args.cost_file) if args.cost_file else CostConfig()
    bt = BacktestConfig(default_lots=args.lots, signal_timeframe=args.timeframe)
    tf = args.timeframe

    m1 = data.load_m1_parquet(DATA / "parquet" / "XAUUSD_M1.parquet")
    if args.m1_limit and args.m1_limit < len(m1):
        m1 = m1.iloc[: args.m1_limit]

    signal_df = pd.read_parquet(DATA / "parquet" / "tfs" / f"XAUUSD_{tf}.parquet")
    signal_df.index = pd.DatetimeIndex(signal_df.index).as_unit("ns")
    signal_df.index.name = "time"
    signal_df = signal_df.loc[m1.index[0]: m1.index[-1]]

    strat_obj = strat.make_strategy(
        args.strategy, lots=args.lots, sl_atr=args.sl_atr, tp_atr=args.tp_atr,
        **({"fast": args.n, "slow": args.slow} if args.strategy == "macross"
           else {"n": args.n}))
    strat_obj.signal_timeframe = tf

    res = run_backtest(m1, signal_df, strat_obj, cost, bt, log=_log)
    stats = metrics.compute_metrics(res)
    print(metrics.format_metrics(stats))

    first_close = signal_df["bid_close"].iloc[0]
    bh = bt.start_capital_usd + (signal_df["bid_close"] - first_close) * args.lots * cost.contract_size_oz
    print(f"\nBuy&hold same lots ref: return {(bh.iloc[-1] / bt.start_capital_usd - 1.0) * 100:.2f}%")

    if not args.no_save:
        OUT.mkdir(parents=True, exist_ok=True)
        name = f"{args.strategy}_{tf}"
        res.daily_equity.to_csv(OUT / f"equity_{name}.csv")
        res.trades.to_csv(OUT / f"trades_{name}.csv", index=False)
        (OUT / f"metrics_{name}.json").write_text(json.dumps(stats, indent=2))
        print(f"saved -> out/equity_{name}.csv, out/trades_{name}.csv")


def _load_trades(path):
    if not path:
        return None
    return export.trades_list(pd.read_csv(path))


def _load_equity(path):
    if not path:
        return None
    df = pd.read_csv(path)
    idx = pd.to_datetime(df.iloc[:, 0])
    if idx.dt.tz is None:
        idx = idx.dt.tz_localize("UTC")
    df = df.set_index(idx).drop(columns=[df.columns[0]])
    return export.equity_block(df if "equity" in df.columns else df.iloc[:, -1])


def cmd_chart(args):
    m1 = data.load_m1_parquet(args.data)
    if args.start:
        m1 = m1.loc[m1.index >= pd.Timestamp(args.start)]
    if args.end:
        m1 = m1.loc[m1.index < pd.Timestamp(args.end)]

    tfs = list(dict.fromkeys([args.timeframe] + (args.extra_tfs or [])))
    bars_by_tf = {tf: export.bars_block(data.resample(m1, tf)) for tf in tfs}

    zones = None
    if args.zones_file:
        zones = chart.zones_from_rows(json.loads(Path(args.zones_file).read_text()))

    name = args.name or (Path(args.trades_file).stem if args.trades_file else "chart")
    if not name.endswith(".html"):
        name += ".html"
    period_label = args.period_label or f"{args.timeframe} · {args.symbol}"

    stats = None
    if args.stats_file:
        stats = export.stats_block(json.loads(Path(args.stats_file).read_text()))

    page = chart.spec(
        args.symbol, bars_by_tf, exchange=args.exchange,
        period_label=period_label, default_tf=args.timeframe,
        trades=_load_trades(args.trades_file), zones=zones,
        equity=_load_equity(args.equity_file),
        indicators=chart.parse_indicators(args.inds), stats=stats)
    out = chart.render(page, OUT / "charts" / name, compact=args.compact)
    print("wrote", out)
    print("gallery", chart.gallery(OUT / "charts", title="Charts", subtitle="offline pages"))


def cmd_setup(args):
    df = data.load_bars(args.data, tf=args.timeframe,
                        start=args.start or None, end=args.end or None)
    params = {"n": args.n} if args.kind == "donchian" else {"fast": args.n, "slow": args.slow}
    if args.sl_atr:
        params["sl_atr"] = args.sl_atr
    if args.tp_atr:
        params["tp_atr"] = args.tp_atr
    opts = {"limit": args.limit, "dir": args.dir,
            "start": pd.Timestamp(args.start) if args.start else None,
            "end": pd.Timestamp(args.end) if args.end else None}
    found = setups.find_all(df, args.kind, params, opts)
    for s in found:
        s.tf = args.timeframe
    found.sort(key=lambda s: s.trigger_time)

    pstr = f"Donchian({args.n})" if args.kind == "donchian" else f"MA {args.n}/{args.slow}"
    extra = []
    if args.sl_atr or args.tp_atr:
        extra.append(f"ATR SL/TP {args.sl_atr:g}/{args.tp_atr:g}")
    if args.dir:
        extra.append(args.dir)
    if args.start or args.end:
        extra.append(f"{args.start or '...'}→{args.end or '...'}")
    label = pstr + ((" · " + " · ".join(extra)) if extra else "")

    out_dir = OUT / "charts" / (args.out or "setups")
    pages = setups.render_pages(df, found, out_dir, pre=args.pre, post=args.post)
    cfg = {"label": label, "tf": args.timeframe, "kind": args.kind, "params": params,
           "dir": args.dir, "limit": args.limit, "start": args.start, "end": args.end}
    index = setups.write_catalog(out_dir, found, cfg)
    print(f"{len(found)} setups")
    for p in pages[:3]:
        print("wrote", p)
    if len(pages) > 3:
        print(f"... {len(pages) - 3} more pages in {out_dir}")
    print("catalog", index)
    print("gallery", chart.gallery(OUT / "charts", title="Charts", subtitle="offline pages"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="chartlab")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download", help="download M1 bi5 from Dukascopy")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("build", help="decode raw bi5 into M1 parquet")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("resample", help="resample M1 into higher timeframes")
    p.set_defaults(func=cmd_resample)

    p = sub.add_parser("backtest", help="run a costed backtest")
    p.add_argument("--strategy", default="donchian", choices=["donchian", "macross"])
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--slow", type=int, default=60)
    p.add_argument("--lots", type=float, default=0.5)
    p.add_argument("--sl-atr", type=float, default=0.0)
    p.add_argument("--tp-atr", type=float, default=0.0)
    p.add_argument("--cost-file", default=None)
    p.add_argument("--m1-limit", type=int, default=None)
    p.add_argument("--no-save", action="store_true")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("chart", help="render an HTML page from parquet + engine output")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--extra-tfs", nargs="*", default=None)
    p.add_argument("--data", default=str(DATA / "parquet" / "XAUUSD_M1.parquet"))
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--trades-file", default=None)
    p.add_argument("--equity-file", default=None)
    p.add_argument("--zones-file", default=None)
    p.add_argument("--stats-file", default=None,
                   help="metrics_*.json to show as a chart metrics panel")
    p.add_argument("--compact", action="store_true",
                   help="base64-encode bars (~3x smaller page, not human-readable)")
    p.add_argument("--inds", default=None, help='comma list e.g. "SMA50,EMA200"')
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--exchange", default="")
    p.add_argument("--period-label", default=None)
    p.add_argument("--name", default=None)
    p.set_defaults(func=cmd_chart)

    p = sub.add_parser("setup", help="generate a setup catalog + annotated pages")
    p.add_argument("--kind", default="donchian", choices=["donchian", "macross"])
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--n", type=int, default=20, help="donchian N / macross fast")
    p.add_argument("--slow", type=int, default=60, help="macross slow")
    p.add_argument("--sl-atr", type=float, default=2.0)
    p.add_argument("--tp-atr", type=float, default=2.0)
    p.add_argument("--dir", default=None, choices=["long", "short"])
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--pre", type=int, default=60)
    p.add_argument("--post", type=int, default=45)
    p.add_argument("--data", default=str(DATA / "parquet" / "XAUUSD_M1.parquet"))
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_setup)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
