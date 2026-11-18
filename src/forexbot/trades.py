"""Link your real MT5 trades to the bot's signals, and notice when you close them.

Pure logic (no MT5 here): run_bot.py feeds in open positions and closed-trade history.
A trade belongs to a signal when it's the same symbol and direction, opened no earlier than
10 minutes before the signal was sent and within 30 days of it; among several candidates the
one whose entry is closest to the trade's price wins (several orders may share one signal).
"""
import pandas as pd

from .signals import Tracked

EARLY = pd.Timedelta(minutes=10)     # MT5 and the signal's candle clocks can differ a little
WINDOW = pd.Timedelta(days=30)


def match_signal(symbol: str, side: str, opened: pd.Timestamp | None, price: float,
                 tracked: list[Tracked]) -> Tracked | None:
    best, best_gap = None, None
    for t in tracked:
        if not t.active or t.symbol != symbol or t.side != side:
            continue
        created = pd.Timestamp(t.created)
        if opened is not None and not (created - EARLY <= opened <= created + WINDOW):
            continue
        gap = abs(t.entry - price) / t.entry if t.entry else 0
        if best is None or gap < best_gap:
            best, best_gap = t, gap
    return best


def link_message(symbol: str, side: str, lots: float, price: float, t: Tracked | None, fmt) -> str:
    what = f"{side.upper()} {symbol} {lots:g} lots at {fmt(price)}"
    if t is None:
        return f"👀 <b>New MT5 trade: {what}</b>. Not from one of my signals; I'll tell you when it closes."
    return (f"🔗 <b>Linked your MT5 trade: {what}</b> to my signal from {pd.Timestamp(t.created):%d %b} "
            f"(entry {fmt(t.entry)}). I'll keep you posted and tell you when it closes.")


def closed_message(h: dict, t: Tracked | None, still_open: int, fmt) -> str:
    """h: one entry of mt5_client.position_history (a fully closed trade)."""
    money = f"{'+' if h['profit'] >= 0 else '−'}${abs(h['profit']):,.2f}"
    lines = [f"✅ <b>Closed in MT5: {h['side'].upper()} {h['symbol']}</b> {h['lots']:g} lots",
             f"Opened {fmt(h['open_price'])} → closed {fmt(h['close_price'])} · result <b>{money}</b>"]
    if t is not None:
        risk = abs(t.entry - t.sl)
        r = t.sign * (h["close_price"] - h["open_price"]) / risk if risk else 0.0
        lines.append(f"That's {r:+.1f}x the planned risk on the signal from {pd.Timestamp(t.created):%d %b}.")
        lines.append("Your other order for this signal is still open; I'm still following it."
                     if still_open else "I've stopped following this signal: no more stop updates for it.")
    return "\n".join(lines)
