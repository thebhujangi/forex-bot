"""Where the bot gets prices: MT5 on your PC, or free web APIs in the cloud.

Both give the same thing: closed candles in UTC (index = candle open time) and a current price.
Daily candles are built from hourly ones with the day rolling over at 17:00 New York, exactly
like Elev8's MT5 server, so live signals match the backtests.

Cloud sources (free):
  * Crypto: Coinbase Exchange public candles (no key).
  * Forex & gold: Twelve Data (free key in TWELVEDATA_API_KEY; 800 calls/day, 8/minute).
Prices from these differ from Elev8's (BTC by ~$30, gold by ~$1-2). scripts/calibrate_feeds.py
measures the gap against MT5 and saves it to feed_calibration.json; the cloud then shifts the free
prices onto Elev8's bid and knows how far off they may still be (the "doubt" margin).
"""
import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from . import PROJECT_ROOT
from .timeutils import NEW_YORK, ROLLOVER_SHIFT, TIMEFRAME_MINUTES, trading_day

CALIBRATION_FILE = PROJECT_ROOT / "feed_calibration.json"

COINBASE = {"BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "XRPUSD": "XRP-USD", "SOLUSD": "SOL-USD",
            "LTCUSD": "LTC-USD", "DOGEUSD": "DOGE-USD", "ADAUSD": "ADA-USD"}
TWELVE = {"XAUUSD": "XAU/USD", "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
          "AUDUSD": "AUD/USD", "NZDUSD": "NZD/USD", "USDCAD": "USD/CAD", "USDCHF": "USD/CHF"}
COINBASE_GRAN = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 21600, "D1": 86400}
TWELVE_INTERVAL = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h"}


class FeedError(RuntimeError):
    pass


def _get_json(url: str, tries: int = 3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == tries - 1:
                raise FeedError(f"{url.split('?')[0]}: {e}") from None
            time.sleep(2 * (attempt + 1))


def only_closed(df: pd.DataFrame, tf: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = now or pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf]) <= now]


