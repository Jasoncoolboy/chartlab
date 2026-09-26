from __future__ import annotations

import html as _html
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import chart, export, sources
from . import strategies as strat

DIR_COLORS = {"long": "#26a69a", "short": "#ef5350"}
# Extra-timeframe views: how many of THEIR OWN bars a coarse view is widened to
# (leftwards only), and the most bars any one extra view may carry.
MIN_EXTRA_BARS = 40
MAX_EXTRA_BARS = 5000
# Decimals kept on setup prices: strips float noise only. 4 would round a 5-digit FX
# entry to the nearest whole pip, so the drawn entry would miss the bar's open.
DIGITS = 8


@dataclass
class Setup:
    id: str
    tf: str
    kind: str
    dir: str
    trigger_time: int
    entry_time: int
    entry_price: float
    stop: float | None = None
    target: float | None = None
    atr: float | None = None
    zone: dict | list = field(default_factory=dict)
    label: str = ""
    params: dict = field(default_factory=dict)
    symbol: str = ""
    lots: float | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    net: float | None = None
    reason: str = ""
    net_unit: str | None = None        # unit of ``net``: "$" (default), "R" or "pips"

    def rr(self) -> float | None:
        if not self.stop or not self.target:
            return None
        risk = abs(self.entry_price - self.stop)
        if risk <= 0:
            return None
        return round(abs(self.target - self.entry_price) / risk, 2)


def _epoch(value) -> int:
    return chart.to_epoch(pd.Timestamp(value))


def _passes(t, start, end, direction, want_dir) -> bool:
    if want_dir and direction != want_dir:
        return False
    if start and t < start:
        return False
    if end and t >= end:
        return False
    return True


def find_donchian(df: pd.DataFrame, n: int = 20, sl_atr: float = 2.0,
                  tp_atr: float = 2.0, allow_short: bool = True,
                  limit: int | None = None, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None,
                  dir: str | None = None) -> list[Setup]:
    high = df["bid_high"].to_numpy(dtype=float)
    low = df["bid_low"].to_numpy(dtype=float)
    open_ = df["bid_open"].to_numpy(dtype=float)
    times = df.index.to_numpy()
    atr = strat.atr_wilder(df).to_numpy(dtype=float)
    dirs = strat.donchian_direction(df, n, allow_short)
    out: list[Setup] = []
    for i in range(n, len(df) - 1):
        direction = int(dirs[i])
        if direction == 0:
            continue
        d = "long" if direction == 1 else "short"
        t = pd.Timestamp(times[i])
        if not _passes(t, start, end, d, dir):
            continue
        up = high[i - n:i].max()
        lo = low[i - n:i].min()
        entry = float(open_[i + 1])
        atrv = float(atr[i]) if i < len(atr) else 0.0
        s = entry - direction * atrv * sl_atr if sl_atr else None
        e = entry + direction * atrv * tp_atr if tp_atr else None
        out.append(Setup(
            id=f"donch{n}-{t:%Y%m%d%H%M}-{'L' if d == 'long' else 'S'}",
            tf="", kind="donchian", dir=d,
            trigger_time=_epoch(t), entry_time=_epoch(pd.Timestamp(times[i + 1])),
            entry_price=round(entry, DIGITS),
            stop=round(s, DIGITS) if s else None,
            target=round(e, DIGITS) if e else None,
            atr=round(atrv, DIGITS) if atrv else None,
            zone={"s": _epoch(pd.Timestamp(times[i - n])), "e": _epoch(t),
                  "lo": round(float(lo), DIGITS), "hi": round(float(up), DIGITS),
                  "label": f"{d.title()} {n}B breakout", "color": DIR_COLORS[d]},
            label=f"Donchian({n}) {d.title()}",
            params={"n": n, "sl_atr": sl_atr, "tp_atr": tp_atr}))
        if limit and len(out) >= limit:
            break
    return out


