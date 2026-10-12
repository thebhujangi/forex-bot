"""Free long-history daily candles from Dukascopy (Swiss bank), for testing slow strategies over decades.

One LZMA-compressed file per symbol per year. Each record is 24 bytes, big-endian:
seconds from the start of the year, open, close, low, high (int, scaled), volume (float).
Dukascopy days run 00:00-24:00 UTC (Elev8's run 17:00-17:00 New York); for multi-week trend
systems that difference is small. The server rate-limits hard, so requests are slow and polite
and every finished year is cached so runs can resume.
"""
import lzma
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
SCALE = {"XAUUSD": 1e3, "USDJPY": 1e3}  # everything else 1e5
RECORD = struct.Struct(">5if")
PAUSE = 2.0            # seconds between requests


def _get(url: str, tries: int = 7) -> bytes:
    delay = 30.0
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            time.sleep(PAUSE)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            err = e
        except OSError as e:
            err = e
        print(f"    server busy ({err}); waiting {delay:.0f}s", flush=True)
        time.sleep(delay)
        delay = min(delay * 2, 600)
    raise RuntimeError(f"giving up on {url}: {err}")


def fetch_daily_year(symbol: str, year: int, cache_dir: Path) -> pd.DataFrame:
    """Daily candles (index = day start, UTC) for one calendar year. Weekends dropped."""
    cache = Path(cache_dir) / symbol / f"{year}_D1.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    raw = _get(f"{BASE}/{symbol}/{year}/BID_candles_day_1.bi5")
    data = lzma.decompress(raw) if raw else b""
    scale = SCALE.get(symbol, 1e5)
    start = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
    rows = [RECORD.unpack_from(data, i) for i in range(0, len(data) - len(data) % 24, 24)]
    df = pd.DataFrame(rows, columns=["t", "open", "close", "low", "high", "volume"])
    df.index = pd.DatetimeIndex(start + pd.to_timedelta(df.pop("t").to_numpy(), unit="s")).as_unit("ns")
    df = df[(df.volume > 0) & (df.index.weekday < 5)]
    for col in ("open", "close", "low", "high"):
        df[col] = df[col] / scale
    df = df[["open", "high", "low", "close", "volume"]]
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cache)
    return df
