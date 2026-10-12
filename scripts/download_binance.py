"""Download crypto candle history from Binance's free public data API into data/binance/.

No account or key needed. Stops at the in-sample end date (the held-back period is tested later
on Elev8's own prices). Saved under our names (BTCUSD etc.) so backtests can use --data-dir.

Usage:  .venv\\Scripts\\python scripts\\download_binance.py [--symbols BTCUSD ETHUSD] [--timeframes M15 D1]
"""
import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

from forexbot import DATA_DIR
from forexbot.config import load_config
from forexbot.data import save_bars

sys.stdout.reconfigure(encoding="utf-8")
OUT = DATA_DIR / "binance"
API = "https://data-api.binance.vision/api/v3/klines"
INTERVAL = {"M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}
PAIR = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT", "XRPUSD": "XRPUSDT",
        "BNBUSD": "BNBUSDT", "LTCUSD": "LTCUSDT", "DOGEUSD": "DOGEUSDT", "ADAUSD": "ADAUSDT"}


def get(url: str, tries: int = 6):
    delay = 5
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"    retry ({e})", flush=True)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"giving up on {url}")


def download(symbol: str, tf: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows, cur = [], int(start.timestamp() * 1000)
    stop = int(end.timestamp() * 1000)
    while cur < stop:
        batch = get(f"{API}?symbol={PAIR[symbol]}&interval={INTERVAL[tf]}&startTime={cur}&limit=1000")
        if not batch:
            break
        rows += batch
        cur = batch[-1][0] + 1
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", *range(6)])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.t, unit="ms", utc=True)).as_unit("ns")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[df.index < end]
    return df[~df.index.duplicated()]


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
    ap.add_argument("--timeframes", nargs="+", default=["M5", "M15", "H1", "D1"])
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", help="default: the in-sample end date (keeps the held-back period unseen)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start, tz="UTC")
    end = (pd.Timestamp(args.end, tz="UTC") if args.end
           else pd.Timestamp(cfg["backtest"]["in_sample_end"], tz="UTC") + pd.Timedelta(days=1))
    for sym in args.symbols:
        for tf in args.timeframes:
            df = download(sym, tf, start, end)
            save_bars(df, sym, tf, out)
            print(f"{sym:7} {tf:4} {len(df):>8,} bars  {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}",
                  flush=True)
    if (DATA_DIR / "symbols.json").exists():
        shutil.copy(DATA_DIR / "symbols.json", out / "symbols.json")
    print("Done.")


if __name__ == "__main__":
    main()
