"""What news usually does to your markets, in plain words.

Three sources, none of which chooses a trade:
  * news_impact.json: how far each market really moved after scheduled US releases 2019-2026 on
    Elev8's prices (scripts/research_news_impact.py). Result: releases change HOW FAR price moves
    (2-7x normal), not reliably which way; following or fading the first move lost money.
  * RELEASE_NOTES: the textbook reaction to a higher-than-expected number.
  * headline_effects: the textbook effect of a headline, from keywords. A rough guide: it can't tell
    whether the market already expected the news, and it has no track record (no free archive of
    past headlines to test on), so it is shown and logged with every signal, never used to pick one.
"""
import html
import json
import re
from pathlib import Path

import pandas as pd

from .headlines import Headline
from .instruments import SymbolSpec
from .news import Event

IMPACT_FILE = Path(__file__).with_name("news_impact.json")
NAMES = {"XAUUSD": "Gold", "XAGUSD": "Silver", "XBRUSD": "Brent oil", "XTIUSD": "WTI oil", "BTCUSD": "Bitcoin",
         "ETHUSD": "Ethereum", "XRPUSD": "XRP", "SPX500": "S&P 500", "NAS100": "Nasdaq", "EURUSD": "EUR/USD",
         "GBPUSD": "GBP/USD"}
GROUPS = {"gold": ["XAUUSD", "XAGUSD"], "oil": ["XBRUSD", "XTIUSD"], "crypto": ["BTCUSD", "ETHUSD", "XRPUSD"],
          "stocks": ["SPX500", "NAS100"], "euro": ["EURUSD"], "pound": ["GBPUSD"]}

# Forex Factory title -> release measured in news_impact.json
RELEASES = ((r"^(core )?cpi", "CPI"), (r"non-?farm|average hourly earnings|^unemployment rate", "Non-Farm Payrolls"),
            (r"core pce", "Core PCE"), (r"ppi", "PPI"), (r"retail sales", "Retail Sales"), (r"\bgdp\b", "GDP"),
            (r"federal funds|fomc", "FOMC rate decision"), (r"crude oil inventories", "EIA crude oil stocks"))

RELEASE_NOTES = {
    "CPI": "Higher than forecast usually means a stronger dollar and weaker gold, stocks and crypto (rate cuts look further away). Lower: the opposite.",
    "Core PCE": "Higher than forecast usually means a stronger dollar and weaker gold, stocks and crypto. Lower: the opposite.",
    "PPI": "Higher than forecast usually leans towards a stronger dollar and weaker gold, stocks and crypto.",
    "Non-Farm Payrolls": "Strong jobs usually mean a stronger dollar and weaker gold; stocks and crypto can go either way.",
    "Retail Sales": "Strong spending usually leans towards a stronger dollar and weaker gold.",
    "GDP": "Strong growth usually leans towards a stronger dollar and higher oil.",
    "FOMC rate decision": "A higher rate or tougher tone usually means a stronger dollar and weaker gold, stocks and crypto. A softer tone: the opposite.",
    "EIA crude oil stocks": "Stocks falling more than expected usually lifts oil; stocks rising more than expected usually pushes it down.",
}

_UP = {"gold": 1, "crypto": 1, "stocks": 1, "euro": 1, "pound": 1}
_DOWN = {k: -v for k, v in _UP.items()}
MONEY = ("easier money", "tighter money", "cooler inflation", "hotter inflation")
US = re.compile(r"\bfed\b|fomc|powell|\bu\.?s\.?\b|american|treasur", re.I)
EURO = re.compile(r"\becb\b|lagarde|euro ?zone|euro area|german|french|italian|spanish", re.I)
POUND = re.compile(r"\bboe\b|bank of england|bailey|\buk\b|brit(ain|ish)|\bmann\b", re.I)
OTHER = re.compile(r"\bboc\b|\brba\b|\bboj\b|rbnz|\bsnb\b|pboc|riksbank|norges|canad|austral|japan|new zealand|swiss|"
                   r"chin(a|ese)|india|brazil|turk|mexic|korea|russia|poland|swed|norw|peso|rupee|\byen\b|yuan", re.I)


