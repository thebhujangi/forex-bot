"""Daily market briefing: what each market is doing and how close it is to a signal.

Read-only commentary for the morning message. It never creates signals; it explains the state the
tested trend rule is watching, so quiet days still show what the bot sees.
"""
import pandas as pd

from .instruments import SymbolSpec


def market_line(symbol: str, d1: pd.DataFrame, spec: SymbolSpec, entry_days: int = 55, atr_period: int = 20,
                watched: bool = True) -> str | None:
    """One line: trend, volatility vs normal, yesterday's move, and the breakout levels a signal needs."""
    if len(d1) < max(entry_days, 100) + 2:
        return None
    f = spec.fmt
    c, h, lo = d1["close"], d1["high"], d1["low"]
    prev = c.shift(1)
    tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    last, atr_now = float(c.iloc[-1]), float(atr.iloc[-1])
    vol_ratio = atr_now / float(atr.iloc[-100:].median())
    upper = float(h.iloc[-entry_days - 1:-1].max())       # previous N days, as the strategy uses
    lower = float(lo.iloc[-entry_days - 1:-1].min())
    sma100 = float(c.iloc[-100:].mean())
    day = (last / float(c.iloc[-2]) - 1) * 100
    trend = "📈 uptrend" if last > sma100 and last > c.iloc[-21] else "📉 downtrend" if last < sma100 and last < c.iloc[-21] \
        else "↔️ sideways"
    vol = "very jumpy" if vol_ratio > 1.5 else "jumpy" if vol_ratio > 1.15 else "calm" if vol_ratio < 0.85 else "normal"
    up_gap, dn_gap = (upper - last) / atr_now, (last - lower) / atr_now
    if not watched:
        near = ""
    elif up_gap <= 0 or dn_gap <= 0:
        near = " · at a breakout level"
    elif min(up_gap, dn_gap) < 1:
        side, lvl = ("buy", upper) if up_gap < dn_gap else ("sell", lower)
        near = f" · <b>near a {side} signal</b> (daily close beyond {f(lvl)})"
    else:
        near = f" · signal levels {f(lower)} / {f(upper)}"
    return f"<b>{symbol}</b> {f(last)} ({day:+.1f}%) {trend}, {vol}{near}"


def briefing(lines: list[str]) -> str:
    return ("📊 <b>Markets today</b> (daily candles)\n" + "\n".join(lines)
            + "\n<i>Signals only come from the tested trend rule (a daily close beyond the levels shown). "
              "'Near' means within one normal day's move.</i>")
