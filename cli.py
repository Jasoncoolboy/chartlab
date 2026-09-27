from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import pandas as pd

from . import chart, data, dukascopy, export, metrics, pricedata, setups, sources
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


def _ftmo_bid_ask(df: pd.DataFrame, spread: float) -> pd.DataFrame:
    """priceData BID bars as the engine's bid/ask columns: ask = BID + the typical spread."""
    out = pd.DataFrame(index=pd.DatetimeIndex(df.index).as_unit("ns"))
    for side, add in (("bid", 0.0), ("ask", spread)):
        for c in ("Open", "High", "Low", "Close"):
            out[f"{side}_{c.lower()}"] = df[c].to_numpy(dtype=float) + add
        out[f"{side}_volume"] = df["Volume"].to_numpy() if "Volume" in df.columns else 0
    out.index.name = "time"
    return out


def cmd_backtest(args):
    symbol = args.symbol.upper()
    if args.source == "dukascopy" and symbol != "XAUUSD":
        raise SystemExit("--source dukascopy backtests the local Dukascopy XAUUSD parquet only")
    try:
        cost = CostConfig.from_json(args.cost_file) if args.cost_file else CostConfig(symbol=symbol)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None
    print(f"warning: legacy backtest demo - never quote its numbers; use the backtest-method rules. Costs "
          f"({cost.symbol}, from priceData COST_MODELS unless --cost-file): commission "
          f"{cost.commission_per_side_per_lot:g} $/lot + {cost.commission_pct_side:g} % of notional per side, "
          f"slippage {cost.slippage_per_side_usd:g} per market fill, swap {cost.swap_long_per_lot_per_day:g} / "
          f"{cost.swap_short_per_lot_per_day:g} $/lot/night at 00:00 FTMO server (Wed->Thu x3).",
          file=sys.stderr)
    tf = args.timeframe

    if args.source == "ftmo":
        # priceData's clean years, true UTC; the ask is BID + priceData's typical spread (paid once per round trip).
        model = pricedata.cost_model(symbol, args.root)
        spread = float(model["spread_pip"]) * float(model["pip"])
        m1 = pricedata.load_frame(symbol, "M1", root=args.root)
        if args.m1_limit and args.m1_limit < len(m1):
            m1 = m1.iloc[: args.m1_limit]
        m1 = _ftmo_bid_ask(m1, spread)
        signal_df = _ftmo_bid_ask(pricedata.load_frame(symbol, tf, root=args.root), spread)
        bt = BacktestConfig(default_lots=args.lots, signal_timeframe=tf, data_clock="utc")
    else:
        m1 = data.load_m1_parquet(DATA / "parquet" / "XAUUSD_M1.parquet")
        if args.m1_limit and args.m1_limit < len(m1):
            m1 = m1.iloc[: args.m1_limit]
        signal_df = pd.read_parquet(DATA / "parquet" / "tfs" / f"XAUUSD_{tf}.parquet")
        signal_df.index = pd.DatetimeIndex(signal_df.index).as_unit("ns")
        signal_df.index.name = "time"
        bt = BacktestConfig(default_lots=args.lots, signal_timeframe=tf)
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
        name = f"{args.strategy}_{tf}" if args.source == "dukascopy" else f"{symbol}_{args.strategy}_{tf}_ftmo"
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


def _source(args) -> str:
    """ftmo | dukascopy | auto ('pricedata' is the old name for ftmo)."""
    return "ftmo" if args.source == "pricedata" else args.source