def money_effects(title: str, eff: dict[str, int]) -> dict[str, int]:
    """Rate and inflation news moves the currency of its own country: the Fed moves everything here,
    the ECB the euro, the Bank of England the pound; other central banks none of these markets."""
    if US.search(title):
        return eff
    if EURO.search(title):
        return {"euro": -eff["euro"]}
    if POUND.search(title):
        return {"pound": -eff["pound"]}
    return {} if OTHER.search(title) else eff
EFFECTS = (
    # central banks and inflation
    (r"rate cut|cuts? (interest )?rates|dovish|easing cycle", _UP, "easier money"),
    (r"rate hike|hikes? (interest )?rates|raises? (interest )?rates|hawkish|higher for longer", _DOWN, "tighter money"),
    (r"inflation (cool|eas|slow|fall|drop|declin)|(cool|soft)(er|ing)? (than expected )?(cpi|inflation)", _UP, "cooler inflation"),
    (r"inflation (ris|jump|accelerat|surg|heat)|hot(ter)? (than expected )?(cpi|inflation)", _DOWN, "hotter inflation"),
    # risk
    (r"ceasefire|peace (deal|talks|agreement)|de-?escalat|truce", {"gold": -1, "oil": -1, "stocks": 1, "crypto": 1}, "calmer politics"),
    (r"(?<!price )(?<!trade )(?<!bidding )\bwar\b|missile|air ?strike|invasion|invades|attack(s|ed)? (on )?(iran|israel|ukraine|russia|tanker|ship|oil)|escalat|strait of hormuz",
     {"gold": 1, "oil": 1, "stocks": -1, "crypto": -1}, "war risk"),
    (r"tariffs? (cut|delay|pause|lifted|rollback)|(delay|pause|cut|lift)s? tariff|trade deal", {"stocks": 1, "crypto": 1, "oil": 1, "gold": -1}, "trade relief"),
    (r"tariff", {"stocks": -1, "crypto": -1, "gold": 1}, "tariff worry"),
    (r"recession|mass layoffs|bank (failure|collapse)|credit downgrade", {"gold": 1, "stocks": -1, "oil": -1, "crypto": -1}, "economic worry"),
    # oil
    (r"opec\S* .{0,40}(output|production|supply).{0,15}(cut|extend)|(cut|extend)\S* .{0,25}opec|output cut", {"oil": 1}, "less oil supply"),
    (r"opec\S* .{0,40}(rais|increas|boost|hike)|output (hike|increase|boost)", {"oil": -1}, "more oil supply"),
    (r"(crude|oil) (inventor|stock|stockpile)\S* .{0,15}(draw|fall|fell|drop|declin|shrink)", {"oil": 1}, "oil stocks falling"),
    (r"(crude|oil) (inventor|stock|stockpile)\S* .{0,15}(build|ris|rose|jump|surg|grow)", {"oil": -1}, "oil stocks rising"),
    # crypto
    (r"etf .{0,20}(inflow|approv)|(approv|inflow)\S* .{0,25}etf|bitcoin reserve", {"crypto": 1}, "crypto demand"),
    (r"etf .{0,20}(outflow|reject)|(hack|exploit)\w*|sec (sue|charge)|crypto ban|exchange (halt|collapse)", {"crypto": -1}, "crypto worry"),
    # dollar
    (r"(?<!canadian )(?<!australian )(?<!zealand )dollar (rall|ris|strengthen|surg|jump|climb|firm)|stronger (us )?dollar", {"gold": -1, "euro": -1, "pound": -1}, "stronger dollar"),
    (r"(?<!canadian )(?<!australian )(?<!zealand )dollar (fall|slip|slid|weaken|drop|tumbl|sink)|weaker (us )?dollar", {"gold": 1, "euro": 1, "pound": 1}, "weaker dollar"),
)


