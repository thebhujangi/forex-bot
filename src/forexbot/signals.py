"""Live signals: run tested strategies on fresh candles, format the message, then follow each
signal until it's finished (stop moves, stopped out, going badly, trend reversed).

Pure logic only: the bot (scripts/run_bot.py) supplies candles and sends the messages.
"""
import html
import re
import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import filters
from .assistant import position_size
from .instruments import SymbolSpec
from .models import Signal
from .news import Event, relevant
from .strategies import get_strategy


@dataclass
class Source:
    """One signal feed from config.yaml -> signals."""
    name: str
    strategy: str
    symbols: list[str]
    horizon: str            # LONG-TERM | INTRADAY
    tier: str               # e.g. "Proven", "Experimental"
    stats: str              # one plain-English line about the backtest
    params: dict = field(default_factory=dict)


@dataclass
class Tracked:
    """A signal being followed after it was sent."""
    id: str
    source: str
    horizon: str
    symbol: str
    side: str
    entry: float
    sl: float               # original stop
    cur_sl: float           # stop after trailing
    trail_distance: float | None
    tp1: float | None
    created: str            # ISO UTC
    status: str = "new"     # new | placed | skipped | closed
    best: float | None = None
    notified_sl: float | None = None
    warned_loss: bool = False
    result_r: float | None = None   # final result in multiples of the planned risk
    closed_at: str | None = None
    recorded: bool = False          # written to the permanent history file
    placed: bool = False            # you tapped "Placed"
    signal: dict | None = None      # full signal (intraday ones are replayed through the engine)
    seen_exits: int = 0
    fill_notified: bool = False
    outcome: str | None = None      # how it ended: sl, tp2, be, time, trail, reversed, expired...
    flags: list = field(default_factory=list)   # one-off prompts already sent (e.g. "maybe triggered")

    def close(self, exit_price: float, when: pd.Timestamp | None = None, outcome: str = "stop"):
        risk = abs(self.entry - self.sl)
        self.placed = self.placed or self.status == "placed"
        self.status, self.outcome = "closed", outcome
        self.result_r = self.sign * (exit_price - self.entry) / risk if risk else 0.0
        self.closed_at = (when or pd.Timestamp.now(tz="UTC")).isoformat()

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1

    @property
    def active(self) -> bool:
        """Still being followed. Skipped signals are followed too, so the report shows how they'd have done."""
        return self.status in ("new", "placed", "skipped")


def new_signals(source: Source, bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec,
                after: pd.Timestamp | None) -> list[Signal]:
    """Signals created by the newest closed candles (strictly after `after`)."""
    if bars.empty:
        return []
    sigs = get_strategy(source.strategy).generate(bars, symbol, params, spec)
    sigs = filters.apply(sigs, params.get("filter"), symbol, bars)
    last_close = bars.index[-1] + (bars.index[-1] - bars.index[-2] if len(bars) > 1 else pd.Timedelta(0))
    return [s for s in sigs if (after is None or s.created > after) and s.created <= last_close]


def signal_to_dict(sig: Signal) -> dict:
    d = asdict(sig)
    for k in ("created", "expires", "close_by"):
        d[k] = d[k].isoformat() if d[k] is not None else None
    return d


def signal_from_dict(d: dict) -> Signal:
    d = dict(d)
    for k in ("created", "expires", "close_by"):
        d[k] = pd.Timestamp(d[k]) if d.get(k) else None
    return Signal(**d)


def track(sig: Signal, source: Source) -> Tracked:
    return Tracked(id=uuid.uuid4().hex[:8], source=source.name, horizon=source.horizon,
                   symbol=sig.symbol, side=sig.side, entry=sig.entry, sl=sig.sl, cur_sl=sig.sl,
                   trail_distance=sig.trail_distance, tp1=sig.tp1, created=sig.created.isoformat(),
                   best=sig.entry, notified_sl=sig.sl,
                   signal=signal_to_dict(sig) if source.horizon == "INTRADAY" else None)


