"""Strategy D: daily trend following (channel breakout with an ATR trailing stop).

Idea: currencies and gold spend long stretches trending. Rather than predicting turns, join a
trend once price closes at a new N-day high (or low), keep a wide stop so normal noise doesn't
shake you out, and let a trailing stop take you out only when the trend actually bends.
Most trades are small losses; a few big trends pay for them. Costs are tiny relative to the
wide stop, which is what sank the intraday strategies.

On each daily close:
  * BUY  when the close is above the highest high of the previous `entry_days` days
         (first close above it only), optionally only if the close is above its EMA.
  * SELL mirror image at the lowest low.
Order: market at the next open. SL = sl_atr x ATR. No fixed target; the SL then trails the
best price at trail_atr x ATR.
"""
import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES

NAME = "daily_trend"
DESCRIPTION = "Daily channel breakout, trend following with a wide ATR trailing stop (market orders)"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    n = params["entry_days"]
    h, l, c = bars["high"], bars["low"], bars["close"]
    upper = h.rolling(n).max().shift(1)          # previous N days, excluding today
    lower = l.rolling(n).min().shift(1)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(params["atr_period"]).mean()

    long_ok = short_ok = pd.Series(True, index=bars.index)
    if params.get("trend_ema"):
        ema = c.ewm(span=params["trend_ema"], adjust=False).mean()
        long_ok, short_ok = c > ema, c < ema

    above = c > upper
    below = c < lower
    signals = []
    for side, cond in (("buy", above & ~above.shift(1, fill_value=False) & long_ok),
                       ("sell", below & ~below.shift(1, fill_value=False) & short_ok)):
        for i in np.flatnonzero(cond.to_numpy()):
            a = atr.iloc[i]
            if np.isnan(a) or a <= 0:
                continue
            sign = 1 if side == "buy" else -1
            entry = c.iloc[i]
            level = upper.iloc[i] if side == "buy" else lower.iloc[i]
            signals.append(Signal(
                strategy=NAME, symbol=symbol, side=side, order_type="market",
                created=bars.index[i] + tf,
                entry=round(entry, spec.digits),
                sl=round(entry - sign * params["sl_atr"] * a, spec.digits),
                tp1=None,
                trail_distance=params["trail_atr"] * a,
                reason=(f"Daily close {spec.fmt(entry)} broke the {n}-day "
                        f"{'high' if side == 'buy' else 'low'} {spec.fmt(level)}: new trend leg. "
                        f"Wide stop {params['sl_atr']}x ATR ({spec.fmt(params['sl_atr'] * a)}), "
                        f"then trailing {params['trail_atr']}x ATR, no fixed target."),
            ))
    return signals
