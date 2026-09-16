#!/usr/bin/env python3
"""Run the ChartLab pipeline from the repository root.

The modules use relative imports internally, so the code must be imported as a
package. This launcher registers the repository root as the ``chartlab``
package and then delegates to ``chartlab.cli.main``::

    python3 run.py backtest --strategy donchian --timeframe D1 --n 20
    python3 run.py chart --timeframe D1 --name donchian_D1
    python3 run.py setup --kind donchian --timeframe H4 --limit 15

When the package is nested inside a project (development layout), the
equivalent ``python3 -m chartlab ...`` still works from that project root.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "chartlab",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit("chartlab: unable to load the package")
    module = importlib.util.module_from_spec(spec)
    sys.modules["chartlab"] = module
    spec.loader.exec_module(module)

    from chartlab.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
