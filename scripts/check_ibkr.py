"""Check the bot can read live Nasdaq futures (MNQ) prices from Interactive Brokers.

Before running: open IB Gateway (or TWS), log in (paper account is fine), and in
Configure > Settings > API tick "Enable ActiveX and Socket Clients" and note the port.

Usage:
  .venv\\Scripts\\python scripts\\check_ibkr.py            # port from config.yaml (futures_prices.ibkr_port)
  .venv\\Scripts\\python scripts\\check_ibkr.py --port 7497
"""
import argparse
import sys

import pandas as pd

from forexbot.config import load_config
from forexbot.feeds import FeedError
from forexbot.ibkr import IbkrMarket

sys.stdout.reconfigure(encoding="utf-8")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=(cfg.get("futures_prices") or {}).get("ibkr_port", 4002))
    args = ap.parse_args()
    try:
        m = IbkrMarket(port=args.port)
        c = m.contract("MNQ")
        print(f"Connected. Front-month contract: {c.localSymbol} (expires {c.lastTradeDateOrContractMonth})")
        bars = m.bars("MNQ", "M5", 12)
    except FeedError as e:
        print(f"Problem: {e}")
        return 1
    age = pd.Timestamp.now(tz="UTC") - (bars.index[-1] + pd.Timedelta(minutes=5))
    print(bars.tail(3).to_string())
    print(f"Last closed 5-min candle ended {age.total_seconds() / 60:.0f} minutes ago.")
    if age > pd.Timedelta(minutes=20):
        print("Prices look delayed or the market is closed. If the market is open, subscribe to "
              "'CME Real-Time (NP, L1)' in IBKR Client Portal > Settings > Market Data Subscriptions.")
    print("\nIf this looks right, set futures_prices.source: ibkr in config.yaml and restart the bot.")
    m.ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