def _auto_frame(args):
    """--source auto: read --data, identify FTMO vs Dukascopy, convert to UTC.

    Prints the evidence. Raises SourceError (exit message) if it cannot decide.
    """
    if not args.data:
        raise SystemExit("--source auto needs --data FILE (parquet or CSV)")
    raw = sources.load_file(args.data)
    problems = sources.check_frame(raw)
    if problems:
        raise SystemExit(f"{args.data} failed integrity checks: " + "; ".join(problems))
    try:
        det = sources.resolve(raw, args.assume)
    except sources.SourceError as exc:
        if args.assume is None:
            raise sources.SourceError(f"{exc} -- if you know which it is, add --assume ftmo|dukascopy") from None
        raise
    print("source:", det.summary())
    df = sources.to_utc(raw, det.source)
    inferred = sources.timeframe_name(df.index)
    tf = args.timeframe or inferred
    if tf is None:
        raise SystemExit("cannot infer the timeframe from the bar spacing; pass --timeframe")
    if inferred and args.timeframe and args.timeframe.upper() != inferred:
        raise SystemExit(f"--timeframe {args.timeframe} but the file's bars are {inferred}")
    return df, det.source, tf.upper()


def cmd_chart(args):
    src = _source(args)
    zones = json.loads(Path(args.zones_file).read_text()) if args.zones_file else None
    trades, equity = _load_trades(args.trades_file), _load_equity(args.equity_file)
    name = args.name or (Path(args.trades_file).stem if args.trades_file else "chart")
    if not name.endswith(".html"):
        name += ".html"
    stats = None
    if args.stats_file:
        stats = export.stats_block(json.loads(Path(args.stats_file).read_text()))

    _pre_clean_ftmo_only(args, src)
    if src == "ftmo":
        tf = (args.timeframe or "D1").upper()
        tfs = list(dict.fromkeys([tf] + [t.upper() for t in (args.extra_tfs or [])]))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", pricedata.DataWarning)
            page = pricedata.build_spec(
                args.symbol, tfs, default_tf=tf, start=args.start, end=args.end,
                max_bars=args.max_bars, volume=args.volume, trades=trades, zones=zones,
                equity=equity, clock=args.clock, indicators=args.inds, stats=stats,
                tz=args.tz, root=args.root, bars=args.bars, verify_m1=not args.no_m1_check,
                pre_clean=args.pre_clean)
        for w in caught:
            if issubclass(w.category, pricedata.DataWarning):
                print("WARNING:", w.message)
            else:
                warnings.warn_explicit(w.message, w.category, w.filename, w.lineno)
    elif src == "dukascopy":
        tf = (args.timeframe or "D1").upper()
        tfs = list(dict.fromkeys([tf] + [t.upper() for t in (args.extra_tfs or [])]))
        m1 = data.load_m1_parquet(args.data)
        sources.resolve(m1, "dukascopy")          # refuses an FTMO file passed as Dukascopy
        if args.start:
            m1 = m1.loc[m1.index >= pd.Timestamp(args.start)]
        if args.end:
            m1 = m1.loc[m1.index < pd.Timestamp(args.end)]
        trades, zones, equity = sources.convert_overlay(trades, zones, equity, args.clock or "utc")
        bars_by_tf = {t: export.bars_block(data.resample(m1, t), include_volume=args.volume)
                      for t in tfs}
        page = chart.spec(
            args.symbol.upper(), bars_by_tf, exchange="Dukascopy", source="dukascopy",
            period_label=f"{tf} · {args.symbol.upper()} · BID", default_tf=tf, trades=trades,
            zones=zones, equity=equity, indicators=chart.parse_indicators(args.inds),
            stats=stats, tz=args.tz, notes=_source_notes("dukascopy"))
    else:
        if args.extra_tfs:
            raise SystemExit("--source auto charts the one timeframe in the file; drop --extra-tfs")
        df, found_source, tf = _auto_frame(args)
        trades, zones, equity = sources.convert_overlay(trades, zones, equity, args.clock)
        page = chart.spec(
            args.symbol.upper(), {tf: export.bars_block(df, include_volume=args.volume)},
            exchange=sources.SOURCES[found_source]["label"], source=found_source,
            period_label=f"{tf} · {args.symbol.upper()} · BID", default_tf=tf, trades=trades,
            zones=zones, equity=equity, indicators=chart.parse_indicators(args.inds),
            stats=stats, tz=args.tz, notes=_source_notes(found_source, args.data))
    if args.exchange:
        page["exchange"] = args.exchange
    if args.period_label:
        page["periodLabel"] = args.period_label
    out = chart.render(page, OUT / "charts" / name, compact=args.compact)
    print("wrote", out)
    print("gallery", chart.gallery(OUT / "charts", title="Charts", subtitle="offline pages"))


