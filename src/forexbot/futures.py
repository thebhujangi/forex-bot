"""Futures signals (MNQ at Interactive Brokers): the message and the follow-ups.

Until IBKR prices are connected the bot reads Elev8's NAS100 CFD, which moves point for point
with the futures but sits at a different price level. So everything is sent as DISTANCES in
points from your own fill ("stop 32.25 pts below your fill"), never as price levels.

Pure logic only: the bot (scripts/run_bot.py) supplies candles and sends the messages.
"""
import html

import pandas as pd

from .assistant import position_size
from .instruments import SymbolSpec
from .models import Signal
from .signals import Source, Tracked, copyable, horizon_line, signal_from_dict

TICK = 0.25          # MNQ moves in quarter points
LATE_MINUTES = 5     # entering one candle late halved the edge in the 2019-23 test


def ticks(points: float) -> float:
    """Round a distance to whole ticks, so it can be typed into IBKR as it is."""
    return max(TICK, round(points / TICK) * TICK)


def pts(x: float) -> str:
    return f"{x:,.2f}"


def contracts(sig: Signal, spec: SymbolSpec, account: float, risk_pct: float) -> tuple[int, float]:
    """Contracts that lose about `risk_pct` of `account` at the stop, and that loss in USD.
    0 contracts if even one would risk more (the loss shown is then for one contract)."""
    size = position_size(spec, account, risk_pct, sig.entry, sig.sl)
    if size["lots"]:
        return int(size["lots"]), size["risk_usd"]
    return 0, size["min_lot_risk_usd"]


def format_futures_signal(sig: Signal, source: Source, spec: SymbolSpec, account: float, risk_pct: float,
                          tz: str, reference: str, levels: bool = False) -> str:
    """The order to place in IBKR. `reference` names the price feed (e.g. "Elev8 NAS100");
    `levels` = the feed IS the futures price, so price levels can be shown too."""
    stop, trail = ticks(sig.stop_distance), ticks(sig.trail_distance)
    n, loss = contracts(sig, spec, account, risk_pct)
    icon, verb = ("🟢", "BUY") if sig.side == "buy" else ("🔴", "SELL")
    below = "below" if sig.side == "buy" else "above"
    late = (sig.created + pd.Timedelta(minutes=LATE_MINUTES)).tz_convert(tz)
    close_by = sig.close_by.tz_convert(tz)
    size_line = (f"Contracts: <b>{n}</b> (lose about ${loss:,.0f} = {risk_pct:g}% of ${account:,.0f} "
                 f"if the stop is hit)" if n else
                 f"Contracts: 1 contract risks ${loss:,.0f}, more than {risk_pct:g}% of ${account:,.0f}. "
                 f"Skip, or take 1 only if you accept that.")
    lines = [f"{icon} <b>{verb} {sig.symbol} NOW (market)</b> (Micro Nasdaq futures)",
             horizon_line(source), f"Confidence: <b>{html.escape(source.tier)}</b>", "",
             size_line,
             f"Stop loss: {copyable(pts(stop))} pts {below} your fill",
             f"Trailing stop: {copyable(pts(trail))} pts",
             f"Close by: <b>{close_by:%H:%M}</b> Malaysia time if still open."]
    if levels:
        sign = sig.direction
        lines.insert(5, f"Entry: about {copyable(spec.fmt(sig.entry))}")
        lines.insert(7, f"(stop level about {spec.fmt(sig.entry - sign * stop)} if you fill at the entry)")
    lines += ["",
              "<b>In IBKR:</b>",
              f"1. {verb.capitalize()} {max(n, 1)} {sig.symbol} at market.",
              f"2. Attach a STOP order {pts(stop)} pts {below} your fill price.",
              f"3. Attach a TRAILING STOP with trailing amount {pts(trail)} pts, in the same OCA group as "
              f"the stop (whichever closes the trade cancels the other).",
              f"Enter within a minute or two. <b>After {late:%H:%M} Malaysia time, skip it</b>: "
              f"entering 5 minutes late halved the edge in testing.",
              "", f"<b>Why:</b> {html.escape(sig.reason)}",
              f"<i>Prices from {html.escape(reference)}.</i>"
              + ("" if levels else " <i>Futures trade at a different level, so use the distances above.</i>"),
              "", f"<i>{html.escape(source.stats)}</i>",
              "<i>Paper-trade first. Past results don't guarantee future ones.</i>"]
    return "\n".join(lines)


def futures_followup(t: Tracked, bars: pd.DataFrame, cost, now: pd.Timestamp) -> list[tuple[str, str]]:
    """Replay the trade through the backtest engine on the candles since the signal and report
    how it ended: stop, trailing stop, or time to close. The orders sit at IBKR, so the stop
    and trailing stop close it there by themselves; only the time exit needs you."""
    from .engine import Bars, simulate
    if not t.active or t.signal is None or not len(bars):
        return []
    sig = signal_from_dict(t.signal)
    name = f"{sig.side.upper()} {t.symbol}"
    res = simulate(sig, Bars.from_df(bars), cost)
    real = [e for e in res.exits if e.reason != "data_end"]
    if not res.filled:
        return []
    if real:
        reason, r, when = real[-1].reason, res.r_multiple, real[-1].time
    elif sig.close_by is not None and now >= sig.close_by:
        # Close time reached: say so now, not when the next candle has closed.
        price = float(bars.close.iloc[-1])
        reason, r, when = "time", sig.direction * (price - res.fill_price) / sig.stop_distance, now
    else:
        return []
    t.status, t.outcome, t.result_r, t.closed_at = "closed", reason, r, when.isoformat()
    text = {
        "sl": f"🛑 <b>Stop hit: {name}</b>, about {r:+.1f}x risk. Your stop order should have closed it in "
              f"IBKR (if not, close it now). Done for today.",
        "trail": f"🔒 <b>Trailing stop closed it: {name}</b>, about {r:+.1f}x risk. Check IBKR shows no open "
                 f"{t.symbol} position. Done for today.",
        "time": f"⏰ <b>Close your {t.symbol} now</b> ({name}): the tested plan ends at "
                f"{sig.close_by.tz_convert('America/New_York'):%H:%M} New York. About {r:+.1f}x risk. "
                f"Cancel the leftover stop and trailing stop orders after closing.",
    }.get(reason, f"🏁 <b>{name} finished</b>, about {r:+.1f}x risk.")
    return [(f"futures:{t.id}:{reason}", text)]


def fresh(sig: Signal, now: pd.Timestamp) -> bool:
    """Still worth sending: the signal is less than LATE_MINUTES old."""
    return now - sig.created <= pd.Timedelta(minutes=LATE_MINUTES)
