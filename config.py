from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _project_root() -> Path:
    """Locate where ``data/``/``out/`` live.

    When the package is nested inside a project that has a sibling ``data/``
    directory (this repository's development layout) that project is the root.
    In a standalone checkout the package directory itself is the root.
    """
    pkg = Path(__file__).resolve().parent
    return pkg.parent if (pkg.parent / "data").is_dir() else pkg


ROOT = _project_root()


# The cost fields filled from priceData when left at None.
FTMO_COST_FIELDS = ("contract_size_oz", "commission_per_side_per_lot", "commission_pct_side",
                    "slippage_per_side_usd", "swap_long_per_lot_per_day", "swap_short_per_lot_per_day")


def ftmo_costs(symbol: str, root=None) -> dict:
    """The legacy engine's cost fields for ``symbol``, from priceData's ``COST_MODELS``.

    priceData is the one FTMO cost source (its ``docs/COSTS.md``); ChartLab keeps
    no copy of a cost number. Derived here: contract size; commission per side
    (flat $ per lot for FX, percent of notional for metals, both deals charged);
    slippage per market fill (``slippage_pip_per_fill`` x pip, in price units);
    swap in USD per lot per night (swap points x point x contract, the schedule
    priceData read from the terminal). The engine books P/L in the quote currency
    with a fixed swap per night, so only USD-quoted symbols with a points swap
    are supported: EURUSD, GBPUSD, AUDUSD, NZDUSD, XAUUSD, XAGUSD. The spread is
    the bars' ask side (Dukascopy) or priceData's ``spread_pip`` added to BID (FTMO).
    """
    from . import pricedata
    m = pricedata.cost_model(symbol, root)
    sym = symbol.upper()
    if not sym.endswith("USD") or m.get("swap_mode") != 1:
        raise ValueError(f"the legacy engine books P/L in USD with a fixed swap per night; {sym} "
                         "(non-USD quote or a percent-of-price swap) is not supported")
    point, contract = float(m["point"]), float(m["contract"])
    return {k: round(v, 10) for k, v in {
        "contract_size_oz": contract,
        "commission_per_side_per_lot": float(m.get("commission_usd_lot_side") or 0.0),
        "commission_pct_side": float(m.get("commission_pct_side") or 0.0),
        "slippage_per_side_usd": float(m.get("slippage_pip_per_fill") or 0.0) * float(m["pip"]),
        "swap_long_per_lot_per_day": float(m["swap_long_pts"]) * point * contract,
        "swap_short_per_lot_per_day": float(m["swap_short_pts"]) * point * contract,
    }.items()}


@dataclass
class CostConfig:
    """Costs of the legacy backtest demo (never quote its numbers).

    Every cost field left at None is filled from priceData's ``COST_MODELS`` for
    ``symbol`` (see ``ftmo_costs``) when the config is made; pass a value to
    override it. Swap is charged at 00:00 FTMO server time for each weekday that
    ends with the position open, x3 for Wednesday's (the Wed->Thu rollover,
    tester-verified), none for Saturday/Sunday.
    """
    symbol: str = "XAUUSD"
    contract_size_oz: float | None = None        # units per lot (priceData "contract")
    account_currency: str = "USD"
    lot_step: float = 0.01
    min_lot: float = 0.01

    commission_per_side_per_lot: float | None = None   # flat $/lot/side (FX); added to the percent below
    commission_pct_side: float | None = None           # percent of notional per side (metals)
    slippage_per_side_usd: float | None = None         # price units per market fill
    spread_add_per_side_usd: float = 0.0

    swap_long_per_lot_per_day: float | None = None
    swap_short_per_lot_per_day: float | None = None
    triple_swap_on_wednesday: bool = True        # x3 on the Wed->Thu rollover (00:00 Thursday server)

    def __post_init__(self):
        missing = [f for f in FTMO_COST_FIELDS if getattr(self, f) is None]
        if missing:
            costs = ftmo_costs(self.symbol)
            for f in missing:
                setattr(self, f, costs[f])

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def from_json(cls, path: str | Path) -> "CostConfig":
        raw = json.loads(Path(path).read_text())
        cfg = cls(**raw)
        if "commission_pct_side" not in raw and raw.get("commission_per_side_per_lot", 0) and cfg.commission_pct_side:
            # Saved before commission_pct_side existed: its flat figure was the whole commission, and
            # priceData's percent would now be charged on top of it.
            warnings.warn(
                f"{path}: no commission_pct_side, so priceData's {cfg.commission_pct_side} % of notional per "
                f"side for {cfg.symbol} is ADDED to commission_per_side_per_lot="
                f"{raw['commission_per_side_per_lot']}. Set commission_pct_side explicitly "
                "(FX: 0 with 2.5 flat; metals: the percent with 0 flat).",
                UserWarning, stacklevel=2)
        return cfg


@dataclass
class BacktestConfig:
    signal_timeframe: str = "D1"
    data_clock: str = "utc"          # clock of the M1 bars: "utc" (Dukascopy) or "ftmo" (server time)
    start_capital_usd: float = 100_000.0
    default_lots: float = 0.5
    stop_if_sl_tp_same_bar: bool = True
    allow_reentry_same_bar: bool = False


@dataclass
class DataConfig:
    symbol: str = "XAUUSD"
    start: str = "2022-01-01"
    end: str | None = None
    base_dir: Path = field(default_factory=lambda: ROOT / "data")

    @property
    def m1_parquet(self) -> Path:
        return self.base_dir / "parquet" / f"{self.symbol}_M1.parquet"

    @property
    def tf_dir(self) -> Path:
        return self.base_dir / "parquet" / "tfs"
