"""ChartLab standalone chart generator.

Reads a JSON chart spec (bars + trades + zones + equity + indicators) and
writes an offline, TradingView-style interactive HTML page.

Standard library only. The only external files needed are the viewer
template and the vendored lightweight-charts build, both beside this module
in ``assets/`` (overridable via ``viewer=``/``lib=``).

Copy ``chart.py`` plus ``assets/`` into any project to use it there.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import shutil
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
VIEWER_NAME = "viewer.html"
LIB_NAME = "lightweight-charts.standalone.production.js"
SPEC_VERSION = 1

_TF_COMPACT = {"t": "time", "o": "open", "h": "high", "l": "low",
               "c": "close", "v": "volume"}


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
def to_epoch(value) -> int:
    """Convert an epoch number, ISO string, or datetime to epoch seconds UTC."""
    if isinstance(value, bool):
        raise TypeError("boolean is not a timestamp")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if any(ch in s for ch in "-T:") or "/" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            return int(float(s))
    else:
        raise TypeError(f"cannot parse timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def to_iso(seconds) -> str:
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# normalization helpers
# --------------------------------------------------------------------------
def _num_list(values, key):
    out = []
    for v in values:
        if v is None or v == "":
            out.append(None)
        else:
            out.append(float(v))
    return out


def _bars_from_block(block: dict) -> dict:
    src = {}
    for k, v in block.items():
        src[_TF_COMPACT.get(k, k)] = v
    missing = [k for k in ("time", "open", "high", "low", "close") if k not in src]
    if missing:
        raise ValueError(f"bar block missing keys: {missing}")
    out = {
        "time": [to_epoch(x) for x in src["time"]],
        "open": _num_list(src["open"], "open"),
        "high": _num_list(src["high"], "high"),
        "low": _num_list(src["low"], "low"),
        "close": _num_list(src["close"], "close"),
    }
    if src.get("volume") is not None:
        out["volume"] = [None if v is None else float(v) for v in src["volume"]]
    return out


def bars_from_rows(rows, time="time", open="open", high="high",
                   low="low", close="close", volume=None) -> dict:
    """Build a bar block from dict rows (by column name) or row sequences.

    Dict rows are read by the given column names; sequences are read
    positionally as ``(time, open, high, low, close[, volume])``.
    """
    keys = {"time": time, "open": open, "high": high, "low": low, "close": close}
    has_vol = volume is not None
    out = {"time": [], "open": [], "high": [], "low": [], "close": []}
    if has_vol:
        out["volume"] = []
    positional = ["time", "open", "high", "low", "close"] + (["volume"] if has_vol else [])
    for row in rows:
        if isinstance(row, dict):
            out["time"].append(to_epoch(row.get(keys["time"])))
            for name in ("open", "high", "low", "close"):
                out[name].append(float(row.get(keys[name])))
            if has_vol:
                v = row.get(volume)
                out["volume"].append(None if v in (None, "") else float(v))
        else:
            for j, name in enumerate(positional):
                v = row[j]
                if name == "time":
                    out["time"].append(to_epoch(v))
                elif name == "volume":
                    out["volume"].append(None if v in (None, "") else float(v))
                else:
                    out[name].append(float(v))
    return out


def _norm_trade(t: dict) -> dict:
    def pick(*names, default=None):
        for n in names:
            if n in t and t[n] is not None:
                return t[n]
        return default
    d = str(pick("dir", "direction", "side", default="long")).lower()
    if d in ("1", "buy", "l", "long"):
        d = "long"
    elif d in ("-1", "sell", "s", "short"):
        d = "short"
    out = {
        "dir": d,
        "entryTime": to_epoch(pick("entryTime", "entry_time", "time")),
        "entryPrice": float(pick("entryPrice", "entry_price", "price")),
    }
    lots = pick("lots", "size", "qty", "quantity")
    if lots is not None:
        out["lots"] = float(lots)
    for src, dst in (("exitTime", "exitTime"), ("exit_time", "exitTime"),
                     ("exitPrice", "exitPrice"), ("exit_price", "exitPrice"),
                     ("net", "net"), ("reason", "reason"),
                     ("sl", "sl"), ("stop", "sl"), ("tp", "tp"), ("target", "tp")):
        val = t.get(src)
        if val is None or val == "":
            continue
        if dst in out:
            continue
        out[dst] = to_epoch(val) if dst.endswith("Time") else (
            float(val) if dst in ("exitPrice", "net", "sl", "tp") else str(val))
    return out


def trades_from_rows(rows) -> list:
    return [_norm_trade(dict(r)) for r in rows]


def _norm_zone(z: dict) -> dict:
    def pick(*names, default=None):
        for n in names:
            if n in z and z[n] is not None:
                return z[n]
        return default
    out = {
        "start": to_epoch(pick("start", "s", "from")),
        "end": to_epoch(pick("end", "e", "to")),
        "low": float(pick("low", "lo", "bottom")),
        "high": float(pick("high", "hi", "top")),
    }
    label = pick("label", "name")
    if label:
        out["label"] = str(label)
    if z.get("color"):
        out["color"] = str(z["color"])
    return out


def zones_from_rows(rows) -> list:
    return [_norm_zone(dict(z)) for z in rows]


def _norm_indicator(ind: dict) -> dict:
    out = {}
    for k in ("name", "color", "values", "type", "width"):
        if ind.get(k) is not None:
            out[k] = ind[k]
    if "period" in ind and ind["period"] is not None:
        out["period"] = int(ind["period"])
    elif ind.get("n") is not None:
        out["period"] = int(ind["n"])
    if ind.get("k") is not None and "type" not in out:
        out["type"] = str(ind["k"]).lower()
    if ind.get("col") is not None and "color" not in out:
        out["color"] = ind["col"]
    if ind.get("on") is False:
        out["on"] = False
    return out


def indicators_from_rows(rows) -> list:
    return [_norm_indicator(dict(i)) for i in rows]


_TONES = {"up": "up", "down": "down", "pos": "up", "positive": "up",
          "good": "up", "neg": "down", "negative": "down", "bad": "down"}


def _norm_stat(row) -> dict:
    if isinstance(row, dict):
        label = row.get("label", row.get("name", row.get("key")))
        value = row.get("value", row.get("v", row.get("val")))
        tone = row.get("tone")
    else:
        row = list(row)
        label = row[0] if len(row) > 0 else None
        value = row[1] if len(row) > 1 else None
        tone = row[2] if len(row) > 2 else None
    if label is None:
        raise ValueError(f"stat has no label: {row!r}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    out = {"label": str(label), "value": value if value is not None else "—"}
    tone = _TONES.get(str(tone).lower()) if tone is not None else None
    if tone:
        out["tone"] = tone
    return out


def stats_from_rows(rows) -> list:
    """Build the metrics panel from a list of rows or a ``{label: value}`` dict.

    Each row is ``{label, value[, tone]}`` (aliases ``name``/``key`` and
    ``v``/``val`` are accepted) or the sequence ``[label, value[, tone]]``.
    """
    if isinstance(rows, dict):
        return [_norm_stat({"name": k, "value": v}) for k, v in rows.items()]
    return [_norm_stat(r) for r in rows]


# --------------------------------------------------------------------------
# compact (base64) payload encoding — opt-in, ~3x smaller than readable JSON
# --------------------------------------------------------------------------
_PRICE_SCALE = 10000


def _b64_ints(values, signed: bool) -> str:
    fmt = "<%d%s" % (len(values), "i" if signed else "I")
    return base64.b64encode(struct.pack(fmt, *values)).decode("ascii")


def encode_block(block: dict) -> dict:
    """Encode a canonical bar block as base64 int arrays.

    Times stay absolute epoch seconds (uint32) and prices are scaled by 1e4
    (int32); the viewer's ``decodeBlock`` reverses this exactly for four
    decimal places, which is plenty for FX/metals. Volume, when present, is
    left as-is.
    """
    times = [int(t) for t in block["time"]]
    out = {"t": _b64_ints(times, False)}
    for key, short in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")):
        out[short] = _b64_ints(
            [int(round(float(v) * _PRICE_SCALE)) for v in block[key]], True)
    if block.get("volume"):
        out["v"] = block["volume"]
    return out


def encode_timeframes(blocks: dict) -> dict:
    return {tf: encode_block(b) for tf, b in blocks.items()}


# --------------------------------------------------------------------------
# spec building
# --------------------------------------------------------------------------
def spec(symbol, timeframes: dict, *, exchange: str = "", period_label: str = "",
         default_tf: str | None = None, trades=None, zones=None, equity=None,
         indicators=None, stats=None, title: str | None = None) -> dict:
    """Assemble and validate a chart spec from flexible inputs."""
    if not timeframes:
        raise ValueError("timeframes must not be empty")
    blocks = {}
    for tf, block in timeframes.items():
        if isinstance(block, dict):
            blocks[tf] = _bars_from_block(block)
        else:
            blocks[tf] = bars_from_rows(block)
    if default_tf is None:
        default_tf = max(blocks, key=lambda k: len(blocks[k]["time"]))
    elif default_tf not in blocks:
        raise ValueError(f"default_tf {default_tf!r} not in timeframes {list(blocks)}")

    ind_map = {}
    if indicators:
        if isinstance(indicators, list):
            ind_map = {default_tf: indicators_from_rows(indicators)}
        else:
            ind_map = {tf: indicators_from_rows(vals)
                       for tf, vals in indicators.items()}

    eq = None
    if equity:
        eq = _norm_equity(equity)

    out = {
        "version": SPEC_VERSION,
        "symbol": str(symbol),
        "exchange": str(exchange),
        "periodLabel": str(period_label),
        "defaultTimeframe": default_tf,
        "timeframes": blocks,
        "overlay": {
            "trades": trades_from_rows(trades) if trades else [],
            "zones": zones_from_rows(zones) if zones else [],
        },
        "equity": eq,
        "indicators": ind_map,
        "stats": stats_from_rows(stats) if stats else [],
    }
    if title is not None:
        out["title"] = str(title)
    return out


def _norm_equity(eq) -> dict:
    if isinstance(eq, dict):
        times = eq.get("time", eq.get("t"))
        vals = eq.get("value", eq.get("v"))
        label = eq.get("label", "Equity")
    else:
        rows = list(eq)
        times = [r["time"] if isinstance(r, dict) else r[0] for r in rows]
        vals = [r["value"] if isinstance(r, dict) else r[1] for r in rows]
        label = "Equity"
    return {
        "time": [to_epoch(x) for x in times],
        "value": [float(v) for v in vals],
        "label": str(label),
    }


def validate(s: dict) -> list:
    """Return a list of human-readable problems; empty means valid."""
    problems = []
    if not isinstance(s, dict):
        return ["spec is not an object"]
    if not s.get("timeframes"):
        problems.append("no timeframes")
    for tf, b in (s.get("timeframes") or {}).items():
        n = len(b.get("time", []))
        for key in ("open", "high", "low", "close"):
            if len(b.get(key, [])) != n:
                problems.append(f"{tf}.{key} length {len(b.get(key, []))} != time {n}")
        if b.get("volume") and len(b["volume"]) != n:
            problems.append(f"{tf}.volume length != time")
        tt = b.get("time", [])
        if any(tt[i] > tt[i + 1] for i in range(len(tt) - 1)):
            problems.append(f"{tf}.time is not ascending")
    dft = s.get("defaultTimeframe")
    if dft and dft not in (s.get("timeframes") or {}):
        problems.append(f"defaultTimeframe {dft!r} not present")
    return problems


def normalize(s: dict) -> dict:
    """Coerce a hand-written spec into canonical form.

    Accepts ISO-8601 timestamps, compact bar keys (``t/o/h/l/c``) and the
    trade/zone/indicator aliases documented in ``docs/CHART_SPEC.md``, so an
    external system can write a human-friendly JSON file and still get a
    correct page. Returns a new dict; the input is not mutated.
    """
    if not isinstance(s, dict):
        raise TypeError("spec must be an object")
    out = dict(s)

    blocks = {}
    for tf, block in (s.get("timeframes") or {}).items():
        blocks[tf] = (_bars_from_block(block) if isinstance(block, dict)
                      else bars_from_rows(block))
    out["timeframes"] = blocks

    dft = out.get("defaultTimeframe")
    if not dft:
        if not blocks:
            raise ValueError("spec has no timeframes")
        dft = max(blocks, key=lambda k: len(blocks[k]["time"]))
    elif dft not in blocks:
        raise ValueError(f"defaultTimeframe {dft!r} not in timeframes {list(blocks)}")
    out["defaultTimeframe"] = dft

    ov = s.get("overlay") or {}
    out["overlay"] = {
        "trades": trades_from_rows(ov.get("trades") or []),
        "zones": zones_from_rows(ov.get("zones") or []),
    }
    if s.get("equity"):
        out["equity"] = _norm_equity(s["equity"])
    out["stats"] = stats_from_rows(s.get("stats") or [])
    inds = s.get("indicators")
    if inds:
        if isinstance(inds, list):
            out["indicators"] = {dft: indicators_from_rows(inds)}
        else:
            out["indicators"] = {tf: indicators_from_rows(v) for tf, v in inds.items()}

    out["version"] = int(out.get("version") or SPEC_VERSION)
    out.setdefault("symbol", "Chart")
    out.setdefault("exchange", "")
    out.setdefault("periodLabel", "")
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _resolve_asset(path, default_name):
    if path is not None:
        return Path(path)
    return ASSETS / default_name


def _emit(out_path, *, viewer_path, lib_path, title, spec, spec_url,
          inline_lib, lib_dir, compact) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not viewer_path.exists():
        raise FileNotFoundError(f"viewer template not found: {viewer_path}")
    if not lib_path.exists():
        raise FileNotFoundError(f"chart library not found: {lib_path}")

    if inline_lib:
        payload = base64.b64encode(lib_path.read_bytes()).decode("ascii")
        lib_rel = "data:text/javascript;base64," + payload
    else:
        dst = out.parent / lib_dir / LIB_NAME
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != lib_path.stat().st_size:
            shutil.copyfile(lib_path, dst)
        lib_rel = f"{lib_dir}/{LIB_NAME}"

    if spec is None:
        data = "null"
    else:
        if compact:
            spec = dict(spec)
            spec["timeframes"] = encode_timeframes(spec["timeframes"])
        # Escape '<' so a value like "</script>" cannot terminate the block.
        data = json.dumps(spec, separators=(",", ":")).replace("<", "\\u003c")

    tpl = viewer_path.read_text(encoding="utf-8")
    safe_title = str(title or "Chart")
    safe_title = safe_title.replace("&", "&amp;").replace("<", "&lt;")
    spec_token = json.dumps(spec_url) if spec_url else "null"
    html = (
        tpl.replace("__TITLE__", safe_title)
        .replace("__LIB__", lib_rel)
        .replace("__SPEC_URL__", spec_token)
        .replace("__DATA__", data)
    )
    out.write_text(html, encoding="utf-8")
    return out


def render(s, out_path, *, title=None, viewer=None, lib=None,
           inline_lib: bool = False, lib_dir: str = "lib",
           normalize_spec: bool = True, compact: bool = False,
           spec_url: str | None = None) -> Path:
    if normalize_spec:
        s = normalize(s)
    safe_title = str(title or s.get("title") or s.get("symbol") or "Chart")
    return _emit(out_path, viewer_path=_resolve_asset(viewer, VIEWER_NAME),
                 lib_path=_resolve_asset(lib, LIB_NAME), title=safe_title, spec=s,
                 spec_url=spec_url, inline_lib=inline_lib, lib_dir=lib_dir,
                 compact=compact)


def render_loader(out_path, spec_url, *, title=None, viewer=None, lib=None,
                  inline_lib: bool = False, lib_dir: str = "lib") -> Path:
    """Write a viewer page that fetches its spec JSON from ``spec_url`` at load.

    Useful when the data changes often (the page is regenerated once) or when
    the spec is served by another system alongside the page.
    """
    safe_title = str(title or "Chart")
    return _emit(out_path, viewer_path=_resolve_asset(viewer, VIEWER_NAME),
                 lib_path=_resolve_asset(lib, LIB_NAME), title=safe_title,
                 spec=None, spec_url=spec_url, inline_lib=inline_lib,
                 lib_dir=lib_dir, compact=False)


def render_file(spec_path, out_path, **kw) -> Path:
    s = normalize(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    problems = validate(s)
    if problems:
        raise ValueError("invalid spec: " + "; ".join(problems))
    return render(s, out_path, **kw)


# --------------------------------------------------------------------------
# gallery index
# --------------------------------------------------------------------------
def _page_title(path: Path) -> str:
    import re
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            head = fh.read(4096)
        m = re.search(r"<title>(.*?)</title>", head, re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except OSError:
        pass
    return path.parent.name


def gallery(out_root, *, title="Charts", subtitle="", recurse_dir="setups") -> Path:
    """Write an index.html listing top-level pages and nested catalogs."""
    root = Path(out_root)
    charts = [(p.name, p.name) for p in sorted(root.glob("*.html")) if p.name != "index.html"]
    catalogs = []
    for group in (root, root / recurse_dir):
        if not group.is_dir():
            continue
        for d in sorted(x for x in group.iterdir() if x.is_dir() and x.name != "lib"):
            idx = d / "index.html"
            if idx.exists():
                rel = idx.relative_to(root).as_posix()
                catalogs.append((_page_title(idx), rel))
    rows = "".join(f"<li><a href='{rel}'>{name}</a></li>" for name, rel in charts)
    top = f"<h3>Pages</h3><ul class='plain'>{rows}</ul>" if charts else ""
    crows = "".join(f"<li><a href='{rel}'>{name}</a></li>" for name, rel in catalogs)
    cat = f"<h3>Setup catalogs</h3><ul class='plain'>{crows}</ul>" if crows else ""
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{background:#0e1117;color:#d1d4dc;font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;"
        "padding:26px;max-width:980px;margin:0 auto}"
        "h1{color:#fff;font-size:20px;font-weight:600}"
        "h2{color:#787b86;font-size:13px;font-weight:400;margin:4px 0 18px}"
        "h3{color:#787b86;font-size:12px;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px}"
        "ul{list-style:none;padding:0;columns:2;column-gap:30px}"
        "li{margin:2px 0}a{color:#2962ff;text-decoration:none}a:hover{text-decoration:underline}"
        f"</style></head><body><h1>{title}</h1><h2>{subtitle}</h2>{top}{cat}</body></html>"
    )
    dest = root / "index.html"
    dest.write_text(html, encoding="utf-8")
    return dest


# --------------------------------------------------------------------------
# CSV convenience
# --------------------------------------------------------------------------
def _read_rows(path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _first_present(fieldnames, *names):
    lower = {f.lower(): f for f in fieldnames}
    for n in names:
        if n in lower:
            return lower[n]
    return None


def bars_from_csv(path, time=None, open=None, high=None, low=None,
                  close=None, volume=None) -> dict:
    rows = _read_rows(path)
    if not rows:
        return {"time": [], "open": [], "high": [], "low": [], "close": []}
    cols = list(rows[0].keys())
    time = time or _first_present(cols, "time", "date", "datetime", "timestamp") or cols[0]
    open = open or _first_present(cols, "open", "o", "bid_open") or cols[1]
    high = high or _first_present(cols, "high", "h", "bid_high") or cols[2]
    low = low or _first_present(cols, "low", "l", "bid_low") or cols[3]
    close = close or _first_present(cols, "close", "c", "bid_close") or cols[4]
    volume = volume or _first_present(cols, "volume", "v", "bid_volume")
    return bars_from_rows(rows, time=time, open=open, high=high,
                          low=low, close=close, volume=volume)


def trades_from_csv(path) -> list:
    return trades_from_rows(_read_rows(path))


def equity_from_csv(path) -> dict:
    rows = _read_rows(path)
    if not rows:
        return {"time": [], "value": [], "label": "Equity"}
    cols = list(rows[0].keys())
    time = _first_present(cols, "time", "date", "datetime") or cols[0]
    value = _first_present(cols, "equity", "value", "balance") or cols[-1]
    return {
        "time": [to_epoch(r[time]) for r in rows],
        "value": [float(r[value]) for r in rows],
        "label": "Equity",
    }


_IND_COLORS = ["#e5a93d", "#4fc3f7", "#ba68c8", "#aed581", "#ff8a65"]


def parse_indicators(text, colors=None) -> list:
    """Parse 'SMA50,EMA200,BB20' into indicator definitions."""
    if not text:
        return []
    palette = colors or _IND_COLORS
    out = []
    for i, tok in enumerate(x.strip() for x in text.split(",") if x.strip()):
        up = tok.upper()
        color = palette[i % len(palette)]
        if up.startswith("BB"):
            out.append({"name": tok, "type": "bb", "color": color})
        elif up.startswith("EMA"):
            out.append({"name": tok, "type": "ema", "period": int(tok[3:]), "color": color})
        else:
            out.append({"name": tok, "type": "sma", "period": int(tok[3:]), "color": color})
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="chartlab-chart",
                                     description="Render a JSON chart spec to offline HTML.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render", help="render spec.json -> out.html")
    p.add_argument("spec")
    p.add_argument("out")
    p.add_argument("--title", default=None)
    p.add_argument("--inline-lib", action="store_true",
                   help="embed the chart JS in the HTML (single shareable file)")
    p.add_argument("--compact", action="store_true",
                   help="base64-encode bars (~3x smaller page, not human-readable)")
    p.add_argument("--viewer", default=None)
    p.add_argument("--lib", default=None)

    p = sub.add_parser("loader",
                       help="write a page that fetches its spec JSON from a URL")
    p.add_argument("spec_url")
    p.add_argument("out")
    p.add_argument("--title", default=None)
    p.add_argument("--inline-lib", action="store_true")
    p.add_argument("--viewer", default=None)
    p.add_argument("--lib", default=None)

    p = sub.add_parser("from-csv", help="build a page from bars/trades/equity CSVs")
    p.add_argument("--bars", required=True)
    p.add_argument("--trades", default=None)
    p.add_argument("--equity", default=None)
    p.add_argument("--symbol", default="")
    p.add_argument("--exchange", default="")
    p.add_argument("--timeframe", default="D1")
    p.add_argument("--period-label", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--inline-lib", action="store_true")
    p.add_argument("--compact", action="store_true")

    p = sub.add_parser("validate", help="validate a spec file")
    p.add_argument("spec")

    args = parser.parse_args(argv)

    if args.cmd == "render":
        s = normalize(json.loads(Path(args.spec).read_text(encoding="utf-8")))
        problems = validate(s)
        if problems:
            print("invalid spec:", *problems, sep="\n  ", file=sys.stderr)
            return 2
        out = render(s, args.out, title=args.title, viewer=args.viewer,
                     lib=args.lib, inline_lib=args.inline_lib, compact=args.compact)
        print(out)
        return 0

    if args.cmd == "loader":
        out = render_loader(args.out, args.spec_url, title=args.title,
                            viewer=args.viewer, lib=args.lib,
                            inline_lib=args.inline_lib)
        print(out)
        return 0

    if args.cmd == "validate":
        s = normalize(json.loads(Path(args.spec).read_text(encoding="utf-8")))
        problems = validate(s)
        if problems:
            print("invalid:", *problems, sep="\n  ")
            return 2
        bars = sum(len(b.get("time", [])) for b in s.get("timeframes", {}).values())
        ov = s.get("overlay", {})
        print(f"ok: {len(s.get('timeframes', {}))} timeframes, {bars} bars, "
              f"{len(ov.get('trades', []))} trades, {len(ov.get('zones', []))} zones")
        return 0

    bars = bars_from_csv(args.bars)
    s = spec(
        args.symbol or Path(args.bars).stem,
        {args.timeframe: bars},
        exchange=args.exchange,
        period_label=args.period_label,
        default_tf=args.timeframe,
        trades=trades_from_csv(args.trades) if args.trades else None,
        equity=equity_from_csv(args.equity) if args.equity else None,
    )
    out = render(s, args.out, inline_lib=args.inline_lib, compact=args.compact)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
