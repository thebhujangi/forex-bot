"""Outside data for strategy filters (all free), saved to data/ext_<name>.csv.

  * FRED: US 10-year real interest rate (DFII10) and the broad dollar index (DTWEXBGS).
  * Deribit perpetual futures: funding rates (what traders pay every 8h to hold longs vs shorts).
  * CFTC Commitments of Traders: big speculators' net position in gold / euro / pound futures.

Each series is stored with the time it became PUBLIC, so a backtest only ever uses what a live
bot could have known at that moment (no peeking ahead).
"""
import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from . import DATA_DIR

COT_CODES = {"XAUUSD": "088691", "EURUSD": "099741", "GBPUSD": "096742"}
FUNDING = {"BTCUSD": "BTC-PERPETUAL", "ETHUSD": "ETH-PERPETUAL"}


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def path(name: str):
    return DATA_DIR / f"ext_{name}.csv"


def save(name: str, s: pd.Series) -> int:
    s.rename("value").rename_axis("available").to_csv(path(name))
    return len(s)


def load(name: str) -> pd.Series:
    df = pd.read_csv(path(name))
    return pd.Series(df["value"].to_numpy(float),
                     index=pd.DatetimeIndex(pd.to_datetime(df["available"], utc=True, format="ISO8601")))


def fred(series: str, key: str, start: str = "2015-01-01") -> pd.Series:
    """Daily FRED series. Published the next US morning, so usable from day+1 14:00 UTC."""
    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                "observation_start": start})
    obs = _get(f"https://api.stlouisfed.org/fred/series/observations?{q}")["observations"]
    s = pd.Series({pd.Timestamp(o["date"], tz="UTC") + pd.Timedelta(days=1, hours=14): float(o["value"])
                   for o in obs if o["value"] not in (".", "")})
    return s.sort_index()


def deribit_funding(instrument: str, start: str = "2019-06-01") -> pd.Series:
    """Perpetual-futures funding, as the 8-hour rate (0.0001 = 0.01%), hourly. Deribit is used
    because Binance/Bybit/BitMEX are blocked from Malaysian connections."""
    out, t = {}, pd.Timestamp(start, tz="UTC")
    now = pd.Timestamp.now(tz="UTC")
    while t < now:
        end = min(t + pd.Timedelta(days=30), now)
        q = urllib.parse.urlencode({"instrument_name": instrument, "start_timestamp": int(t.timestamp() * 1000),
                                    "end_timestamp": int(end.timestamp() * 1000)})
        for r in _get(f"https://www.deribit.com/api/v2/public/get_funding_rate_history?{q}")["result"]:
            out[pd.Timestamp(r["timestamp"], unit="ms", tz="UTC")] = float(r["interest_8h"])
        t = end
        time.sleep(0.2)
    return pd.Series(out).sort_index()


def cot_net_long(code: str) -> pd.Series:
    """Large speculators' net long as a share of open interest (-1..1). The report is for Tuesday
    but published Friday 15:30 New York, so it counts from Friday 20:00 UTC."""
    q = urllib.parse.urlencode({
        "$select": "report_date_as_yyyy_mm_dd,open_interest_all,noncomm_positions_long_all,"
                   "noncomm_positions_short_all",
        "$where": f"cftc_contract_market_code='{code}' AND report_date_as_yyyy_mm_dd >= '2015-01-01'",
        "$limit": 2000, "$order": "report_date_as_yyyy_mm_dd"})
    rows = _get(f"https://publicreporting.cftc.gov/resource/6dca-aqww.json?{q}")
    s = {}
    for r in rows:
        when = pd.Timestamp(r["report_date_as_yyyy_mm_dd"][:10], tz="UTC") + pd.Timedelta(days=3, hours=20)
        oi = float(r["open_interest_all"])
        s[when] = (float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])) / oi
    return pd.Series(s).sort_index()


def as_of(s: pd.Series, times: pd.DatetimeIndex) -> pd.Series:
    """The latest published value at each time (NaN before the first one)."""
    return s.reindex(s.index.union(times)).ffill().reindex(times)


def download_all(fred_key: str) -> dict[str, int]:
    done = {}
    for name in ("DFII10", "DTWEXBGS"):
        done[name] = save(name, fred(name, fred_key))
    for sym, pair in FUNDING.items():
        done[f"funding_{sym}"] = save(f"funding_{sym}", deribit_funding(pair))
    for sym, code in COT_CODES.items():
        done[f"cot_{sym}"] = save(f"cot_{sym}", cot_net_long(code))
    return done
