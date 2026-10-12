"""Live signals: run tested strategies on fresh candles, format the message, then follow each
signal until it's finished (stop moves, stopped out, going badly, trend reversed).

Pure logic only: the bot (scripts/run_bot.py) supplies candles and sends the messages.
"""
import html
import uuid
from dataclasses import asdict, dataclass, field

import pandas as pd

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
    """Two linked orders (buy above / sell below): one message, one cancels the other."""
    first = format_signal(sigs[0], source, spec, balance, risk_pct, events, tz, crowd)
    f = spec.fmt
    lines = ["🔀 <b>TWO ORDERS: place both. When one fills, cancel the other</b> (I'll remind you).", "", first,
             "", f"<b>Second order: {sigs[1].label} {sigs[1].symbol}</b>",
             f"Entry: <b>{f(sigs[1].entry)}</b> · Stop loss: <b>{f(sigs[1].sl)}</b>"
             + (f" · Take profit: {f(sigs[1].tp1)}" if sigs[1].tp1 else "")
             + (f", then {f(sigs[1].tp2)}" if sigs[1].tp2 else "")]
    size = position_size(spec, balance, risk_pct, sigs[1].entry, sigs[1].sl)
    if size["lots"]:
        lines.append(f"Lot size: <b>{size['lots']:g}</b>")
    if sigs[0].expires is not None:
        lines.append(f"Cancel both if neither has filled by {sigs[0].expires.tz_convert(tz):%H:%M} (Malaysia time).")
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
            alerts.append((f"oco:{o.id}", f"❎ <b>Cancel your other order: {sig.label} {o.symbol}</b> at "
                                          f"{spec.fmt(sig.entry)}. The {filled.side.upper()} side filled, so this "
                                          f"one is no longer needed."))
    return alerts


