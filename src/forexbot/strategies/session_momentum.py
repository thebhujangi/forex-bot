"""Strategy: Nasdaq "session momentum" at the New York open (from @quant_labde, tested 2026-10-25).

Idea: the first 5 minutes after the 09:30 New York open carry the day's first real burst of
volume. If that candle closes above its short EMA, buyers are in control; below, sellers.
Join that side and let a trailing stop ride the move, no fixed target.

Each weekday:
  1. Wait for the first 5-minute candle of the session (09:30-09:35 New York) to close.
  2. Close above the EMA -> BUY at market. Close below -> SELL at market.
  3. Stop = sl_atr x the 5-minute ATR. It then trails the best price at trail_atr x ATR.
     With sl_atr == trail_atr this is ONE broker-side trailing stop order: set it and leave it.
  4. Anything still open at close_time (New York) is closed at market.

Costs decide everything here: the raw edge is ~+0.2R before costs, which a CFD spread of
~3 points mostly eats, while Nasdaq futures (MNQ, ~1.2 points all-in) keep most of it.
"""
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES
from .common import local_to_utc, parse_hm

NAME = "session_momentum"
DESCRIPTION = "Nasdaq New York open: first 5-min candle vs EMA, market entry, trailing stop"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tz = ZoneInfo(params.get("session_tz", "America/New_York"))
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    open_t, close_t = parse_hm(params["open_time"]), parse_hm(params["close_time"])
    delay = pd.Timedelta(minutes=params.get("entry_delay_min", 0))

    h, l, c = bars["high"], bars["low"], bars["close"]
    ema = c.ewm(span=params["ema"], adjust=False).mean()
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(params["atr_period"]).mean()

    local = bars.index.tz_convert(tz)
    first = np.flatnonzero((local.hour == open_t.hour) & (local.minute == open_t.minute)
                           & (local.weekday < 5))
    signals = []
    for i in first:
        a, e, entry = atr.iloc[i], ema.iloc[i], c.iloc[i]
        if math.isnan(a) or a <= 0 or entry == e:
            continue
        side = "buy" if entry > e else "sell"
        sign = 1 if side == "buy" else -1
        d = local[i].date()
        candle = c.iloc[i] - bars["open"].iloc[i]
        signals.append(Signal(
            strategy=NAME, symbol=symbol, side=side, order_type="market",
            created=bars.index[i] + tf + delay,
            entry=round(entry, spec.digits),
            sl=round(entry - sign * params["sl_atr"] * a, spec.digits),
            tp1=None,
            trail_distance=params["trail_atr"] * a,
            close_by=local_to_utc(d, close_t, tz),
            reason=(f"First 5-min candle of the New York session closed "
                    f"{'up' if candle >= 0 else 'down'} {abs(candle):.1f} pts at {spec.fmt(entry)}, "
                    f"{'above' if side == 'buy' else 'below'} the {params['ema']} EMA "
                    f"({spec.fmt(e)}): {'buyers' if side == 'buy' else 'sellers'} in control. "
                    f"Trailing stop {params['trail_atr']}x ATR ({params['trail_atr'] * a:.1f} pts), "
                    f"no fixed target, out by {params['close_time']} New York."),
        ))
    return signals