def find_macross(df: pd.DataFrame, fast: int = 20, slow: int = 60,
                 sl_atr: float = 2.0, tp_atr: float = 2.0,
                 allow_short: bool = True, limit: int | None = None,
                 start: pd.Timestamp | None = None,
                 end: pd.Timestamp | None = None,
                 dir: str | None = None) -> list[Setup]:
    c = df["bid_close"].to_numpy(dtype=float)
    open_ = df["bid_open"].to_numpy(dtype=float)
    times = df.index.to_numpy()
    atr = strat.atr_wilder(df).to_numpy(dtype=float)
    dirs = strat.ma_cross_direction(df, fast, slow, allow_short)
    out: list[Setup] = []
    for i in range(slow + 1, len(df) - 1):
        direction = int(dirs[i])
        if direction == 0:
            continue
        d = "long" if direction == 1 else "short"
        t = pd.Timestamp(times[i])
        if not _passes(t, start, end, d, dir):
            continue
        entry = float(open_[i + 1])
        atrv = float(atr[i])
        sl = entry - direction * atrv * sl_atr if sl_atr else None
        tp = entry + direction * atrv * tp_atr if tp_atr else None
        lo = float(np.min(c[i - slow:i + 1]))
        hi = float(np.max(c[i - slow:i + 1]))
        out.append(Setup(
            id=f"mac{fast}-{slow}-{t:%Y%m%d%H%M}-{'L' if d == 'long' else 'S'}",
            tf="", kind="macross", dir=d,
            trigger_time=_epoch(t), entry_time=_epoch(pd.Timestamp(times[i + 1])),
            entry_price=round(entry, DIGITS),
            stop=round(sl, DIGITS) if sl else None,
            target=round(tp, DIGITS) if tp else None,
            atr=round(atrv, DIGITS),
            zone={"s": _epoch(pd.Timestamp(times[i - slow])), "e": _epoch(t),
                  "lo": round(lo, DIGITS), "hi": round(hi, DIGITS),
                  "label": f"{d.title()} MA{fast}/{slow} cross", "color": DIR_COLORS[d]},
            label=f"MA{fast}/{slow} {d.title()}",
            params={"fast": fast, "slow": slow, "sl_atr": sl_atr, "tp_atr": tp_atr}))
        if limit and len(out) >= limit:
            break
    return out


FINDERS = {"donchian": find_donchian, "macross": find_macross}


def find_all(df: pd.DataFrame, kind: str, params: dict, opts: dict) -> list[Setup]:
    if kind not in FINDERS:
        raise ValueError(f"unknown setup kind: {kind}")
    df = export.to_bid_frame(df)
    kw = dict(params)
    kw.update({"limit": opts.get("limit"), "start": opts.get("start"),
               "end": opts.get("end"), "dir": opts.get("dir")})
    return FINDERS[kind](df, **kw)


def _epoch_array(times) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(times).as_unit("ns").asi8) // 1_000_000_000


def _bar_index(times: pd.DatetimeIndex, sec: int) -> int:
    """Index of the bar CONTAINING ``sec`` (the last bar opening at or before it)."""
    epochs = _epoch_array(times)
    idx = int(np.searchsorted(epochs, sec, side="left"))
    if idx >= len(epochs):
        return len(epochs) - 1
    if idx > 0 and epochs[idx] != sec:
        idx -= 1
    return idx


def _check_in_range(times: pd.DatetimeIndex, setup: Setup) -> None:
    """Refuse a setup whose trigger is outside the bars, instead of clamping.

    ``_bar_index`` clamps to the last bar, which would silently draw the wrong
    window for a setup in the wrong clock (real UTC vs FTMO server time), the
    wrong symbol or the wrong timeframe.
    """
    epochs = _epoch_array(times)
    step = int(np.median(np.diff(epochs))) if len(epochs) > 1 else 86400
    if setup.trigger_time < epochs[0] or setup.trigger_time > epochs[-1] + step:
        raise ValueError(
            f"setup {setup.id}: trigger {chart.to_iso(setup.trigger_time)} is outside the "
            f"{setup.tf} bars ({chart.to_iso(int(epochs[0]))} .. {chart.to_iso(int(epochs[-1]))}). "
            "Wrong clock (setup times must be UTC - pass clock='ftmo' if they are FTMO server "
            "time), wrong symbol, or wrong timeframe?")


def _zones_of(setup: Setup) -> list:
    z = setup.zone
    if not z:
        return []
    return list(z) if isinstance(z, (list, tuple)) else [z]


def setup_indicators(sub: pd.DataFrame, setup: Setup) -> dict:
    """Indicators drawn on a setup page; only the built-in kinds define any."""
    tf = setup.tf
    if setup.kind not in FINDERS:
        return {}
    if setup.kind == "donchian":
        n = int(setup.params.get("n", 20))
        up = sub["bid_high"].rolling(n).max().shift(1).to_numpy(dtype=float)
        lo = sub["bid_low"].rolling(n).min().shift(1).to_numpy(dtype=float)
        return {tf: [export.indicator(f"DC{n} upper", up, "#e5a93d"),
                     export.indicator(f"DC{n} lower", lo, "#4fc3f7")]}
    fast = int(setup.params.get("fast", 20))
    slow = int(setup.params.get("slow", 60))
    cf = sub["bid_close"].rolling(fast).mean().to_numpy(dtype=float)
    cs = sub["bid_close"].rolling(slow).mean().to_numpy(dtype=float)
    return {tf: [export.indicator(f"SMA{fast}", cf, "#4fc3f7", width=2),
                 export.indicator(f"SMA{slow}", cs, "#f4511e")]}