def format_pair(sigs: list[Signal], source: Source, spec: SymbolSpec, balance: float, risk_pct: float,
                events: list[Event], tz: str, crowd: str | None = None) -> str:
    """Two linked pending orders (buy above / sell below) in one message. Only one will trigger."""
    a, b = sigs
    n = sum(2 if split_lots(s, spec, balance, risk_pct) else 1 for s in sigs)
    lines = [f"🔀 <b>{a.symbol}: place {n} PENDING orders</b>",
             horizon_line(source), f"Confidence: <b>{html.escape(source.tier)}</b>", "",
             "Price will break one way. Only that side will trigger, the other side just waits.",
             "<b>Nothing to close yet.</b> When one side triggers I'll message you to delete the other side.", "",
             f"<b>{a.side.capitalize()} side</b>", *order_lines(a, spec, balance, risk_pct), "",
             f"<b>{b.side.capitalize()} side</b>", *order_lines(b, spec, balance, risk_pct)]
    if a.expires is not None:
        lines += ["", f"Delete them all if nothing has triggered by {a.expires.tz_convert(tz):%H:%M} (Malaysia time). "
                      "I'll remind you."]
    lines += footer_lines(a, source, events, tz, crowd)
    return "\n".join(lines)


def cancel_sibling(filled: Tracked, others: list[Tracked], spec: SymbolSpec, now: pd.Timestamp) -> list[tuple[str, str]]:
    """When one order of a linked pair fills, the other is cancelled (as in the backtest)."""
    group = (filled.signal or {}).get("oco_group")
    alerts = []
    if not group:
        return alerts
    for o in others:
        if o is filled or not o.active or (o.signal or {}).get("oco_group") != group or o.fill_notified:
            continue
        o.placed = o.placed or o.status == "placed"
        loud = o.status != "skipped"
        o.status, o.outcome, o.result_r, o.closed_at = "closed", "oco", 0.0, now.isoformat()
        if loud:
            sig = signal_from_dict(o.signal)
            orders = "orders" if sig.tp2 is not None else "order"
            alerts.append((f"oco:{o.id}", f"❎ <b>Delete your other pending {orders}: {sig.label} {o.symbol}</b> at "
                                          f"{spec.fmt(sig.entry)}.\nThe {filled.side.upper()} side triggered, so the "
                                          f"{sig.side.upper()} side is no longer needed. It never triggered, so "
                                          f"there's no trade to close: in MT5 go to Trade, find it under Orders and "
                                          f"delete it. Keep the {filled.side.upper()} trades open."))
    return alerts


def shifted(bars: pd.DataFrame, by: float) -> pd.DataFrame:
    return bars + by if by else bars


