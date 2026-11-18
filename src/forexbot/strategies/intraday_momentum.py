"""Strategy F: intraday momentum into the US close (stock indices, 5-minute candles).

Idea (published): the direction an index has moved during the day tends to carry on into the last
half hour, partly because options dealers hedging short gamma must trade with the move.
  * mode "rest_of_day" (Baltussen, Da, Lammers & Martens, JFE 2021): return from yesterday's
    16:00 New York close to today's 15:30.
  * mode "first_half_hour" (Gao, Han, Li & Zhou, JFE 2018): return from yesterday's close to 10:00.
At 15:30 New York: buy if that return is up, sell if down (optionally only if bigger than
min_move_atr x daily ATR). Market order, closed at 16:00. Safety stop sl_atr x daily ATR.
"""
from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from .common import daily_atr, local_to_utc

NAME = "intraday_momentum"
DESCRIPTION = "Trade the day's direction into the US close, 15:30-16:00 New York (index CFDs)"
NY = ZoneInfo("America/New_York")


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    step = pd.Timedelta(minutes=5)
    closes = bars["close"]
    ends = bars.index + step                               # each candle's close time
    by_end = pd.Series(closes.to_numpy(), index=ends)
    atr = daily_atr(bars, NY, params.get("atr_period", 14))
    signal_t = time(15, 30)
    mark_t = signal_t if params["mode"] == "rest_of_day" else time(10, 0)

    days = sorted({d for d in bars.index.tz_convert(NY).date if d.weekday() < 5})
    signals, prev_close = [], None
    for d in days:
        close_t = local_to_utc(d, time(16, 0), NY)
        at_close = by_end.get(close_t)
        sig_t = local_to_utc(d, signal_t, NY)
        mark = by_end.get(local_to_utc(d, mark_t, NY))
        now = by_end.get(sig_t)
        a = atr.get(d, np.nan)
        if prev_close is not None and mark is not None and now is not None and a and not np.isnan(a):
            move = mark / prev_close - 1
            if abs(mark - prev_close) >= params.get("min_move_atr", 0) * a and move != 0:
                side = "buy" if move > 0 else "sell"
                sign = 1 if side == "buy" else -1
                signals.append(Signal(
                    strategy=NAME, symbol=symbol, side=side, order_type="market", created=sig_t,
                    entry=round(now, spec.digits), sl=round(now - sign * params["sl_atr"] * a, spec.digits),
                    tp1=None, close_by=close_t,
                    reason=(f"Index {'up' if move > 0 else 'down'} {move * 100:+.2f}% from yesterday's close "
                            f"to {mark_t:%H:%M} New York: ride it into the 16:00 close."),
                ))
        if at_close is not None:
            prev_close = at_close
    return signals