def _slice_extra(edf: pd.DataFrame, lo: int, hi_end: int) -> pd.DataFrame | None:
    """The bars of another timeframe covering the primary window ``[lo, hi_end)``.

    The left edge is the bar CONTAINING ``lo`` (so a coarser view is not a bar
    short) and is widened to ``MIN_EXTRA_BARS`` of the view's own bars. The
    right edge is never extended past the primary window. Returns None when the
    view would carry more than ``MAX_EXTRA_BARS`` bars.
    """
    ex = _epoch_array(edf.index)
    i1 = int(np.searchsorted(ex, hi_end, side="left"))
    i0 = _bar_index(edf.index, lo)
    i0 = max(0, min(i0, i1 - MIN_EXTRA_BARS))
    if i1 <= i0 or i1 - i0 > MAX_EXTRA_BARS:
        return None
    return edf.iloc[i0:i1]


def slice_spec(df: pd.DataFrame, setup: Setup, pre_bars: int = 60,
               post_bars: int = 45, *, symbol: str | None = None,
               extra_frames: dict | None = None, exchange: str = "setup",
               source: str | None = None, tz: str | None = None) -> dict:
    """One setup's page: bars around its trigger, its trade, zone(s) and indicators.

    ``df`` is the setup's own timeframe in UTC (any OHLC column style) and the
    setup's times are UTC epochs. ``extra_frames`` ({tf: frame}, UTC) adds
    timeframe buttons; views that would be too large are skipped. The symbol
    comes from the setup, else ``symbol``. ``tz`` is the viewer's initial
    display zone (UTC or MYT, default MYT).
    """
    df = export.to_bid_frame(df)
    times = pd.DatetimeIndex(df.index)
    _check_in_range(times, setup)
    pos = _bar_index(times, setup.trigger_time)
    sub = df.iloc[max(0, pos - pre_bars):min(len(times), pos + post_bars + 1)]
    tf = setup.tf
    blocks = {tf: export.bars_block(sub)}
    if extra_frames:
        sub_t = _epoch_array(sub.index)
        step = int(np.median(np.diff(sub_t))) if len(sub_t) > 1 else 86400
        for etf, edf in extra_frames.items():
            if etf == tf:
                continue
            esub = _slice_extra(export.to_bid_frame(edf), int(sub_t[0]), int(sub_t[-1]) + step)
            if esub is not None:
                blocks[etf] = export.bars_block(esub)
    trade = {"dir": setup.dir, "entryTime": setup.entry_time,
             "entryPrice": setup.entry_price, "sl": setup.stop, "tp": setup.target}
    if setup.lots:
        trade["lots"] = setup.lots
    if setup.exit_time is not None:
        trade.update(exitTime=setup.exit_time, exitPrice=setup.exit_price,
                     net=setup.net, reason=setup.reason or None)
    if setup.net is not None and setup.net_unit:
        trade["netUnit"] = setup.net_unit
    sub_t = _epoch_array(sub.index)
    view = (int(sub_t[0]), int(sub_t[-1])) if len(sub_t) > 1 else None
    return chart.spec(
        setup.symbol or symbol or "Chart", blocks, exchange=exchange, source=source,
        tz=tz, period_label=tf, default_tf=tf,
        trades=[trade], zones=_zones_of(setup),
        indicators=setup_indicators(sub, setup), view=view)


