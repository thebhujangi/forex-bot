"""Download 20 years of daily candles from Dukascopy into data/dukascopy/.

Stops at the in-sample end date on purpose: the held-back period is tested on Elev8's own data.
Safe to re-run: finished years are cached and skipped.

Usage:  .venv\\Scripts\\python scripts\\download_dukascopy.py [--from-year 2004] [--symbols XAUUSD EURUSD]
"""
import argparse
import shutil
import sys

import pandas as pd

from forexbot import DATA_DIR
from forexbot.config import load_config
from forexbot.data import save_bars
from forexbot.dukascopy import fetch_daily_year

sys.stdout.reconfigure(encoding="utf-8")
OUT = DATA_DIR / "dukascopy"


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=cfg["strategies"]["daily_trend"]["symbols"])
    ap.add_argument("--from-year", type=int, default=2004)
    args = ap.parse_args()

    end = pd.Timestamp(cfg["backtest"]["in_sample_end"])
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols:
        frames = []
        for year in range(args.from_year, end.year + 1):
            frames.append(fetch_daily_year(sym, year, OUT / "cache"))
        d1 = pd.concat([f for f in frames if len(f)]).sort_index()
        d1 = d1[~d1.index.duplicated()]
        save_bars(d1, sym, "D1", OUT)
        print(f"{sym:7} D1 {len(d1):>5,} days  {d1.index[0]:%Y-%m-%d} -> {d1.index[-1]:%Y-%m-%d}", flush=True)

    # Contract specs come from the broker download.
    if (DATA_DIR / "symbols.json").exists():
        shutil.copy(DATA_DIR / "symbols.json", OUT / "symbols.json")
    print("Done.")


if __name__ == "__main__":
    main()
