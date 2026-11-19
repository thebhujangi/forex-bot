"""Daily market check: for each market the bot trades, how far price is from a signal.

Information only. It never creates signals; it shows the levels the tested trend rule is waiting
for, so a quiet day still tells you what could happen next.
"""
import pandas as pd

from .instruments import SymbolSpec


def levels(d1: pd.DataFrame, entry_days: int = 55) -> dict | None:
    """Last close, the trend rule's breakout levels and how far away they are (in %)."""
    if len(d1) < max(entry_days, 100) + 2:
        return None
    c, h, lo = d1["close"], d1["high"], d1["low"]
    last = float(c.iloc[-1])
    upper = float(h.iloc[-entry_days - 1:-1].max())        # previous N days, as the strategy uses
    lower = float(lo.iloc[-entry_days - 1:-1].min())
    sma100 = float(c.iloc[-100:].mean())
    trend = "up" if last > sma100 and last > c.iloc[-21] else "down" if last < sma100 and last < c.iloc[-21] else "sideways"
    return {"last": last, "day_pct": (last / float(c.iloc[-2]) - 1) * 100, "upper": upper, "lower": lower,
            "to_buy_pct": (upper / last - 1) * 100, "to_sell_pct": (1 - lower / last) * 100, "trend": trend}


def market_line(symbol: str, d1: pd.DataFrame, spec: SymbolSpec, entry_days: int = 55) -> str | None:
    lv = levels(d1, entry_days)
    if lv is None:
        return None
    f = spec.fmt
    arrow = {"up": "📈 uptrend", "down": "📉 downtrend", "sideways": "↔️ sideways"}[lv["trend"]]
    sell = f"SELL if a day closes below {f(lv['lower'])} ({lv['to_sell_pct']:.1f}% lower)"
    buy = f"BUY if a day closes above {f(lv['upper'])} ({lv['to_buy_pct']:.1f}% higher)"
    first, second = (sell, buy) if lv["to_sell_pct"] <= lv["to_buy_pct"] else (buy, sell)
    close = min(lv["to_sell_pct"], lv["to_buy_pct"])
    flag = " 🔔 <b>close to a signal</b>" if close <= 3 else ""
    return (f"<b>{symbol}</b> {f(lv['last'])} ({lv['day_pct']:+.1f}% today), {arrow}{flag}\n"
            f"   Next signal: {first}; or {second}.")


def briefing(lines: list[str]) -> str:
    return ("🧭 <b>Market check</b> (information only, <b>not a signal</b>)\n"
            "No trade today unless a signal message arrives. These are the levels the tested trend "
            "rule is waiting for:\n\n" + "\n".join(lines)
            + "\n\n<i>A signal comes the day a daily candle closes beyond one of these levels.</i>")