def write_catalog(out_dir: Path, setups: list[Setup], cfg: dict) -> Path:
    tz = cfg.get("tz") or chart.DEFAULT_TZ
    esc = lambda v: _html.escape(str(v))
    cells = []
    for s in setups:
        color = DIR_COLORS[s.dir]
        # Show as many decimals as the prices carry (5-digit FX needs 5).
        prec = chart.infer_precision([s.entry_price, s.stop, s.target])
        fmt = lambda v: f"{v:.{prec}f}" if v else "—"
        rr = f"{s.rr()}R" if s.rr() else "—"
        sym = f"{esc(s.symbol)} · " if s.symbol else ""
        cells.append(
            "<div class='card'>"
            f"<div class='row'><span class='tf'>{esc(s.tf)}</span>"
            f"<span class='dir' style='color:{color}'>● {s.dir.title()}</span>"
            f"<span class='lab'>{esc(s.label)}</span></div>"
            f"<div class='t'>{sym}{chart.to_iso(s.trigger_time, tz, True)}</div>"
            "<table><tr><th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th></tr>"
            f"<tr><td>{fmt(s.entry_price)}</td><td>{fmt(s.stop)}</td><td>{fmt(s.target)}</td><td>{rr}</td></tr></table>"
            f"<a href='{esc(s.id)}.html'>Open chart →</a></div>")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Setup catalog</title><style>"
            "body{background:#0e1117;color:#d1d4dc;font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:1100px;margin:0 auto}"
            "h1{font-size:20px;color:#fff;font-weight:600}"
            "h2{font-size:13px;color:#787b86;font-weight:400;margin:4px 0 18px}"
            ".cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}"
            ".card{background:#131722;border:1px solid #2a2e39;border-radius:8px;padding:12px 14px}"
            ".row{display:flex;gap:8px;align-items:center;margin-bottom:6px}"
            ".tf{background:#2962ff;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;font-weight:600}"
            ".lab{color:#d1d4dc;margin-left:auto;font-size:12px}"
            ".t{color:#787b86;font-size:11px;margin-bottom:8px}"
            "table{width:100%;border-collapse:collapse;margin-bottom:8px}"
            "th{color:#787b86;font-weight:500;text-align:left;font-size:11px;padding:2px 0}"
            "td{font-variant-numeric:tabular-nums;padding:1px 0}"
            "a{color:#2962ff;text-decoration:none;font-size:12px}"
            "</style></head><body>"
            "<h1>Setup catalog</h1>"
            f"<h2>{esc(cfg.get('label',''))} · {esc(cfg.get('tf',''))} · real historical bars · times in {esc(tz)}</h2>"
            f"<div style='color:#787b86;font-size:12px;margin-bottom:16px'>{len(setups)} setups</div>"
            f"<div class='cards'>{''.join(cells)}</div></body></html>")
    Path(out_dir / "index.html").write_text(html, encoding="utf-8")
    rows = [{k: getattr(s, k) for k in
             ("id", "tf", "kind", "dir", "trigger_time", "entry_time",
              "entry_price", "stop", "target", "label")} for s in setups]
    Path(out_dir / "_setups.json").write_text(
        json.dumps({"cfg": cfg, "rows": rows}, indent=2, default=str))
    return out_dir / "index.html"


def render_pages(df: pd.DataFrame, setups: list[Setup], out_dir: Path,
                 pre: int = 60, post: int = 45,
                 lib_dir: str = "../../lib", *, symbol: str | None = None,
                 extra_frames: dict | None = None, source: str | None = None,
                 tz: str | None = None) -> list[Path]:
    """Write one page per setup (frames and setup times in UTC). ``tz`` (UTC or
    MYT, default MYT) is the display zone used in titles and the viewer's start."""
    tz = tz or chart.DEFAULT_TZ
    ids = [s.id for s in setups]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate setup ids would overwrite each other's pages: {dupes[:5]}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = export.to_bid_frame(df)
    extras = {k.upper(): export.to_bid_frame(v) for k, v in (extra_frames or {}).items()}
    written = []
    for s in setups:
        page = slice_spec(df, s, pre_bars=pre, post_bars=post, symbol=symbol,
                          extra_frames=extras, source=source, tz=tz)
        title = f"{s.label} · {s.tf} {chart.to_iso(s.trigger_time, tz, True)}"
        written.append(chart.render(page, out_dir / f"{s.id}.html",
                                    title=title, lib_dir=lib_dir))
    return written


# --------------------------------------------------------------------------
# setups from ANOTHER system's rows
# --------------------------------------------------------------------------
_DIRS = {"1": "long", "buy": "long", "long": "long", "l": "long",
         "-1": "short", "sell": "short", "short": "short", "s": "short"}


def _pick(row: dict, *names):
    for n in names:
        if row.get(n) is not None and row.get(n) != "":
            return row[n]
    return None


def _num(i: int, name: str, value, required: bool = False):
    if value is None:
        if required:
            raise ValueError(f"row {i}: missing required {name!r}")
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"row {i}: {name!r} is not a number: {value!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"row {i}: {name!r} is not finite: {value!r}")
    return v


def _net_unit(i: int, value):
    if value is None:
        return None
    try:
        return chart.norm_net_unit(value)
    except ValueError as exc:
        raise ValueError(f"row {i}: {exc}") from None


