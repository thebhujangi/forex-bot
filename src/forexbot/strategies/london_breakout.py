"""Strategy A: London open breakout of the Asian session range.

Idea: while Asia trades, gold and the European pairs often drift in a tight range. When London
opens, real volume arrives and price frequently breaks out of that range and keeps going.

Each weekday:
  1. Measure the high/low between range_start and range_end (London time).
  2. Skip the day if the range is tiny or already huge compared with the average daily move (ATR).
  3. Send two pending orders (one cancels the other):
       BUY STOP  just above the range high
       SELL STOP just below the range low
     SL = middle of the range (or the other side), TP1 = 1R (close half, SL to breakeven), TP2 = 2R.
  4. Unfilled orders are cancelled at entry_deadline; open trades are closed at close_time.
"""
import math
from zoneinfo import ZoneInfo

import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES
from .common import daily_atr, daily_trend, local_to_utc, parse_hm

NAME = "london_breakout"
DESCRIPTION = "Asian range breakout at the London open (buy stop / sell stop, one cancels the other)"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tz = ZoneInfo(params.get("session_tz", "Europe/London"))
    start, end = parse_hm(params["range_start"]), parse_hm(params["range_end"])
    deadline, close_time = parse_hm(params["entry_deadline"]), parse_hm(params["close_time"])
    skip_days = set(params.get("skip_weekdays") or [])

    local = bars.index.tz_convert(tz)
    minutes = local.hour * 60 + local.minute
    in_range = (minutes >= start.hour * 60 + start.minute) & (minutes < end.hour * 60 + end.minute)
    ranges = bars[in_range].groupby(local[in_range].date).agg(
        hi=("high", "max"), lo=("low", "min"), n=("high", "size")
    )
    range_minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    expected_bars = range_minutes / TIMEFRAME_MINUTES[params["timeframe"]]
    atr = daily_atr(bars, tz, params.get("atr_period", 14))
    trend_days = params.get("trend_ema", 0)
    trend = daily_trend(bars, tz, trend_days) if trend_days else None

    signals = []
    for d, row in ranges.iterrows():
        if d.weekday() >= 5 or d.weekday() in skip_days or row.n < 0.8 * expected_bars:
            continue
        a = atr.get(d, math.nan)
        if not a or math.isnan(a):
            continue
        width = row.hi - row.lo
        if not (params["min_range_atr"] * a <= width <= params["max_range_atr"] * a):
            continue

        buf = params["buffer_atr"] * a
        mid = (row.hi + row.lo) / 2
        common = dict(
            strategy=NAME, symbol=symbol, order_type="stop",
            created=local_to_utc(d, end, tz),
            expires=local_to_utc(d, deadline, tz),
            close_by=local_to_utc(d, close_time, tz),
            tp1_close_pct=params["tp1_close_pct"],
            oco_group=f"{NAME}:{symbol}:{d}",
        )
        info = (f"Asian range {spec.fmt(row.lo)}-{spec.fmt(row.hi)} "
                f"({width / a:.2f}x daily ATR {spec.fmt(a)}).")

        for side in ("buy", "sell"):
            sign = 1 if side == "buy" else -1
            if trend is not None and trend.get(d) != sign:
                continue   # only trade breakouts in the direction of the daily trend
            entry = row.hi + buf if side == "buy" else row.lo - buf
            if params["sl_mode"] == "mid":
                sl = mid
            else:
                sl = row.lo - buf if side == "buy" else row.hi + buf
            risk = abs(entry - sl)
            tp2_r = params.get("tp2_r")
            signals.append(Signal(
                side=side,
                entry=round(entry, spec.digits),
                sl=round(sl, spec.digits),
                tp1=round(entry + sign * params["tp1_r"] * risk, spec.digits),
                tp2=round(entry + sign * tp2_r * risk, spec.digits) if tp2_r else None,
                reason=f"{info} {'Break above the high' if side == 'buy' else 'Break below the low'} "
                       f"at the London open.",
                **common,
            ))
    return signals
