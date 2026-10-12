"""Trading-assistant logic: lot sizing and alerts about open trades.

Kept free of MT5 and Telegram so it can be tested on its own. The bot (scripts/run_bot.py)
feeds it live positions and sends whatever alerts it returns.
"""
from dataclasses import dataclass, field

import pandas as pd

from .instruments import SymbolSpec
from .news import Event, relevant
from .timeutils import NEW_YORK, trading_day


@dataclass
class Position:
    ticket: int
    symbol: str             # our name, e.g. XAUUSD
    side: str               # buy | sell
    lots: float
    open_price: float
    sl: float               # 0 = none
    tp: float               # 0 = none
    price: float            # current closing price for this side
    profit: float           # USD, floating

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


@dataclass
class Alert:
    key: str                # alerts with the same key are only sent once
    text: str


@dataclass
class MonitorContext:
    now: pd.Timestamp                       # UTC
    specs: dict[str, SymbolSpec]
    events: list[Event]
    initial_sl: dict[int, float]            # first SL seen per ticket (to measure 1R)
    equity: float
    day_start_equity: float
    spikes: dict[str, float] = field(default_factory=dict)  # symbol -> last candle / normal size
    news_minutes: int = 30
    breakeven_at_r: float = 1.0
    spike_multiple: float = 3.0
    daily_warn_pct: float = 3.0
    daily_limit_pct: float = 4.0
    local_tz: str = "Asia/Kuala_Lumpur"


def position_size(spec: SymbolSpec, balance: float, risk_pct: float, entry: float, sl: float) -> dict:
    """Lots that lose `risk_pct` of `balance` if the stop is hit."""
    dist = abs(entry - sl)
    if dist <= 0:
        raise ValueError("Stop loss must be different from the entry price.")
    per_lot = spec.money_per_price_unit(1.0, entry) * dist     # USD lost per 1.00 lot at the SL
    target = balance * risk_pct / 100
    lots = spec.round_lots(target / per_lot)
    return {
        "lots": lots,
        "risk_usd": lots * per_lot,
        "target_usd": target,
        "min_lot_risk_usd": spec.volume_min * per_lot,
        "distance": dist,
    }


def _local(ts: pd.Timestamp, tz: str) -> str:
    return ts.tz_convert(tz).strftime("%a %H:%M")


def check_positions(positions: list[Position], ctx: MonitorContext) -> list[Alert]:
    alerts = []
    for p in positions:
        spec = ctx.specs[p.symbol]
        name = f"{p.side.upper()} {p.symbol} {p.lots:g} lots @ {spec.fmt(p.open_price)}"

        if not p.sl:
            alerts.append(Alert(f"nosl:{p.ticket}",
                f"⚠️ <b>No stop loss</b> on {name}.\nOne bad move could cost far more than planned. "
                f"Set a stop loss now (send <code>/size</code> to work out where)."))
            continue

        init_sl = ctx.initial_sl.get(p.ticket, p.sl)
        risk_dist = abs(p.open_price - init_sl)
        gained = p.sign * (p.price - p.open_price)
        sl_still_at_risk = p.sign * (p.sl - p.open_price) < 0
        if risk_dist > 0 and sl_still_at_risk and gained >= ctx.breakeven_at_r * risk_dist:
            alerts.append(Alert(f"be:{p.ticket}",
                f"✅ <b>Move stop to breakeven</b>: {name}\nPrice has moved "
                f"{gained / risk_dist:.1f}x your risk in your favour (profit ${p.profit:,.2f}). "
                f"Moving the stop loss to {spec.fmt(p.open_price)} makes this a no-loss trade."))

        soon = relevant(ctx.events, [p.symbol], ctx.now, ctx.now + pd.Timedelta(minutes=ctx.news_minutes))
        for e in soon:
            mins = int((e.time - ctx.now).total_seconds() // 60)
            alerts.append(Alert(f"news:{p.ticket}:{e.key}",
                f"📰 <b>Big news in {mins} min</b> ({_local(e.time, ctx.local_tz)}): {e.currency} {e.title}"
                + (f" (forecast {e.forecast}, previous {e.previous})" if e.forecast else "")
                + f"\nYou have {name} open, profit ${p.profit:,.2f}. Spreads widen and price can jump "
                  f"past your stop. Consider closing, taking partial profit, or tightening the stop."))

        ny = ctx.now.tz_convert(NEW_YORK)
        if ny.weekday() == 4 and ny.hour >= 15:
            alerts.append(Alert(f"weekend:{p.ticket}:{ny.date()}",
                f"📅 <b>Weekend coming</b>: {name} is still open (profit ${p.profit:,.2f}).\n"
                f"The market closes in under 2 hours and can reopen Monday far from your stop. "
                f"Scalps are usually closed before the weekend."))

    for sym in {p.symbol for p in positions}:
        mult = ctx.spikes.get(sym)
        if mult and mult >= ctx.spike_multiple:
            alerts.append(Alert(f"spike:{sym}:{ctx.now:%Y%m%d%H%M}"[:-1],   # once per ~10 min
                f"⚡ <b>Sudden move on {sym}</b>: the last 5-minute candle was {mult:.1f}x its normal size.\n"
                f"Check your open trade; this often comes with news or a big order."))

    if ctx.day_start_equity > 0:
        dd = (ctx.day_start_equity - ctx.equity) / ctx.day_start_equity * 100
        day = trading_day(ctx.now)
        if dd >= ctx.daily_limit_pct:
            alerts.append(Alert(f"daily_stop:{day}",
                f"🛑 <b>Daily loss limit reached</b>: down {dd:.1f}% today (${ctx.day_start_equity - ctx.equity:,.2f}).\n"
                f"Stop trading for today. A prop firm would fail you at 5%."))
        elif dd >= ctx.daily_warn_pct:
            alerts.append(Alert(f"daily_warn:{day}",
                f"🟠 <b>Down {dd:.1f}% today</b> (${ctx.day_start_equity - ctx.equity:,.2f}). "
                f"Your limit is {ctx.daily_limit_pct:g}%. Only take your very best setup, or stop for the day."))
    return alerts
