# ChartLab Chart Spec (version 1)

A chart is a single JSON object. `chart.render(spec, out.html)` reads it, inlines it
into the viewer template, and writes a self-contained interactive page.

Validate before rendering:

```bash
python3 chart.py validate spec.json
```

or programmatically with `chart.validate(spec)` (returns a list of problems; empty = valid).

## Top level

| Field              | Type              | Required | Notes |
|--------------------|-------------------|----------|-------|
| `version`          | int               | yes      | Schema version. Currently `1`. |
| `symbol`           | string            | yes      | Shown in the header, e.g. `"XAUUSD"`. |
| `exchange`         | string            | no       | e.g. `"retail CFD"`. Shown in the sub-header. |
| `periodLabel`      | string            | no       | e.g. `"D1 · XAUUSD"`. |
| `defaultTimeframe` | string            | no       | Key into `timeframes`. Defaults to the timeframe with the most bars. |
| `timeframes`       | object            | yes      | Map of `"D1"`, `"H4"`, `"W1"`, … to a **bar block**. |
| `overlay`          | object            | no       | `{ "trades": [...], "zones": [...] }`. |
| `equity`           | object or `null`  | no       | Equity curve; adds the bottom pane. |
| `indicators`       | object            | no       | Map of timeframe → list of **indicator** objects. |
| `stats`            | list or object    | no       | Metrics panel (top-right); see below. |
| `title`            | string            | no       | Overrides the page `<title>`. |

The chart opens on `defaultTimeframe` and switches between the provided timeframes
without reloading.

## Bar block

One entry in `timeframes`. All arrays must be the same length and ordered oldest → newest.

| Field    | Type            | Required | Notes |
|----------|-----------------|----------|-------|
| `time`   | int[]           | yes      | **Epoch seconds, UTC**, strictly ascending. |
| `open`   | float[]         | yes      | |
| `high`   | float[]         | yes      | |
| `low`    | float[]         | yes      | |
| `close`  | float[]         | yes      | |
| `volume` | float[] or null | no       | Optional. The bundled viewer has no volume pane, so omit it to save space. |

Compact keys are also accepted and expanded: `t`→`time`, `o`→`open`, `h`→`high`,
`l`→`low`, `c`→`close`, `v`→`volume`.

```json
"timeframes": {
  "D1": {
    "time":  [1704067200, 1704153600],
    "open":  [2050.0, 2054.0],
    "high":  [2056.0, 2060.0],
    "low":   [2048.0, 2051.0],
    "close": [2054.0, 2058.0]
  }
}
```

## Trade objects (`overlay.trades`)

| Field        | Type   | Required | Notes |
|--------------|--------|----------|-------|
| `dir`        | string | yes      | `"long"` / `"short"` (also accepts `1`/`-1`, `buy`/`sell`, `l`/`s`). |
| `entryTime`  | time   | yes      | |
| `entryPrice` | float  | yes      | |
| `lots`       | float  | no       | Displayed in the legend. |
| `exitTime`   | time   | no       | Omit for an open trade. |
| `exitPrice`  | float  | no       | |
| `net`        | float  | no       | P/L shown on the trade. |
| `reason`     | string | no       | e.g. `"tp"`, `"sl"`, `"signal"`, `"end"`. |
| `sl`         | float  | no       | Stop loss line. |
| `tp`         | float  | no       | Take profit line. |

Aliases accepted: `direction`/`side` for `dir`; `entry_time`/`time`, `entry_price`/`price`;
`exit_time`, `exit_price`, `stop` for `sl`, `target` for `tp`.

## Zone objects (`overlay.zones`)

A shaded rectangle spanning two times and two prices.

| Field   | Type   | Required | Notes |
|---------|--------|----------|-------|
| `start` | time   | yes      | |
| `end`   | time   | yes      | |
| `low`   | float  | yes      | |
| `high`  | float  | yes      | |
| `label` | string | no       | Drawn inside the rectangle. |
| `color` | string | no       | CSS hex, e.g. `"#f5c542"`. |

Aliases: `from`/`to` for `start`/`end`, `lo`/`bottom` for `low`, `hi`/`top` for `high`.

## Equity object (`equity`)

| Field   | Type    | Required | Notes |
|---------|---------|----------|-------|
| `time`  | int[]   | yes      | Epoch seconds UTC, ascending. |
| `value` | float[] | yes      | Same length as `time`. |
| `label` | string  | no       | Legend text, default `"Equity"`. |

The equity pane shares the main chart's time axis and can be hidden with its checkbox.

## Stats panel (`stats`)

A provider-supplied list of summary rows shown in a panel at the top-right of the
chart. Purely informational; the user can hide it with the **Metrics** checkbox.

| Field   | Type            | Required | Notes |
|---------|-----------------|----------|-------|
| `label` | string          | yes      | Row label, e.g. `"Sharpe"`. |
| `value` | number or string| yes      | Rendered as-is (format it yourself for `%`/`$`). |
| `tone`  | string          | no       | `"up"` (green) or `"down"` (red). `pos`/`good` and `neg`/`bad` also accepted. |

A `{"label": value, ...}` object is also accepted. When `tone` is omitted the
viewer infers it from a leading `+`/`-` or the sign of a number.

```json
"stats": [
  { "label": "Return", "value": "+22.59%", "tone": "up" },
  { "label": "Max DD", "value": "-22.51%", "tone": "down" },
  { "label": "Trades", "value": 117 }
]
```