def follow_replay(t: Tracked, bars: pd.DataFrame, cost, spec: SymbolSpec,
                  now: pd.Timestamp, doubt: float = 0.0) -> list[tuple[str, str]]:
    """Intraday signals: replay the order through the backtest engine on the candles since it
    was sent, and report anything new (filled, TP1, TP2, stopped, session over, expired).

    doubt = how far our prices may be from the broker's. Bad news is reported if it may have
    happened (a stop within `doubt` counts as hit); good news only once it surely happened."""
    from .engine import Bars, simulate
    alerts = []
    if not t.active or t.signal is None:
        return alerts
    sig = signal_from_dict(t.signal)
    f = spec.fmt
    name = f"{sig.label} {t.symbol}"
    loud = t.status != "skipped"
    res = simulate(sig, Bars.from_df(bars), cost, doubt) if len(bars) else None

    # Not filled on our prices, but it would have on prices `doubt` higher or lower: ask to check MT5.
    if loud and doubt and res is not None and not res.filled and f"maybe:{t.id}" not in t.flags:
        if any(simulate(sig, Bars.from_df(shifted(bars, d)), cost).filled for d in (doubt, -doubt)):
            t.flags.append(f"maybe:{t.id}")
            alerts.append((f"maybe:{t.id}", f"❔ <b>Check MT5: {name}</b> at {f(sig.entry)} may have just "
                                            f"triggered. Price came within {f(doubt)} of it, and my free prices "
                                            f"can be that far from Elev8's. If it's now an open trade in MT5, keep "
                                            f"its stop loss and take profit as they are: MT5 will close it at one "
                                            f"of them."))

    if res is None or not res.filled:
        expired = (res is not None and res.status in ("expired", "cancelled")) or \
                  (sig.expires is not None and now >= sig.expires)
        if expired:
            t.placed = t.placed or t.status == "placed"
            t.status, t.outcome, t.result_r, t.closed_at = "closed", "expired", 0.0, now.isoformat()
            if loud:
                why = ("price ran to the target without coming back to your entry"
                       if res is not None and res.status == "cancelled" else "price never reached your entry")
                alerts.append((f"expired:{t.id}", f"⌛ <b>Delete your pending {'orders' if sig.tp2 is not None else 'order'}: "
                                                  f"{name}</b> at {f(sig.entry)}.\n"
                                                  f"The setup is over: {why}. No loss, no trade."
                                                  + ("\nIf MT5 did trigger it earlier, keep that trade: its stop "
                                                     "loss and take profit will close it." if f"maybe:{t.id}" in t.flags
                                                     else "")))
        return alerts

    if not t.fill_notified:
        t.fill_notified = True
        if loud:
            alerts.append((f"filled:{t.id}", f"▶️ <b>Order triggered: {name}</b> at about {f(res.fill_price)}. "
                                             f"It's a live trade now. Stop loss: {copyable(f(sig.sl))}.\n"
                                             f"<i>MT5 is always right: my free prices differ a little from Elev8's. "
                                             f"If MT5 closes it, tap the button and I'll stop messaging.</i>"))
    real = [e for e in res.exits if e.reason != "data_end"]
    risk = sig.stop_distance
    by_time = dict(zip(bars.index, zip(bars.low, bars.high)))

    def surely(e) -> bool:
        """Did our prices really reach this stop, or only come within the doubt margin?"""
        low, high = by_time.get(e.time, (None, None))
        if low is None:
            return True
        if sig.direction == 1:
            return low <= e.price + cost.slippage
        return high + cost.spread >= e.price - cost.slippage

    for e in real[t.seen_exits:]:
        r_now = sig.direction * (e.price - res.fill_price) / risk
        text = {
            "tp1": f"💰 <b>First target hit: {name}</b> at {f(e.price)}. Your first-target order should have "
                   f"closed itself in MT5 (if not, close it now). On the other order, move the stop loss to your "
                   f"entry so it can't lose.\nNew stop loss: {copyable(f(res.fill_price))}",
            "tp2": f"🎯 <b>Final target hit: {name}</b> at {f(e.price)}. MT5 should have closed your last order; "
                   f"if not, close it now.",
            "sl": (f"🛑 <b>Stopped out: {name}</b> at {f(e.price)}." if surely(e) else
                   f"🛑 <b>Stop probably hit: {name}</b> at {f(e.price)}. Price came within {f(doubt)} of your "
                   f"stop, and my free prices can be that far from Elev8's. Check MT5: if the trade is gone, it's "
                   f"finished. If it's still open, keep the stop where it is. I've stopped following this one."),
            "be": f"➖ <b>Back to entry: {name}</b>. The rest closed at breakeven ({f(e.price)})"
                  + ("." if surely(e) else ", or nearly did: check MT5. I've stopped following this one."),
            "time": f"⏰ <b>Close now: {name}</b>. The session is over; close at about {f(e.price)} "
                    f"({r_now:+.1f}x risk on what's left).",
        }.get(e.reason, f"{name}: closed at {f(e.price)} ({e.reason}).")
        if loud:
            alerts.append((f"exit:{t.id}:{e.reason}", text))
    t.seen_exits = len(real)

    if sum(e.fraction for e in real) >= 0.999:
        t.placed = t.placed or t.status == "placed"
        t.status, t.outcome = "closed", real[-1].reason
        t.result_r = res.r_multiple
        t.closed_at = real[-1].time.isoformat()
        if loud:
            alerts.append((f"done:{t.id}", f"🏁 <b>{name} finished: {t.result_r:+.1f}x risk</b> in total."))
        return alerts

    price = float(bars.close.iloc[-1])
    against = sig.direction * (res.fill_price - price)
    if loud and not t.warned_loss and not real and against >= 0.75 * risk:
        t.warned_loss = True
        alerts.append((f"loss:{t.id}", f"⚠️ <b>Losing trade: {name}</b>. Down {against / risk * 100:.0f}% of the "
                                       f"planned risk (price {f(price)}, stop {f(sig.sl)}). The tested plan lets "
                                       f"the stop decide; close now only if you'd rather take the smaller loss."))
    return alerts


def copyable(text: str) -> str:
    """Telegram shows <code> text in a box; copy_buttons() also turns it into a copy button."""
    return f"<code>{html.escape(text)}</code>"


SHORT = {"Entry": "Entry", "Stop loss": "SL", "New stop loss": "New SL", "Take profit": "TP",
         "First target": "TP1", "Final target": "TP2"}


