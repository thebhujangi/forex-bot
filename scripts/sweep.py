"""Test a grid of parameter values (in-sample only) and print a ranked table.

Look for a *plateau*: neighbouring settings that all do reasonably well. A single best
combination surrounded by losers is luck, not an edge.

Usage:
  .venv\\Scripts\\python scripts\\sweep.py london_breakout --grid sl_mode=mid,opposite trend_ema=0,20,50
"""
import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd
import yaml

from forexbot import DATA_DIR
from forexbot.backtest import run_backtest
from forexbot.config import load_config

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--grid", nargs="+", required=True, metavar="KEY=V1,V2,...")
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--start", help="first date to test (default: data.start in config)")
    args = ap.parse_args()

    cfg = load_config()
    # times like 07:00 stay strings (YAML would read them as base-60 numbers)
    grid = {k: [x if ":" in x else yaml.safe_load(x) for x in v.split(",")]
            for k, v in (g.split("=", 1) for g in args.grid)}
    rows = []
    combos = list(itertools.product(*grid.values()))
    for n, values in enumerate(combos, 1):
        override = dict(zip(grid, values))
        res = run_backtest(cfg, args.strategy, args.symbols, params_override=override,
                           data_dir=Path(args.data_dir), start=args.start)
        s, t = res.summary, res.trades
        row = {**override, "trades": s.get("trades", 0)}
        if s.get("trades"):
            row |= {"exp_R": s["expectancy_r"], "PF": s["profit_factor"], "win%": s["win_rate_pct"],
                    "maxDD%": s["max_drawdown_pct"], "ret%": s["total_return_pct"]}
            for sym, r in t.groupby("symbol").r.mean().items():
                row[f"{sym}_R"] = r
        rows.append(row)
        print(f"[{n}/{len(combos)}] {override} -> {row.get('exp_R', float('nan')):+.3f}R", flush=True)

    table = pd.DataFrame(rows).sort_values("exp_R", ascending=False)
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print("\n" + table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