def setups_from_rows(rows, tf: str, *, clock: str | None = None, symbol: str = "",
                     kind: str = "external") -> list[Setup]:
    """Turn another system's setup rows into ``Setup`` objects for ``render_pages``.

    Each row is a dict. Required: a direction (``dir``/``direction``/``side``:
    long|short|1|-1|buy|sell), ``entry_time`` (or ``fill``) and ``entry_price``
    (or ``entry``). Optional: ``trigger_time`` (or ``arm``; default the entry
    time), ``stop``/``sl``, ``target``/``tp``, ``zone`` (a zone dict or list of
    them: start/end/low/high[/label/color]), ``label``, ``id``, ``symbol``,
    ``lots``, ``exit_time``, ``exit_price``, ``net``, ``net_unit`` (the unit of
    ``net``: ``"$"`` default, ``"R"`` or ``"pips"``), ``reason``.

    Times are epoch seconds, ISO strings or datetimes and ``clock`` is REQUIRED:
    it names the clock of every bare (zone-less) time in the rows - ``'ftmo'``
    (FTMO server time, converted with the US-DST rule), ``'utc'`` or ``'myt'``.
    A time that carries its own zone (``...Z``, ``+08:00``) is absolute. Setups
    are stored in UTC; ``render_pages`` refuses a trigger outside the bars rather
    than draw the wrong window. A stop or target on the wrong side of the entry
    raises.
    """
    clock = sources._need_clock(clock, "setup rows")
    out, seen = [], set()
    for i, raw in enumerate(rows):
        r = dict(raw)
        d = _DIRS.get(str(_pick(r, "dir", "direction", "side")).strip().lower())
        if d is None:
            raise ValueError(f"row {i}: 'dir' must be long/short (or 1/-1, buy/sell); "
                             f"got {_pick(r, 'dir', 'direction', 'side')!r}")
        et = _pick(r, "entry_time", "entryTime", "fill")
        if et is None:
            raise ValueError(f"row {i}: missing required 'entry_time'")
        entry_time = sources.to_utc_epoch(et, clock)
        trig = _pick(r, "trigger_time", "triggerTime", "arm", "time")
        trigger = sources.to_utc_epoch(trig, clock) if trig is not None else entry_time
        entry = _num(i, "entry_price", _pick(r, "entry_price", "entryPrice", "entry", "price"), True)
        stop = _num(i, "stop", _pick(r, "stop", "sl"))
        target = _num(i, "target", _pick(r, "target", "tp"))
        sign = 1 if d == "long" else -1
        if stop is not None and (entry - stop) * sign <= 0:
            raise ValueError(f"row {i}: a {d} stop must be on the losing side of the entry "
                             f"(entry {entry}, stop {stop})")
        if target is not None and (target - entry) * sign <= 0:
            raise ValueError(f"row {i}: a {d} target must be on the winning side of the entry "
                             f"(entry {entry}, target {target})")
        zone = _pick(r, "zone", "zones")
        zones = []
        if zone:
            zrows = zone if isinstance(zone, (list, tuple)) else [zone]
            zones = chart.zones_from_rows(sources.convert_overlay(zones=zrows, clock=clock)[1])
        xt = _pick(r, "exit_time", "exitTime")
        sid = str(_pick(r, "id", "ref") or f"{kind}-{i:04d}-{chart.to_iso(trigger).replace(' ', '_').replace(':', '').replace('-', '')}-{d[0].upper()}")
        sid = re.sub(r"[^A-Za-z0-9._-]+", "_", sid)
        if sid in seen:
            raise ValueError(f"row {i}: duplicate setup id {sid!r}")
        seen.add(sid)
        lots = _num(i, "lots", _pick(r, "lots", "size"))
        out.append(Setup(
            id=sid, tf=tf, kind=kind, dir=d, trigger_time=trigger, entry_time=entry_time,
            entry_price=entry, stop=stop, target=target, zone=zones,
            label=str(_pick(r, "label", "name") or f"{kind} {d}"),
            symbol=str(_pick(r, "symbol") or symbol), lots=lots,
            exit_time=sources.to_utc_epoch(xt, clock) if xt is not None else None,
            exit_price=_num(i, "exit_price", _pick(r, "exit_price", "exitPrice")),
            net=_num(i, "net", _pick(r, "net")), reason=str(_pick(r, "reason") or ""),
            net_unit=_net_unit(i, _pick(r, "net_unit", "netUnit"))))
    return out