def copy_buttons(text: str) -> list[list[tuple[str, dict]]]:
    """One 'copy' button per <code> value in a message, two per row, labelled like
    '🟢 Entry 4193.54' so a two-order message shows which order each number belongs to."""
    buttons, icon = [], ""
    for line in text.splitlines():
        if line[:1] in ("🟢", "🔴"):
            icon = line[0] + " "
        for m in re.finditer(r"(?:([A-Za-z ]+): )?<code>(.*?)</code>", line):
            value = html.unescape(m.group(2))
            label = SHORT.get((m.group(1) or "").strip())
            buttons.append((f"📋 {icon if label else ''}{label + ' ' if label else ''}{value}", {"copy": value}))
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


ORDER_HINTS = {
    ("buy", "market"): "buy now at the current price",
    ("sell", "market"): "sell now at the current price",
    ("buy", "stop"): "pending: triggers if price RISES to the entry",
    ("sell", "stop"): "pending: triggers if price FALLS to the entry",
    ("buy", "limit"): "pending: triggers if price DIPS to the entry",
    ("sell", "limit"): "pending: triggers if price RISES to the entry",
}


def horizon_line(source: Source) -> str:
    return "📅 LONG-TERM (days to weeks)" if source.horizon == "LONG-TERM" else "⚡ INTRADAY (minutes to hours)"


def split_lots(sig: Signal, spec: SymbolSpec, balance: float, risk_pct: float) -> float:
    """Lots for EACH of the two orders of a two-target trade (half the risk each), or 0 if the
    trade has one target or is too small to split. MT5 allows one take profit per order."""
    if sig.tp1 is None or sig.tp2 is None:
        return 0.0
    return position_size(spec, balance, risk_pct / 2, sig.entry, sig.sl)["lots"]


def order_lines(sig: Signal, spec: SymbolSpec, balance: float, risk_pct: float) -> list[str]:
    """The numbers to type into MT5; prices are tap-to-copy."""
    f = spec.fmt
    icon = "🟢" if sig.side == "buy" else "🔴"
    lines = [f"{icon} <b>{sig.label} {sig.symbol}</b> ({ORDER_HINTS[(sig.side, sig.order_type)]})",
             f"Entry: {copyable(f(sig.entry))}",
             f"Stop loss: {copyable(f(sig.sl))} ({f(sig.stop_distance)} away)"]
    half = split_lots(sig, spec, balance, risk_pct)
    if half:
        risk = 2 * position_size(spec, balance, risk_pct / 2, sig.entry, sig.sl)["risk_usd"]
        return lines + [
            f"First target: {copyable(f(sig.tp1))}",
            f"Final target: {copyable(f(sig.tp2))}",
            f"Place it as <b>2 orders of {half:g} lots</b>, both with this entry and stop loss: take profit at "
            f"the first target on one, at the final target on the other (lose ${risk:,.2f} = {risk_pct:g}% "
            f"in total if the stop is hit)."]
    if sig.tp1 is not None and sig.tp2 is not None:      # too few lots to split in two
        lines += [f"Take profit: {copyable(f(sig.tp2))}",
                  f"First target: {f(sig.tp1)}. Too few lots to split, so it's one order; I'll tell you to "
                  f"move the stop to your entry there."]
    elif sig.tp1 is not None:
        lines.append(f"Take profit: {copyable(f(sig.tp1))}")
    if sig.trail_distance:
        lines.append(f"No fixed target: I'll tell you when to move the stop to lock in profit "
                     f"(it follows the best price at {f(sig.trail_distance)} distance).")
    size = position_size(spec, balance, risk_pct, sig.entry, sig.sl)
    if size["lots"]:
        lines.append(f"Lot size: <b>{size['lots']:g}</b> "
                     f"(lose ${size['risk_usd']:,.2f} = {risk_pct:g}% if the stop is hit)")
    else:
        lines.append(f"Lot size: the smallest size ({spec.volume_min:g}) risks ${size['min_lot_risk_usd']:,.2f} "
                     f"({size['min_lot_risk_usd'] / balance * 100:.1f}% of your account), above your {risk_pct:g}%. "
                     f"Use {spec.volume_min:g} only if you accept that, otherwise skip.")
    return lines


def footer_lines(sig: Signal, source: Source, events: list[Event], tz: str, crowd: str | None) -> list[str]:
    lines = ["", f"<b>Why:</b> {html.escape(sig.reason)}"]
    soon = relevant(events, [sig.symbol], sig.created, sig.created + pd.Timedelta(hours=24))
    if soon:
        lines.append("<b>News next 24h:</b> " + "; ".join(
            f"{e.time.tz_convert(tz):%a %H:%M} {e.currency} {html.escape(e.title)}" for e in soon[:4]))
    if crowd:
        lines.append(f"<b>Crowd:</b> {html.escape(crowd)}")
    return lines + ["", f"<i>{html.escape(source.stats)}</i>",
                    "<i>Demo account. Past results don't guarantee future ones.</i>"]