## Indicator objects (`indicators["D1"]`, …)

Indicators are **provider-supplied** and only toggled by the user, never added.

| Field    | Type            | Required | Notes |
|----------|-----------------|----------|-------|
| `name`   | string          | yes      | Chip label, e.g. `"SMA50"`. |
| `color`  | string          | no       | CSS hex. A palette is used if omitted. |
| `width`  | int             | no       | Line width, default `1`. |
| `on`     | bool            | no       | Initial visibility, default `true`. |
| `values` | (float\|null)[] | no       | Explicit series aligned to the timeframe's `time`. **Preferred.** |
| `type`   | string          | no       | Fallback if `values` is absent: `"sma"` (default), `"ema"`, or `"bb"`. |
| `period` | int             | no       | Period used by the fallback `type`, default `20`. |

When `values` is supplied the viewer plots it as-is; when absent it computes the
indicator from the timeframe's `close` using `type`/`period`.

```json
"indicators": {
  "D1": [
    { "name": "SMA50", "type": "sma", "period": 50, "color": "#e5a93d", "values": [null, null, 2058.0] }
  ]
}
```

## Times

`time` values are always **epoch seconds in UTC**. The builder helpers also accept:

- an integer/float epoch (returned as-is),
- an ISO-8601 string such as `"2024-01-01T00:00:00Z"`,
- a `datetime` (`tzinfo` assumed UTC when naive).

`chart.to_epoch(value)` performs the conversion; `chart.to_iso(seconds)` formats one back.

## Programmatic construction

Use `chart.spec(...)` to normalize flexible inputs into a valid spec, then
`chart.validate(...)` and `chart.render(...)`:

```python
from chart import spec, render, bars_from_rows, trades_from_rows, zones_from_rows

s = spec(
    "XAUUSD",
    {
        "D1": bars_from_rows(rows),                       # dict or sequence rows
        "H4": {"time": [...], "open": [...], ...},        # already-normalized block
    },
    exchange="retail CFD",
    period_label="D1 · XAUUSD",
    default_tf="D1",
    trades=trades_from_rows(trade_rows),
    zones=zones_from_rows(zone_rows),
    equity={"time": [...], "value": [...], "label": "Equity"},
    indicators=[{"name": "SMA50", "type": "sma", "period": 50}],
)
render(s, "out.html", title="XAUUSD D1", inline_lib=False)
```

## Builder helpers

| Function | Purpose |
|----------|---------|
| `to_epoch(v)` / `to_iso(sec)` | time conversion |
| `bars_from_rows(rows, ...)` | rows → bar block (dict by column name, or positional) |
| `trades_from_rows(rows)` | rows → normalized trades |
| `zones_from_rows(rows)` | rows → normalized zones |
| `indicators_from_rows(rows)` | rows → normalized indicators |
| `stats_from_rows(rows)` | rows / `{label: value}` → normalized stats |
| `encode_block(block)` / `encode_timeframes(blocks)` | bar block(s) → compact base64 |
| `bars_from_csv(path, ...)` | CSV → bar block (auto-detects columns) |
| `trades_from_csv(path)` / `equity_from_csv(path)` | CSV → trades / equity |
| `parse_indicators("SMA50,EMA200,BB20")` | shorthand → indicator definitions |
| `spec(...)` | assemble a validated spec |
| `validate(spec)` | list of problems (empty = valid) |
| `render(spec, out, ...)` | write the HTML page |
| `render_file(spec_path, out, ...)` | load + validate + render |
| `render_loader(out, spec_url, ...)` | write a page that fetches its spec at runtime |
| `gallery(dir)` | write an index page listing the charts |

## Rendering options

`render(spec, out_path, *, title=None, viewer=None, lib=None, inline_lib=False, lib_dir="lib", compact=False, spec_url=None)`

- `title` — page title; defaults to `spec["title"]`/`symbol`.
- `viewer` / `lib` — override the template or chart library paths.
- `inline_lib=True` — base64-embed the chart library so the HTML is a single
  portable file (no sibling `lib/` directory). Otherwise the library is copied to
  `<out_dir>/lib/` and referenced relatively.
- `lib_dir` — directory (relative to the page) that holds the chart library. Use
  this to make many nested pages share one copy, e.g. `lib_dir="../../lib"`.
- `compact=True` — base64-encode each bar block (absolute `uint32` time + prices
  scaled by `1e4`) for a page roughly 3x smaller. The JSON is no longer human
  readable but renders identically; the payload stays `<`-escaped.

## Loading the spec at runtime

`render_loader(out, spec_url, ...)` writes a page whose inline payload is `null`
and which fetches `spec_url` as JSON when opened. This suits data that changes
often (regenerate the page once, keep serving fresh JSON) or specs produced by
another system. The spec is the same object documented here.

The loader also honours a `?spec=<url>` query parameter, which overrides the
baked-in URL — useful for previewing any spec from one page.

```bash
# Write loader.html; it fetches specs/xau.json next to itself when opened.
python3 chart.py loader specs/xau.json loader.html --title "XAUUSD"
# or the same page with an override:
#   loader.html?spec=https://example.com/other.json
```

Pages written by `render`/`render_file` keep the spec inlined and need no server
(they work from `file://`); loader pages need to be served over HTTP/HTTPS.
