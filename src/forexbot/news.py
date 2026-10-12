"""Economic calendar from the free Forex Factory weekly feed (no key needed).

The feed is refreshed about once an hour by Forex Factory, so it's cached for an hour here too;
please don't poll it more often.
"""
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import DATA_DIR

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE = DATA_DIR / "news_cache.json"
CACHE_SECONDS = 3600

# Which currencies' news moves each symbol.
SYMBOL_CURRENCIES = {
    "XAUUSD": {"USD"}, "EURUSD": {"USD", "EUR"}, "GBPUSD": {"USD", "GBP"},
    "USDJPY": {"USD", "JPY"}, "AUDUSD": {"USD", "AUD"}, "NZDUSD": {"USD", "NZD"},
    "USDCAD": {"USD", "CAD"}, "USDCHF": {"USD", "CHF"},
    # Crypto reacts mostly to US news (inflation, Fed rates, jobs).
    "BTCUSD": {"USD"}, "ETHUSD": {"USD"}, "XRPUSD": {"USD"}, "SOLUSD": {"USD"},
}


@dataclass
class Event:
    time: pd.Timestamp      # UTC
    currency: str
    title: str
    impact: str             # High | Medium | Low | Holiday
    forecast: str
    previous: str

    @property
    def key(self) -> str:
        return f"{self.time:%Y%m%d%H%M}:{self.currency}:{self.title}"


def parse_events(raw: list[dict]) -> list[Event]:
    events = []
    for e in raw:
        try:
            t = pd.Timestamp(e["date"]).tz_convert("UTC")
        except (KeyError, ValueError, TypeError):
            continue
        events.append(Event(t, e.get("country", ""), e.get("title", ""), e.get("impact", ""),
                            e.get("forecast", "") or "", e.get("previous", "") or ""))
    return sorted(events, key=lambda ev: ev.time)


def fetch_events(cache: Path = CACHE, max_age: int = CACHE_SECONDS) -> list[Event]:
    cache = Path(cache)
    if cache.exists():
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if time.time() - stored.get("fetched_at", 0) < max_age:
            return parse_events(stored["events"])
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())
        cache.write_text(json.dumps({"fetched_at": time.time(), "events": raw}), encoding="utf-8")
        return parse_events(raw)
    except Exception:
        if cache.exists():  # stale is better than nothing
            return parse_events(json.loads(cache.read_text(encoding="utf-8"))["events"])
        raise


def relevant(events: list[Event], symbols, start: pd.Timestamp, end: pd.Timestamp,
             impacts=("High",)) -> list[Event]:
    currencies = set().union(*(SYMBOL_CURRENCIES.get(s, set()) for s in symbols))
    return [e for e in events if e.currency in currencies and e.impact in impacts and start <= e.time < end]