def _pre_clean_ftmo_only(args, src):
    if getattr(args, "pre_clean", False) and src != "ftmo":
        raise SystemExit("--pre-clean adds priceData's pre-clean years: it needs --source ftmo")


def _source_notes(found: str, path=None) -> list:
    """The data note of a page whose bars are not read from priceData."""
    if path is not None and found == "ftmo":
        return [{"level": "warn", "text": f"FTMO bars read from {Path(path).name}, not from priceData: not "
                                          "verified here (FTMO pages come from priceData: --source ftmo)"}]
    if found == "dukascopy":
        return [{"level": "info", "text": "Dukascopy bars (true UTC): not FTMO data - an independent "
                                          "cross-check, never the FTMO test data"}]
    return []


def _setup_out(name):
    """(out_dir, lib_dir). A relative name lives in out/charts/ and shares out/lib;
    an absolute one is self-contained (lib/ inside it) so nothing lands outside."""
    p = Path(name)
    if p.is_absolute():
        return p, "lib"
    return OUT / "charts" / name, "../../lib"


def _load_setup_frames(args):
    """(primary UTC frame, {tf: extra UTC frame}, source name) for the chosen source."""
    src = _source(args)
    _pre_clean_ftmo_only(args, src)
    if src == "auto":
        if args.extra_tfs:
            raise SystemExit("--source auto uses the one timeframe in the file; drop --extra-tfs")
        df, found, tf = _auto_frame(args)
        args.timeframe = tf
        return df, {}, found
    args.timeframe = (args.timeframe or "D1").upper()
    others = [t.upper() for t in (args.extra_tfs or []) if t.upper() != args.timeframe]
    if src == "ftmo":
        # Full history up to --end: the finders need their warm-up bars; --start
        # only filters which setups are kept. Everything is UTC. Clean years only,
        # unless --pre-clean adds the price-verified ones before them.
        df = pricedata.load_frame(args.symbol, args.timeframe, end=args.end or None, root=args.root,
                                  bars=args.bars, pre_clean=args.pre_clean)
        extra = {t: pricedata.load_frame(args.symbol, t, end=args.end or None, root=args.root,
                                         bars=args.bars, pre_clean=args.pre_clean)
                 for t in others}
        return df, extra, "ftmo"
    df = data.load_bars(args.data, tf=args.timeframe, start=args.start or None, end=args.end or None)
    sources.resolve(df, "dukascopy")
    extra = {t: data.load_bars(args.data, tf=t, start=args.start or None, end=args.end or None)
             for t in others}
    return df, extra, "dukascopy"


