"""Step 2: download candle history from your MT5 broker into data/.

Usage:  .venv\\Scripts\\python scripts\\download_data.py [--symbols XAUUSD EURUSD] [--timeframes M15 H1]
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import pandas as pd

from forexbot import DATA_DIR
from forexbot import mt5_client as m
from forexbot.config import load_config
from forexbot.data import save_bars
from forexbot.instruments import load_specs, save_specs

sys.stdout.reconfigure(encoding="utf-8")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=cfg["symbols"])
    ap.add_argument("--timeframes", nargs="+", default=cfg["data"]["timeframes"])
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    mt5 = m.connect(cfg)
    try:
        acc = m.account_summary(mt5)
        print(f"Connected to {acc['company']} ({acc['server']})")
        names = {s: m.resolve_symbol(mt5, s, cfg.get("symbol_map")) for s in args.symbols}

        server_time = cfg["broker"]["server_time"]
        if str(server_time) == "auto":
            detected, msg = m.detect_server_time(mt5, next(iter(names.values())))
            print(f"Server clock: {msg}")
            if detected is None:
                detected = "ny_plus_7"
                print("  -> assuming ny_plus_7 (GMT+2/+3, the most common setup). Re-run while the "
                      "market is open to confirm, or set broker.server_time in config.yaml.")
            server_time = detected

        specs = load_specs()
        start = m.utc(cfg["data"]["start"])
        end = datetime.now(timezone.utc)
        for sym, name in names.items():
            specs[sym] = m.symbol_spec(mt5, sym, name)
            for tf in args.timeframes:
                df = m.download_bars(mt5, name, tf, start, end, server_time)
                path = save_bars(df, sym, tf)
                print(f"  {sym:7} {tf:4} {len(df):>8,} bars  {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}"
                      f"  ({path.name})")
                if df.index[0] > pd.Timestamp(start) + pd.Timedelta(days=30):
                    print(f"          !! history starts later than {cfg['data']['start']}. Set MT5 Tools > "
                          f"Options > Charts > 'Max bars in chart' to Unlimited, restart MT5 and re-run "
                          f"(the broker may also simply not have older {tf} data).")
        save_specs(specs)
        (DATA_DIR / "meta.json").write_text(json.dumps({
            "broker": acc["company"], "server": acc["server"], "server_time": server_time,
            "downloaded": end.isoformat(), "symbols": names,
        }, indent=2), encoding="utf-8")
        print("Done.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