def format_signal(sig: Signal, source: Source, spec: SymbolSpec, balance: float, risk_pct: float,
                  events: list[Event], tz: str, crowd: str | None = None) -> str:
    lines = [*order_lines(sig, spec, balance, risk_pct)]
    lines[1:1] = [horizon_line(source), f"Confidence: <b>{html.escape(source.tier)}</b>", ""]
    return "\n".join(lines + footer_lines(sig, source, events, tz, crowd))


def follow(t: Tracked, highs_lows: pd.DataFrame, price: float, spec: SymbolSpec) -> list[tuple[str, str]]:
    """Update a tracked signal with candles closed since it was sent and the current price.

    Returns (alert_key, message) pairs. Mirrors the backtest: the stop trails the best price
    reached by *closed* candles, and only ever moves in the trade's favour.
    """
    alerts = []
    if not t.active:
        return alerts
    f = spec.fmt
    name = f"{t.side.upper()} {t.symbol} (entry {f(t.entry)})"
    if len(highs_lows):
        extreme = highs_lows["high"].max() if t.sign == 1 else highs_lows["low"].min()
        t.best = max(t.best, extreme) if t.sign == 1 else min(t.best, extreme)
    if t.trail_distance:
        trail = t.best - t.sign * t.trail_distance
        if t.sign * (trail - t.cur_sl) > 0:
            t.cur_sl = trail
    risk = abs(t.entry - t.sl)

    # Stopped out?
    if t.sign * (price - t.cur_sl) <= 0:
        was_skipped = t.status == "skipped"
        t.close(t.cur_sl)
        result = t.result_r
        if was_skipped:
            return alerts          # no ping for a signal you skipped; it still counts in the report
        alerts.append((f"stopped:{t.id}",
            f"🏁 <b>Stop reached: {name}</b>\nPrice hit the stop at {f(t.cur_sl)} "
            f"({'+' if result >= 0 else ''}{result:.1f}x risk). Close the trade if it's still open "
            f"(if you set the stop in MT5 it has already closed). This signal is finished."))
        return alerts

    if t.status == "skipped":
        return alerts              # follow quietly

    # Stop moved enough to tell you (at least a quarter of the trailing distance).
    step = 0.25 * (t.trail_distance or risk)
    if t.status == "placed" and t.sign * (t.cur_sl - t.notified_sl) >= step:
        locked = t.sign * (t.cur_sl - t.entry) / risk if risk else 0
        t.notified_sl = t.cur_sl
        alerts.append((f"trail:{t.id}:{f(t.cur_sl)}",
            f"🔒 <b>Move your stop: {name}</b>\nNew stop loss: {copyable(f(t.cur_sl))}. "
            + (f"That locks in about {locked:.1f}x your risk as profit." if locked > 0
               else "That cuts your possible loss.")))

    # Going badly: down 75% of the risk and still heading toward the stop.
    against = t.sign * (t.entry - price)
    if not t.warned_loss and risk and against >= 0.75 * risk:
        t.warned_loss = True
        alerts.append((f"loss:{t.id}",
            f"⚠️ <b>Losing trade: {name}</b>\nIt's down {against / risk * 100:.0f}% of the planned risk "
            f"(price {f(price)}, stop {f(t.cur_sl)}). The tested plan is to let the stop decide; "
            f"close now only if you'd rather take the smaller loss. Don't move the stop further away."))
    return alerts


def reversal_alert(t: Tracked, new: Signal, spec: SymbolSpec) -> tuple[str, str] | None:
    if not t.active or t.symbol != new.symbol or t.side == new.side:
        return None
    was_skipped = t.status == "skipped"
    t.close(new.entry, new.created, outcome="reversed")
    if was_skipped:
        return None
    return (f"reverse:{t.id}",
            f"🔄 <b>Trend has reversed: close {t.side.upper()} {t.symbol}</b> (entry {spec.fmt(t.entry)}).\n"
            f"A {new.side.upper()} signal just fired on the same market, so the reason for this trade is gone.")


HISTORY_COLUMNS = ["id", "source", "horizon", "symbol", "side", "entry", "sl", "created", "closed_at",
                   "placed", "result_r", "outcome"]


