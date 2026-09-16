from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import chart, export
from . import strategies as strat

DIR_COLORS = {"long": "#26a69a", "short": "#ef5350"}


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
    zone: dict = field(default_factory=dict)
    label: str = ""
    params: dict = field(default_factory=dict)

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
            entry_price=round(entry, 4),
            stop=round(s, 4) if s else None,
            target=round(e, 4) if e else None,
            atr=round(atrv, 4) if atrv else None,
            zone={"s": _epoch(pd.Timestamp(times[i - n])), "e": _epoch(t),
                  "lo": round(float(lo), 4), "hi": round(float(up), 4),
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
            entry_price=round(entry, 4),
            stop=round(sl, 4) if sl else None,
            target=round(tp, 4) if tp else None,
            atr=round(atrv, 4),
            zone={"s": _epoch(pd.Timestamp(times[i - slow])), "e": _epoch(t),
                  "lo": round(lo, 4), "hi": round(hi, 4),
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
    kw = dict(params)
    kw.update({"limit": opts.get("limit"), "start": opts.get("start"),
               "end": opts.get("end"), "dir": opts.get("dir")})
    return FINDERS[kind](df, **kw)


def _bar_index(times: pd.DatetimeIndex, sec: int) -> int:
    epochs = np.asarray(times.asi8) // 1_000_000_000
    idx = int(np.searchsorted(epochs, sec, side="left"))
    if idx >= len(epochs):
        return len(epochs) - 1
    if idx > 0 and epochs[idx] != sec:
        idx -= 1
    return idx


def setup_indicators(sub: pd.DataFrame, setup: Setup) -> dict:
    tf = setup.tf
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


def slice_spec(df: pd.DataFrame, setup: Setup, pre_bars: int = 60,
               post_bars: int = 45) -> dict:
    times = pd.DatetimeIndex(df.index)
    pos = _bar_index(times, setup.trigger_time)
    sub = df.iloc[max(0, pos - pre_bars):min(len(times), pos + post_bars + 1)]
    tf = setup.tf
    return chart.spec(
        "XAUUSD", {tf: export.bars_block(sub)}, exchange="setup",
        period_label=tf, default_tf=tf,
        trades=[{"dir": setup.dir, "entryTime": setup.entry_time,
                 "entryPrice": setup.entry_price, "lots": 0.5,
                 "sl": setup.stop, "tp": setup.target}],
        zones=[setup.zone],
        indicators=setup_indicators(sub, setup))


def write_catalog(out_dir: Path, setups: list[Setup], cfg: dict) -> Path:
    cells = []
    for s in setups:
        color = DIR_COLORS[s.dir]
        target = f"{s.target:.3f}" if s.target else "—"
        stop = f"{s.stop:.3f}" if s.stop else "—"
        rr = f"{s.rr()}R" if s.rr() else "—"
        cells.append(
            "<div class='card'>"
            f"<div class='row'><span class='tf'>{s.tf}</span>"
            f"<span class='dir' style='color:{color}'>● {s.dir.title()}</span>"
            f"<span class='lab'>{s.label}</span></div>"
            f"<div class='t'>{chart.to_iso(s.trigger_time)} UTC</div>"
            "<table><tr><th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th></tr>"
            f"<tr><td>{s.entry_price:.3f}</td><td>{stop}</td><td>{target}</td><td>{rr}</td></tr></table>"
            f"<a href='{s.id}.html'>Open chart →</a></div>")
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
            f"<h2>{cfg.get('label','')} · {cfg.get('tf','')} · real historical bars</h2>"
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
                 lib_dir: str = "../../lib") -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for s in setups:
        page = slice_spec(df, s, pre_bars=pre, post_bars=post)
        title = f"{s.label} · {s.tf} {chart.to_iso(s.trigger_time)} UTC"
        written.append(chart.render(page, out_dir / f"{s.id}.html",
                                    title=title, lib_dir=lib_dir))
    return written