def _check_pages(args, df, extra_frames, found, src) -> tuple:
    """Data notes of setup pages: ``(notes for every page, {setup id: notes})``.

    FTMO bars from priceData: priceData's own verdict per timeframe, ChartLab's
    comparison of native bars with M1 over the bars the pages show (what disagrees
    and how many pages show it is also printed), and a label on each page that
    shows pre-clean bars. Bars from a file: one note naming what they are.
    """
    if src != "ftmo" or _source(args) == "auto":
        return _source_notes(src, args.data if _source(args) == "auto" else None), {}
    if not found:
        return [], {}
    sym = args.symbol.upper()
    wins = setups.page_windows(df, found, args.pre, args.post, extra_frames)
    frames = {args.timeframe: df, **extra_frames}
    status = pricedata.verification_status(sym, list(frames), args.root) if args.bars == "native" else {}
    lines = pricedata.describe_verification(sym, status)
    check = args.bars == "native" and not args.no_m1_check
    bad, open_lines, compared = {}, {}, set()
    for tf, frame in frames.items():
        spans = [w[tf] for w in wins if tf in w]
        if not check or tf == "M1" or not spans:
            continue
        step = pd.Timedelta(seconds=sources.spacing_seconds(frame.index) or 60)
        res = pricedata.check_vs_m1(sym, {tf: frame}, start=min(a for a, _ in spans),
                                    end=max(b for _, b in spans) + step, root=args.root)
        if res[tf]["status"] != "unverified":
            compared.add(tf)
        res = pricedata.covered_by_pricedata(res, status)
        found_lines = pricedata.describe_check(sym, res)
        lines += found_lines
        r = res.get(tf)
        if r and r["status"] == "dirty":
            bad[tf] = r["missing"].union(r["mismatched"]).union(r["extra"])
        elif found_lines:
            open_lines[tf] = found_lines
    for line in lines:
        print("WARNING:", line)
    if bad:
        hit = sum(1 for w in wins if any(((t >= w[tf][0]) & (t <= w[tf][1])).any()
                                         for tf, t in bad.items() if tf in w))
        print(f"WARNING: {hit} of {len(found)} pages show those bars; --bars m1 builds every "
              "timeframe from M1 instead")
    until = {tf: f.attrs.get("pre_clean_until") for tf, f in frames.items()}
    per_page = {}
    for s, w in zip(found, wins):
        page_lines = []
        for tf, t in bad.items():
            hits = t[(t >= w[tf][0]) & (t <= w[tf][1])] if tf in w else t[:0]
            if len(hits):
                page_lines.append(f"{sym} {tf}: {len(hits)} native bar(s) on this page disagree with M1 "
                                  f"(first {hits[0]:%Y-%m-%d %H:%M} UTC)")
        page_lines += [line for tf, ls in open_lines.items() if tf in w for line in ls]
        pre = [until[tf] for tf in w if until.get(tf) is not None and w[tf][0] < until[tf]]
        per_page[s.id] = pricedata.page_notes(
            sym, list(w), bars=args.bars, status={tf: status[tf] for tf in w if tf in status},
            check_lines=page_lines, checked=[tf for tf in w if tf in compared],
            pre_clean_until=max(pre) if pre else None)
    return [], per_page


def cmd_setup(args):
    df, extra_frames, src = _load_setup_frames(args)
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
        s.symbol = args.symbol.upper()
    found.sort(key=lambda s: s.trigger_time)

    pstr = f"Donchian({args.n})" if args.kind == "donchian" else f"MA {args.n}/{args.slow}"
    extra = []
    if args.sl_atr or args.tp_atr:
        extra.append(f"ATR SL/TP {args.sl_atr:g}/{args.tp_atr:g}")
    if args.dir:
        extra.append(args.dir)
    if args.start or args.end:
        extra.append(f"{args.start or '...'}→{args.end or '...'} UTC")
    label = pstr + ((" · " + " · ".join(extra)) if extra else "")

    notes, page_notes = _check_pages(args, df, extra_frames, found, src)
    out_dir, lib_dir = _setup_out(args.out or "setups")
    pages = setups.render_pages(df, found, out_dir, pre=args.pre, post=args.post,
                                lib_dir=lib_dir, symbol=args.symbol.upper(),
                                extra_frames=extra_frames, source=src, tz=args.tz,
                                notes=notes, page_notes=page_notes)
    cfg = {"label": label, "tf": args.timeframe, "kind": args.kind, "params": params,
           "dir": args.dir, "limit": args.limit, "start": args.start, "end": args.end,
           "tz": args.tz}
    index = setups.write_catalog(out_dir, found, cfg)
    print(f"{len(found)} setups")
    for p in pages[:3]:
        print("wrote", p)
    if len(pages) > 3:
        print(f"... {len(pages) - 3} more pages in {out_dir}")
    print("catalog", index)
    if not Path(args.out or "setups").is_absolute():
        print("gallery", chart.gallery(OUT / "charts", title="Charts", subtitle="offline pages"))


