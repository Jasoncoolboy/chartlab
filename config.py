from __future__ import annotations

import json
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


@dataclass
class CostConfig:
    symbol: str = "XAUUSD"
    contract_size_oz: int = 100
    account_currency: str = "USD"
    lot_step: float = 0.01
    min_lot: float = 0.01

    commission_per_side_per_lot: float = 0.0
    slippage_per_side_usd: float = 0.02
    spread_add_per_side_usd: float = 0.0

    swap_long_per_lot_per_day: float = -14.0
    swap_short_per_lot_per_day: float = -4.0
    triple_swap_on_wednesday: bool = True

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def from_json(cls, path: str | Path) -> "CostConfig":
        return cls(**json.loads(Path(path).read_text()))


@dataclass
class BacktestConfig:
    signal_timeframe: str = "D1"
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
