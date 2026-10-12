"""Free live headlines (RSS) filtered to your markets, with a 'big news' test for push alerts.

Feeds are polled every 10 minutes. A headline is *relevant* if it mentions one of your markets,
and *big* if it also mentions something that typically moves prices hard (rate decisions,
inflation, war, hacks, ETF decisions...). Only big + relevant headlines are pushed.
"""
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import pandas as pd

FEEDS = {
    "FXStreet": "https://www.fxstreet.com/rss/news",
    "FXStreet crypto": "https://www.fxstreet.com/rss/crypto",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
}
ALWAYS_BIG = {"Federal Reserve"}       # official Fed releases always matter

USD_WORDS = r"fed|fomc|powell|cpi|inflation|non-?farm|payrolls|nfp|treasur|dollar|usd|tariff|recession"
MARKET_WORDS = {
    "XAUUSD": r"gold|xau|bullion|precious metal",
    "EURUSD": r"eur\b|euro|ecb|lagarde|eurozone",
    "GBPUSD": r"gbp|pound|sterling|boe\b|bank of england|bailey",
    "BTCUSD": r"bitcoin|btc",
    "ETHUSD": r"ether\b|ethereum|eth\b",
    "XRPUSD": r"xrp|ripple",
    "SOLUSD": r"solana|\bsol\b",
    "BNBUSD": r"\bbnb\b|binance coin",
    "LTCUSD": r"litecoin|\bltc\b",
    "DOGEUSD": r"dogecoin|\bdoge\b",
    "ADAUSD": r"cardano|\bada\b",
}
CRYPTO = {"BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "BNBUSD", "LTCUSD", "DOGEUSD", "ADAUSD"}
BIG_WORDS = (r"rate decision|rate cut|rate hike|cuts rates|hikes rates|raises rates|holds rates|fomc|powell|"
             r"\bcpi\b|non-?farm|payrolls|emergency|war\b|invasion|missile|sanction|default|tariff|"
             r"hack|exploit|etf (approv|reject|inflow|outflow)|sec (sue|charge|approv)|lawsuit|ban\b|"
             r"crash|plunge|collapse|soar|surge|record high|all-time high|liquidat|bankrupt|halt")


@dataclass
class Headline:
    source: str
    title: str
    link: str
    time: pd.Timestamp
    markets: list[str]
    big: bool

    @property
    def key(self) -> str:
        return self.link or self.title


def classify(source: str, title: str, watched: list[str]) -> tuple[list[str], bool]:
    t = title.lower()
    markets = [s for s in watched if s in MARKET_WORDS and re.search(MARKET_WORDS[s], t)]
    if not markets and (source in ALWAYS_BIG or re.search(USD_WORDS, t)):
        markets = ["USD"]          # US news moves gold, the USD pairs and crypto alike
    big = source in ALWAYS_BIG or bool(re.search(BIG_WORDS, t))
    return markets, big and bool(markets)


def parse_feed(source: str, raw: bytes, watched: list[str]) -> list[Headline]:
    out = []
    for item in ET.fromstring(raw).findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        try:
            when = pd.Timestamp(parsedate_to_datetime(item.findtext("pubDate") or "")).tz_convert("UTC")
        except (TypeError, ValueError):
            when = pd.Timestamp.now(tz="UTC")
        markets, big = classify(source, title, watched)
        if markets:
            out.append(Headline(source, title, (item.findtext("link") or "").strip(), when, markets, big))
    return out


def fetch_all(watched: list[str]) -> list[Headline]:
    items = []
    for source, url in FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                items += parse_feed(source, r.read(), watched)
        except Exception:
            continue      # one feed down shouldn't stop the others
    return sorted(items, key=lambda h: h.time, reverse=True)