def cmd_rows(args):
    """Setup pages from another system's rows (JSON list, or {"rows": [...]})."""
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("rows", rows.get("setups"))
    if not isinstance(rows, list):
        raise SystemExit('rows file must be a JSON list of setup rows (or {"rows": [...]})')
    args.start = None
    df, extra_frames, src = _load_setup_frames(args)
    found = setups.setups_from_rows(rows, args.timeframe, clock=args.clock, symbol=args.symbol.upper())
    try:
        notes, page_notes = _check_pages(args, df, extra_frames, found, src)
    except ValueError as exc:
        if src == "ftmo" and "is outside the" in str(exc) and not args.pre_clean:
            cf = pricedata.clean_from(args.symbol, args.root)
            raise ValueError(f"{exc} FTMO bars start at priceData's clean data"
                             + (f" ({cf:%Y-%m-%d} server)" if cf is not None else "")
                             + "; --pre-clean adds the price-verified years before it, for context") from None
        raise
    out_dir, lib_dir = _setup_out(args.out)
    pages = setups.render_pages(df, found, out_dir, pre=args.pre, post=args.post,
                                lib_dir=lib_dir, symbol=args.symbol.upper(),
                                extra_frames=extra_frames, source=src, tz=args.tz,
                                notes=notes, page_notes=page_notes)
    index = setups.write_catalog(out_dir, found, {
        "label": args.label, "tf": args.timeframe, "kind": "external", "tz": args.tz})
    print(f"{len(found)} setups -> {len(pages)} pages")
    print("catalog", index)
    if not Path(args.out).is_absolute():
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

    p = sub.add_parser("backtest", help="legacy backtest demo (never quote its numbers): Dukascopy XAUUSD, "
                                        "or FTMO bars from priceData")
    p.add_argument("--source", choices=["dukascopy", "ftmo"], default="dukascopy",
                   help="dukascopy = the local Dukascopy XAUUSD parquet (bid/ask); ftmo = priceData's clean "
                        "years (BID, ask = BID + priceData's spread)")
    p.add_argument("--symbol", default="XAUUSD",
                   help="--source ftmo: EURUSD, GBPUSD, AUDUSD, NZDUSD, XAUUSD or XAGUSD")
    p.add_argument("--root", default=None, help="priceData root")
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

    src_help = ("ftmo = native FTMO bars from the priceData folder (default; 'pricedata' is the old "
                "name); dukascopy = the local Dukascopy M1 parquet given by --data (UTC); auto = read "
                "--data (parquet/CSV), identify FTMO vs Dukascopy from tag/columns/weekend fingerprint "
                "and refuse if it cannot tell. Everything is converted to true UTC internally.")
    src_choices = ["ftmo", "pricedata", "dukascopy", "auto"]
    tz_help = "display zone the page opens in (default MYT = Malaysian time, UTC+8); switchable in the page"
    clock_help = ("clock of the times in trades/zones/equity/rows: ftmo = FTMO server time (UTC+2, "
                  "+3 in US DST), utc, or myt. Required whenever any are given.")
    bars_help = ("--source ftmo: native = each timeframe's own priceData file (default); m1 = build every "
                 "timeframe from the M1 file (use it when the native files have holes)")
    check_help = ("--source ftmo, native bars: skip comparing the page's bars with the same bars built "
                  "from M1 (on by default: a hole or a wrong native bar is reported)")
    pre_clean_help = ("--source ftmo: also show the years before priceData's clean data whose PRICES are "
                      "verified (FX 2020-21, GBPUSD from 2019; spreads were placeholders) - context "
                      "only, labelled on the page. Default: clean years only (from 2022-01-01)")
    p = sub.add_parser("chart", help="render an HTML page from priceData (or legacy parquet) + overlays")
    p.add_argument("--source", choices=src_choices, default="ftmo", help=src_help)
    p.add_argument("--assume", choices=["ftmo", "dukascopy"], default=None,
                   help="with --source auto: declare the file's source when it cannot be identified")
    p.add_argument("--root", default=None, help="priceData root (default $PRICEDATA_ROOT or C:/personalCode/priceData)")
    p.add_argument("--max-bars", type=int, default=None, help="keep only the most recent N bars per timeframe")
    p.add_argument("--tz", choices=["MYT", "UTC"], default="MYT", help=tz_help)
    p.add_argument("--clock", choices=["ftmo", "utc", "myt"], default=None, help=clock_help)
    p.add_argument("--timeframe", default=None, help="default D1 (for --source auto: inferred from the file)")
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
    p.add_argument("--volume", action="store_true",
                   help="include the volume series (viewer shows the pane only when present)")
    p.add_argument("--inds", default=None, help='comma list e.g. "SMA50,EMA200,RSI14,MACD"')
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--exchange", default="")
    p.add_argument("--period-label", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--bars", choices=list(pricedata.BARS), default="native", help=bars_help)
    p.add_argument("--no-m1-check", action="store_true", help=check_help)
    p.add_argument("--pre-clean", action="store_true", help=pre_clean_help)
    p.set_defaults(func=cmd_chart)

    p = sub.add_parser("setup", help="generate a setup catalog + annotated pages (built-in Donchian / MA-cross finders)")
    p.add_argument("--source", choices=src_choices, default="ftmo", help=src_help)
    p.add_argument("--assume", choices=["ftmo", "dukascopy"], default=None,
                   help="with --source auto: declare the file's source when it cannot be identified")
    p.add_argument("--root", default=None, help="priceData root")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--extra-tfs", nargs="*", default=None, help="more timeframe buttons on each page")
    p.add_argument("--tz", choices=["MYT", "UTC"], default="MYT", help=tz_help)
    p.add_argument("--data", default=str(DATA / "parquet" / "XAUUSD_M1.parquet"),
                   help="Dukascopy M1 parquet, or the file for --source auto")
    p.add_argument("--kind", default="donchian", choices=["donchian", "macross"])
    p.add_argument("--timeframe", default=None, help="default D1 (for --source auto: inferred from the file)")
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
    p.add_argument("--out", default=None)
    p.add_argument("--bars", choices=list(pricedata.BARS), default="native", help=bars_help)
    p.add_argument("--no-m1-check", action="store_true", help=check_help)
    p.add_argument("--pre-clean", action="store_true", help=pre_clean_help)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("rows", help="setup pages from another system's rows (JSON file)")
    p.add_argument("--rows", required=True, help="JSON list of setup rows; see setups.setups_from_rows")
    p.add_argument("--source", choices=src_choices, default="ftmo", help=src_help)
    p.add_argument("--assume", choices=["ftmo", "dukascopy"], default=None,
                   help="with --source auto: declare the file's source when it cannot be identified")
    p.add_argument("--root", default=None, help="priceData root")
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True, help="timeframe the rows were generated on")
    p.add_argument("--extra-tfs", nargs="*", default=None, help="more timeframe buttons on each page")
    p.add_argument("--end", default=None)
    p.add_argument("--pre", type=int, default=60)
    p.add_argument("--post", type=int, default=45)
    p.add_argument("--label", default="external setups")
    p.add_argument("--tz", choices=["MYT", "UTC"], default="MYT", help=tz_help)
    p.add_argument("--clock", choices=["ftmo", "utc", "myt"], required=True, help=clock_help)
    p.add_argument("--data", default=str(DATA / "parquet" / "XAUUSD_M1.parquet"),
                   help="Dukascopy M1 parquet, or the file for --source auto")
    p.add_argument("--out", required=True, help="folder name under out/charts, or an absolute folder")
    p.add_argument("--bars", choices=list(pricedata.BARS), default="native", help=bars_help)
    p.add_argument("--no-m1-check", action="store_true", help=check_help)
    p.add_argument("--pre-clean", action="store_true", help=pre_clean_help)
    p.set_defaults(func=cmd_rows)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (sources.SourceError, sources.ClockError) as exc:
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