def follow_replay(t: Tracked, bars: pd.DataFrame, cost, spec: SymbolSpec,
                  now: pd.Timestamp) -> list[tuple[str, str]]:
    """Intraday signals: replay the order through the backtest engine on the candles since it
    was sent, and report anything new (filled, TP1, TP2, stopped, session over, expired)."""
    from .engine import Bars, simulate
    alerts = []
    if not t.active or t.signal is None:
        return alerts
    sig = signal_from_dict(t.signal)
    f = spec.fmt
    name = f"{sig.label} {t.symbol}"
    loud = t.status != "skipped"
    res = simulate(sig, Bars.from_df(bars), cost) if len(bars) else None

    if res is None or not res.filled:
        expired = (res is not None and res.status in ("expired", "cancelled")) or \
                  (sig.expires is not None and now >= sig.expires)
        if expired:
            t.placed = t.placed or t.status == "placed"
            t.status, t.outcome, t.result_r, t.closed_at = "closed", "expired", 0.0, now.isoformat()
            if loud:
                why = ("price ran to the target without coming back to your entry"
                       if res is not None and res.status == "cancelled" else "price never reached your entry")
                alerts.append((f"expired:{t.id}", f"⌛ <b>Cancel your pending order: {name}</b> at {f(sig.entry)}.\n"
                                                  f"The setup is over: {why}. No loss, no trade."))
        return alerts

    if not t.fill_notified:
        t.fill_notified = True
        if loud:
            alerts.append((f"filled:{t.id}", f"▶️ <b>Order filled: {name}</b> at about {f(res.fill_price)}. "
                                             f"It's a live trade now. Stop loss {f(sig.sl)}."))
    real = [e for e in res.exits if e.reason != "data_end"]
    risk = sig.stop_distance
    for e in real[t.seen_exits:]:
        r_now = sig.direction * (e.price - res.fill_price) / risk
        text = {
            "tp1": f"💰 <b>TP1 hit: {name}</b>. Take half profit at {f(e.price)}, then move your stop to "
                   f"your entry {f(res.fill_price)} so the rest can't lose.",
            "tp2": f"🎯 <b>TP2 hit: {name}</b>. Close the rest at {f(e.price)}.",
            "sl": f"🛑 <b>Stopped out: {name}</b> at {f(e.price)}.",
            "be": f"➖ <b>Back to entry: {name}</b>. The rest closed at breakeven ({f(e.price)}).",
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


def format_signal(sig: Signal, source: Source, spec: SymbolSpec, balance: float, risk_pct: float,
                  events: list[Event], tz: str, crowd: str | None = None) -> str:
    f = spec.fmt
    icon = "🟢" if sig.side == "buy" else "🔴"
    horizon = "📅 LONG-TERM (days to weeks)" if source.horizon == "LONG-TERM" else "⚡ INTRADAY (minutes to hours)"
    size = position_size(spec, balance, risk_pct, sig.entry, sig.sl)
    if size["lots"]:
        lot_line = f"Lot size: <b>{size['lots']:g}</b> (lose ${size['risk_usd']:,.2f} = {risk_pct:g}% if the stop is hit)"
    else:
        lot_line = (f"Lot size: the smallest size ({spec.volume_min:g}) risks ${size['min_lot_risk_usd']:,.2f} "
                    f"({size['min_lot_risk_usd'] / balance * 100:.1f}% of your account), above your {risk_pct:g}%. "
                    f"Use {spec.volume_min:g} only if you accept that, otherwise skip.")
    lines = [
        f"{icon} <b>{sig.label} {sig.symbol}</b>",
        horizon,
        f"Confidence: <b>{html.escape(source.tier)}</b>",
        "",
        f"Entry: <b>{f(sig.entry)}</b>" + (" (buy at the current price)" if sig.order_type == "market" and sig.side == "buy"
                                            else " (sell at the current price)" if sig.order_type == "market" else ""),
        f"Stop loss: <b>{f(sig.sl)}</b> ({f(sig.stop_distance)} away)",
    ]
    if sig.tp1 is not None:
        lines.append(f"Take profit: {f(sig.tp1)}" + (f", then {f(sig.tp2)}" if sig.tp2 else ""))
    if sig.trail_distance:
        lines.append(f"No fixed target: I'll tell you when to move the stop to lock in profit "
                     f"(it follows the best price at {f(sig.trail_distance)} distance).")
    lines += [lot_line, "", f"<b>Why:</b> {html.escape(sig.reason)}"]
    soon = relevant(events, [sig.symbol], sig.created, sig.created + pd.Timedelta(hours=24))
    if soon:
        lines.append("<b>News next 24h:</b> " + "; ".join(
            f"{e.time.tz_convert(tz):%a %H:%M} {e.currency} {html.escape(e.title)}" for e in soon[:4]))
    if crowd:
        lines.append(f"<b>Crowd:</b> {html.escape(crowd)}")
    lines += ["", f"<i>{html.escape(source.stats)}</i>",
              "<i>Demo account. Past results don't guarantee future ones.</i>"]
    return "\n".join(lines)


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
            f"🔒 <b>Move your stop: {name}</b>\nNew stop loss: <b>{f(t.cur_sl)}</b>. "
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


def weekly_report(history: pd.DataFrame, open_signals: list[tuple[Tracked, float | None]],
                  now: pd.Timestamp, account_week: dict | None = None) -> str:
    """Plain-English report card. history = closed signals; open_signals = (signal, current R)."""
    week_ago = now - pd.Timedelta(days=7)
    lines = [f"📊 <b>Weekly report card</b> ({week_ago:%d %b} to {now:%d %b})", ""]

    h = history.copy()
    if not h.empty:
        h["closed_at"] = pd.to_datetime(h["closed_at"], utc=True)
        h["created"] = pd.to_datetime(h["created"], utc=True)
    week = h[h.closed_at >= week_ago] if not h.empty else h
    sent = (h.created >= week_ago).sum() if not h.empty else 0
    sent += sum(1 for t, _ in open_signals if pd.Timestamp(t.created) >= week_ago)
    lines.append(f"<b>This week:</b> {sent} new signal(s), {len(week)} finished.")
    for r in week.itertuples() if not week.empty else []:
        lines.append(f"• {r.side.upper()} {r.symbol}: {r.result_r:+.1f}x risk "
                     f"({'you placed it' if r.placed else 'you skipped it'})")
    if open_signals:
        lines += ["", "<b>Still open:</b>"]
        for t, cur in open_signals:
            lines.append(f"• {t.side.upper()} {t.symbol} since {pd.Timestamp(t.created):%d %b}: "
                         + (f"{cur:+.1f}x risk now" if cur is not None else "price unavailable")
                         + ("" if t.status == "placed" else " (not placed)"))

    if not h.empty:
        if "outcome" not in h:
            h["outcome"] = ""
        traded = h[~h.outcome.isin(["expired", "oco"])]
        expired = len(h) - len(traded)
        lines += ["", "<b>Since the start, by feed:</b>"]
        for src, g in traded.groupby("source"):
            lines.append(f"• {src}: {len(g)} trades · win rate {(g.result_r > 0).mean() * 100:.0f}% · "
                         f"total {g.result_r.sum():+.1f}x risk · average {g.result_r.mean():+.2f}x")
        if expired:
            lines.append(f"({expired} pending order(s) never filled; they don't count.)")
        placed = traded[traded.placed.astype(bool)]
        if len(placed):
            lines.append(f"The ones you placed ({len(placed)}): total {placed.result_r.sum():+.1f}x risk")
        lines.append("Backtest expectations: crypto trend about +0.26x per signal; the ⚗️ experimental "
                     "intraday feeds about -0.04x to -0.16x. Judge after 20+ trades per feed.")
    if account_week:
        lines += ["", f"<b>Your MT5 demo this week:</b> {account_week['trades']} closed trade(s), "
                      f"${account_week['profit']:+,.2f} · balance ${account_week['balance']:,.2f}"]
    return "\n".join(lines)


def to_dict(t: Tracked) -> dict:
    return asdict(t)


def from_dict(d: dict) -> Tracked:
    return Tracked(**d)
