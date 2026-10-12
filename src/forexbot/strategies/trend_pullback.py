"""Strategy B: trend + pullback with limit orders.

Idea: in a clear trend, don't chase. Wait for price to stretch away from its average, then
place a limit order back at the average so you join the trend at a better price.

On each closed candle (default H1):
  * Uptrend:   fast EMA above slow EMA, and the slow EMA rising over the last `slope_bars`.
  * Stretched: close is at least `stretch_atr` x ATR above the fast EMA.
  -> BUY LIMIT at the fast EMA, SL `sl_atr` x ATR below it, TP1/TP2 in R.
  (mirror for downtrends -> SELL LIMIT)
A new order is only sent when the setup first appears, then a cooldown applies.
"""
import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES

NAME = "trend_pullback"
DESCRIPTION = "Join the trend on a pullback to the moving average (buy/sell limit orders)"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    close = bars["close"]
    fast = close.ewm(span=params["fast_ema"], adjust=False).mean()
    slow = close.ewm(span=params["slow_ema"], adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([bars.high - bars.low, (bars.high - prev_close).abs(), (bars.low - prev_close).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(params["atr_period"]).mean()
    rising = slow > slow.shift(params["slope_bars"])
    falling = slow < slow.shift(params["slope_bars"])
    up = (fast > slow) & rising & (close - fast >= params["stretch_atr"] * atr)
    down = (fast < slow) & falling & (fast - close >= params["stretch_atr"] * atr)

    warmup = params["slow_ema"] * 3
    weekday = bars.index.weekday < 5
    signals, last_i = [], -10**9
    for side, cond in (("buy", up), ("sell", down)):
        trigger = cond & ~cond.shift(1, fill_value=False) & weekday
        last_i = -10**9
        for i in np.flatnonzero(trigger.to_numpy()):
            if i < warmup or i - last_i < params["cooldown_bars"]:
                continue
            last_i = i
            a, entry = atr.iloc[i], fast.iloc[i]
            sign = 1 if side == "buy" else -1
            sl = entry - sign * params["sl_atr"] * a
            risk = abs(entry - sl)
            created = bars.index[i] + tf
            signals.append(Signal(
                strategy=NAME, symbol=symbol, side=side, order_type="limit", created=created,
                entry=round(entry, spec.digits), sl=round(sl, spec.digits),
                tp1=round(entry + sign * params["tp1_r"] * risk, spec.digits),
                tp2=round(entry + sign * params["tp2_r"] * risk, spec.digits) if params.get("tp2_r") else None,
                tp1_close_pct=params["tp1_close_pct"],
                expires=created + params["expiry_bars"] * tf,
                close_by=created + params["max_hold_bars"] * tf,
                # Price starts beyond TP1 by design (we wait for it to come back to the EMA),
                # so "target reached before fill" must not cancel the order.
                cancel_if_tp_before_fill=False,
                reason=(f"{'Up' if side == 'buy' else 'Down'}trend: EMA{params['fast_ema']} "
                        f"{'above' if side == 'buy' else 'below'} EMA{params['slow_ema']} and the slow EMA "
                        f"{'rising' if side == 'buy' else 'falling'}. Price stretched "
                        f"{abs(close.iloc[i] - entry) / a:.1f}x ATR from EMA{params['fast_ema']}; "
                        f"{'buy' if side == 'buy' else 'sell'} the pullback at {spec.fmt(entry)}."),
            ))
    return signals