def history_row(t: Tracked) -> dict:
    return {"id": t.id, "source": t.source, "horizon": t.horizon, "symbol": t.symbol, "side": t.side,
            "entry": t.entry, "sl": t.sl, "created": t.created, "closed_at": t.closed_at,
            "placed": t.placed, "result_r": round(t.result_r or 0.0, 3), "outcome": t.outcome}


FEED_NAMES = {"crypto_trend": "Crypto trend (tested)", "fx_gold_trend": "Gold/FX trend",
              "x_crypto_breakout": "⚗️ Crypto breakout", "x_crypto_pullback": "⚗️ Crypto pullback",
              "x_fx_ny_range": "⚗️ Gold/FX NY range"}


def _money(r: float, per_r: float) -> str:
    return f"{'+' if r >= 0 else '−'}${abs(r) * per_r:,.0f}"


def _tally(g: pd.DataFrame, per_r: float) -> str:
    """'12 trades · 5 won, 7 lost · −1.9x risk ≈ −$190'"""
    if g.empty:
        return "no trades"
    won, lost = int((g.result_r > 0.05).sum()), int((g.result_r < -0.05).sum())
    total = g.result_r.sum()
    return (f"{len(g)} trade{'s' if len(g) != 1 else ''} · {won} won, {lost} lost · "
            f"{total:+.1f}x risk ≈ <b>{_money(total, per_r)}</b>")


def weekly_report(history: pd.DataFrame, open_signals: list[tuple[Tracked, float | None]],
                  now: pd.Timestamp, account_week: dict | None = None, per_r: float = 100.0,
                  start_balance: float = 10_000, risk_pct: float = 1.0) -> str:
    """Short report card: would following the signals have made money? Only orders that actually
    triggered count (pending orders that never filled cost nothing). per_r = dollars per 1x risk."""
    week_ago = now - pd.Timedelta(days=7)
    h = history.copy()
    if "outcome" not in h:
        h["outcome"] = ""
    h["outcome"] = h["outcome"].fillna("")
    if not h.empty:
        h["closed_at"] = pd.to_datetime(h["closed_at"], utc=True, format="ISO8601")
        h["created"] = pd.to_datetime(h["created"], utc=True, format="ISO8601")
    traded = h[~h.outcome.isin(["expired", "oco"])]
    never = len(h) - len(traded)
    week = traded[traded.closed_at >= week_ago] if not traded.empty else traded
    since = f"{h.created.min():%d %b}" if not h.empty else f"{now:%d %b}"

    lines = [f"📊 <b>Report card</b> ({week_ago:%d %b} to {now:%d %b})", "",
             f"If you had taken <b>every</b> signal ({risk_pct:g}% risk each on ${start_balance:,.0f}):",
             f"• This week: {_tally(week, per_r)}",
             f"• Since {since}: {_tally(traded, per_r)}", "", "<b>By feed, since the start:</b>"]
    for src, g in sorted(traded.groupby("source"), key=lambda x: -x[1].result_r.sum()):
        lines.append(f"• {FEED_NAMES.get(src, src)}: {_tally(g, per_r)}")
    if traded.empty:
        lines.append("• No trades have triggered yet.")
    placed = traded[traded.placed.astype(bool)] if not traded.empty else traded
    lines += ["", f"<b>The ones you marked ✅ Placed:</b> {_tally(placed, per_r)}"]
    if never:
        lines.append(f"{never} pending order(s) never triggered: no gain, no loss.")

    live = [(t, cur) for t, cur in open_signals if t.fill_notified or t.horizon == "LONG-TERM"]
    if live:
        lines += ["", "<b>Still open:</b>"]
        for t, cur in live[:8]:
            lines.append(f"• {t.side.upper()} {t.symbol} since {pd.Timestamp(t.created):%d %b}"
                         + (f": {cur:+.1f}x risk now" if cur is not None else ""))
    if account_week:
        lines += ["", f"<b>Your MT5 demo this week:</b> {account_week['trades']} closed trade(s), "
                      f"${account_week['profit']:+,.2f} · balance ${account_week['balance']:,.2f}"]
    lines += ["", "<i>Backtests expected: crypto trend about +0.26x per trade; the ⚗️ experimental feeds "
                  "-0.04x to -0.16x. Judge a feed after 20+ trades. Results are worked out from free prices "
                  "matched to Elev8's; your MT5 history is the final word.</i>"]
    return "\n".join(lines)


def to_dict(t: Tracked) -> dict:
    return asdict(t)


def from_dict(d: dict) -> Tracked:
    return Tracked(**d)
