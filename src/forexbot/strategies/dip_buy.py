"""Strategy E: buy short dips inside a long uptrend (stock indices, daily candles).

Idea: in an index that's above its 200-day average (long-term uptrend), a sharp 1-3 day drop
tends to bounce back within days. Long only.

On each daily close, when close > SMA(trend_sma):
  * mode "rsi2": RSI(2) below `rsi_below` (a very oversold short-term reading)
  * mode "ibs":  close in the bottom `ibs_below` of the day's range (IBS = (C-L)/(H-L))
Order: buy at market at the next open. Target: the 5-day average at signal time (where price
usually snaps back to). Safety stop: sl_atr x ATR. Time exit after hold_days.
"""
import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES

NAME = "dip_buy"
DESCRIPTION = "Buy short-term dips in a long-term uptrend (daily, long only)"


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    h, l, c = bars["high"], bars["low"], bars["close"]
    sma_trend = c.rolling(params["trend_sma"]).mean()
    sma5 = c.rolling(5).mean()
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(params["atr_period"]).mean()
    if params["mode"] == "rsi2":
        dip = rsi(c, 2) < params["rsi_below"]
        why = "RSI(2) {:.0f}: sharply oversold"
        score = rsi(c, 2)
    else:
        score = (c - l) / (h - l).replace(0, np.nan)
        dip = score < params["ibs_below"]
        why = "closed in the bottom {:.0%} of the day's range"
    cond = (c > sma_trend) & dip
    signals = []
    for i in np.flatnonzero(cond.to_numpy()):
        a, entry, target = atr.iloc[i], c.iloc[i], sma5.iloc[i]
        if np.isnan(a) or a <= 0:
            continue
        created = bars.index[i] + tf
        tp = round(target, spec.digits) if target > entry + 0.1 * a else None
        signals.append(Signal(
            strategy=NAME, symbol=symbol, side="buy", order_type="market", created=created,
            entry=round(entry, spec.digits), sl=round(entry - params["sl_atr"] * a, spec.digits),
            tp1=tp, close_by=created + pd.Timedelta(days=params["hold_days"]),
            reason=(f"Uptrend (above the {params['trend_sma']}-day average) and the index "
                    + why.format(score.iloc[i]) + f". Target the 5-day average"
                    + (f" {spec.fmt(tp)}" if tp else "") + f", exit within {params['hold_days']} days."),
        ))
    return signals