def impact() -> dict:
    try:
        return json.loads(IMPACT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def release_of(title: str) -> str | None:
    t = title.lower()
    return next((name for rx, name in RELEASES if re.search(rx, t)), None)


def measured(symbol: str, event: Event) -> dict | None:
    """What this release did to this market 2019-2026, or None (not a US release we measured)."""
    name = release_of(event.title)
    if name is None or event.currency not in ("USD", "All"):
        return None
    return impact().get(symbol, {}).get(name)


def impact_text(symbol: str, event: Event) -> str | None:
    m = measured(symbol, event)
    if not m or not m.get("times_normal"):
        return None
    size = f"{m['times_normal']:g}x a normal 15 minutes" if m["times_normal"] >= 1.2 else "about a normal 15 minutes"
    return f"{NAMES.get(symbol, symbol)} typically moves {m['move15_pct']:.2f}% in the first 15 min ({size})"


def headline_effects(title: str) -> tuple[dict[str, int], list[str]]:
    """{market group: +1 usually up / -1 usually down} and the plain-word reasons."""
    t, out, why = title.lower(), {}, []
    for rx, eff, reason in EFFECTS:
        if re.search(rx, t):
            if reason == "tariff worry" and "trade relief" in why:
                continue
            if reason == "war risk" and "calmer politics" in why:
                continue
            if reason in MONEY:
                eff = money_effects(title, eff)
                if not eff:
                    continue
            for g, d in eff.items():
                out[g] = out.get(g, 0) + d
            why.append(reason)
    return {g: (1 if d > 0 else -1) for g, d in out.items() if d}, why


def effect_line(title: str) -> str:
    """'Usually: gold ↑, stocks ↓ (war risk)', or ''."""
    eff, why = headline_effects(title)
    if not eff:
        return ""
    return "Usually: " + ", ".join(f"{g} {'↑' if d > 0 else '↓'}" for g, d in eff.items()) + f" ({', '.join(why)})"


def lean(symbol: str, headlines: list[Headline], now: pd.Timestamp, hours: float = 6) -> tuple[int, list[tuple[str, int]]]:
    """Net lean of recent headlines for one market, and the headlines behind it (deduplicated)."""
    groups = [g for g, s in GROUPS.items() if symbol in s]
    out, seen = [], set()
    for h in headlines:
        key = re.sub(r"\W+", " ", h.title.lower()).strip()[:60]
        if h.time < now - pd.Timedelta(hours=hours) or key in seen:
            continue
        eff, _ = headline_effects(h.title)
        d = next((eff[g] for g in groups if g in eff), 0)
        if d:
            seen.add(key)
            out.append((h.title, d))
    return sum(d for _, d in out), out


def lean_text(symbol: str, side: str, headlines: list[Headline], now: pd.Timestamp) -> str:
    """One line for a signal message: what the last 6 hours of headlines usually mean for it."""
    net, items = lean(symbol, headlines, now)
    if not items:
        return "no recent headline with a usual effect on this market"
    if net == 0:
        return f"{len(items)} recent headlines, pulling both ways"
    agree = "agrees with" if (net > 0) == (side == "buy") else "goes against"
    example = next(t for t, d in items if (d > 0) == (net > 0))
    return (f"{len(items)} recent headline{'s' if len(items) > 1 else ''} usually push{'es' if len(items) == 1 else ''} it "
            f"{'up' if net > 0 else 'down'}, which {agree} this {side.upper()}: \"{example[:90]}\"")


def reaction_lines(events: list[Event], moves: dict[str, float]) -> list[str]:
    """15 minutes after a release: how far each market moved against what is typical for it."""
    rows = []
    for sym, mv in sorted(moves.items(), key=lambda kv: -abs(kv[1])):
        m = next((x for x in (measured(sym, e) for e in events) if x), None)
        ref = f" ({abs(mv) / m['move15_pct']:.1f}x its typical move for this release)" if m and m["move15_pct"] else ""
        rows.append(f"• {NAMES.get(sym, sym)} {mv:+.2f}%{ref}")
    return rows


def clean(title: str) -> str:
    return html.escape(re.sub(r"\s+", " ", title).strip())