def daily_from_hourly(h1: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Elev8-style daily candles (day = 17:00 to 17:00 New York), completed days only."""
    if h1.empty:
        return h1
    now = now or pd.Timestamp.now(tz="UTC")
    days = pd.Series(h1.index.tz_convert(NEW_YORK) + ROLLOVER_SHIFT).dt.date.to_numpy()
    g = h1.groupby(days)
    d1 = pd.DataFrame({"time": g.apply(lambda x: x.index[0]), "open": g.open.first(), "high": g.high.max(),
                       "low": g.low.min(), "close": g.close.last(), "hours": g.size()})
    d1 = d1[(d1.hours >= 6) & (pd.Index(d1.index) < trading_day(now))]
    return d1.set_index("time").drop(columns="hours").rename_axis(None)


def load_calibration() -> dict:
    """{symbol: {low, high, close, doubt}}, all as fractions of the price. Empty if never measured."""
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8")).get("symbols", {})
    except (OSError, ValueError):
        return {}


def to_broker(df: pd.DataFrame, cal: dict | None) -> pd.DataFrame:
    """Shift free-feed candles onto the broker's bid prices using the measured gaps."""
    if not cal or df.empty:
        return df
    out = df.copy()
    out["open"] = df.open * (1 + cal["close"])
    out["close"] = df.close * (1 + cal["close"])
    out["high"] = (df.high * (1 + cal["high"])).combine(out[["open", "close"]].max(axis=1), max)
    out["low"] = (df.low * (1 + cal["low"])).combine(out[["open", "close"]].min(axis=1), min)
    return out


class ApiMarket:
    """Free web APIs. Used in the cloud, where MT5 isn't available."""
    has_account = False

    def __init__(self, twelvedata_key: str = "", calibration: dict | None = None):
        self.key = twelvedata_key
        self.calibration = load_calibration() if calibration is None else calibration
        self._cache: dict = {}
        self._last_call = 0.0

    def doubt(self, symbol: str, price: float) -> float:
        """How far our (shifted) prices may still be from Elev8's, in price units."""
        cal = self.calibration.get(symbol)
        return price * cal["doubt"] if cal else 0.0

    def raw_bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        """Unshifted candles straight from the free source (for calibration)."""
        if symbol in COINBASE:
            return only_closed(self._coinbase(symbol, tf, count), tf)
        if symbol in TWELVE:
            return only_closed(self._twelve(symbol, tf, count), tf)
        raise FeedError(f"No free price source for {symbol}")

    # ---- Coinbase ----
    def _coinbase(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        gran = COINBASE_GRAN[tf]
        end = pd.Timestamp.now(tz="UTC")
        rows = []
        while len(rows) < count:
            start = end - pd.Timedelta(seconds=gran * 300)
            q = urllib.parse.urlencode({"granularity": gran, "start": start.isoformat(), "end": end.isoformat()})
            batch = _get_json(f"https://api.exchange.coinbase.com/products/{COINBASE[symbol]}/candles?{q}")
            if not batch:
                break
            rows += batch
            end = start
            time.sleep(0.2)
        df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"]).drop_duplicates("t")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), unit="s", utc=True)).as_unit("ns")
        return df[["open", "high", "low", "close"]].astype(float).sort_index().tail(count)

    # ---- Twelve Data ----
    def _twelve(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        if not self.key:
            raise FeedError("No TWELVEDATA_API_KEY: forex/gold prices unavailable in the cloud.")
        wait = 8.0 - (time.time() - self._last_call)       # stay under 8 calls/minute
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        q = urllib.parse.urlencode({"symbol": TWELVE[symbol], "interval": TWELVE_INTERVAL[tf],
                                    "outputsize": min(count, 5000), "timezone": "UTC", "apikey": self.key})
        d = _get_json(f"https://api.twelvedata.com/time_series?{q}")
        if d.get("status") != "ok":
            raise FeedError(f"Twelve Data {symbol}: {d.get('message', 'error')}")
        df = pd.DataFrame(d["values"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("datetime"), utc=True)).as_unit("ns")
        return df[["open", "high", "low", "close"]].astype(float).sort_index()

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        """Closed candles. Daily candles are built from hourly ones (Elev8 day boundaries)."""
        if tf == "D1":
            h1 = self.bars(symbol, "H1", min(count * 24 + 48, 5000))
            return daily_from_hourly(h1).tail(count)
        key = (symbol, tf)
        cached = self._cache.get(key)
        step = pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])
        now = pd.Timestamp.now(tz="UTC")
        # Re-fetch only when a new candle has closed since the last fetch.
        if cached is not None and len(cached[1]) >= count and cached[1].index[-1] + 2 * step > now:
            return cached[1].tail(count)
        df = to_broker(self.raw_bars(symbol, tf, count), self.calibration.get(symbol))
        self._cache[key] = (now, df)
        return df.tail(count)

    def price(self, symbol: str, side: str) -> float | None:
        """Latest price we know (close of the newest 15-minute candle)."""
        try:
            b = self.bars(symbol, "M15", 4)
            return float(b.close.iloc[-1]) if len(b) else None
        except FeedError:
            return None


class Mt5Market:
    """Elev8 prices and your account, from the MT5 terminal on this PC (read-only)."""
    has_account = True

    def __init__(self, mt5, names: dict[str, str], server_time: str):
        from . import mt5_client
        self.mt5, self.names, self.server_time, self._m = mt5, names, server_time, mt5_client

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        return self._m.recent_bars(self.mt5, self.names[symbol], tf, count, self.server_time)

    def price(self, symbol: str, side: str) -> float | None:
        return self._m.side_price(self.mt5, self.names[symbol], side)

    def doubt(self, symbol: str, price: float) -> float:
        return 0.0          # these are Elev8's own prices
