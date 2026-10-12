"""Strategy F: fade a failed break of the Asian-session range at the London open.

Plain words: overnight (Asia) price usually stays in a small box. When London opens, price often
pokes out of the box to trigger other traders' stop orders, then falls straight back in. When a
candle closes back inside the box after poking out, we trade back toward the middle and the far
side of the box.

  * Box = high/low between range_start and range_end (session_tz).
  * Watch from range_end to watch_until. If a candle's high went above the box but it CLOSED
    back inside -> SELL. Mirror for the low -> BUY. One trade per side per day.
  * SL just beyond the highest/lowest price reached since the box closed (the fake-out extreme).
  * TP1 = middle of the box (close tp1_close_pct there, SL to breakeven), TP2 = far side of the box.
"""
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES
from .common import daily_atr, local_to_utc, parse_hm

NAME = "range_fade"
DESCRIPTION = "Fade a failed break of the Asian range at the London open (market order, targets inside the range)"


def _minutes(t) -> int:
    return t.hour * 60 + t.minute


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tz = ZoneInfo(params.get("session_tz", "Europe/London"))
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    r_start, r_end = parse_hm(params["range_start"]), parse_hm(params["range_end"])
    watch_until, close_time = parse_hm(params["watch_until"]), parse_hm(params["close_time"])

    local = bars.index.tz_convert(tz)
    mins = np.asarray(local.hour * 60 + local.minute)
    dates = np.asarray(local.date)
    atr = daily_atr(bars, tz, params.get("atr_period", 14))
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))

    in_box = (mins >= _minutes(r_start)) & (mins < _minutes(r_end))
    in_watch = (mins >= _minutes(r_end)) & (mins < _minutes(watch_until))
    box = pd.DataFrame({"h": h[in_box], "l": l[in_box], "d": dates[in_box]}).groupby("d").agg(
        hi=("h", "max"), lo=("l", "min"), n=("h", "size"))
    expected = (_minutes(r_end) - _minutes(r_start)) / TIMEFRAME_MINUTES[params["timeframe"]]

    signals = []
    state = {}   # date -> [highest since box close, lowest since box close, used sides]
    for i in np.flatnonzero(in_watch):
        d = dates[i]
        if d not in box.index or d.weekday() >= 5:
            continue
        b = box.loc[d]
        a = atr.get(d, math.nan)
        if b.n < 0.8 * expected or not a or math.isnan(a):
            continue
        width = b.hi - b.lo
        if not (params["min_range_atr"] * a <= width <= params["max_range_atr"] * a):
            continue
        st = state.setdefault(d, [-math.inf, math.inf, set()])
        st[0], st[1] = max(st[0], h[i]), min(st[1], l[i])
        mid = (b.hi + b.lo) / 2
        buf = params["sl_buffer_atr"] * a
        for side, poked, back_inside, extreme, far in (
            ("sell", st[0] > b.hi, c[i] < b.hi, st[0], b.lo),
            ("buy", st[1] < b.lo, c[i] > b.lo, st[1], b.hi),
        ):
            if side in st[2] or not (poked and back_inside):
                continue
            sign = 1 if side == "buy" else -1
            entry = c[i]
            sl = extreme - sign * buf
            risk = abs(entry - sl)
            # Need room to the middle of the box worth at least min_reward_r of the risk.
            if sign * (mid - entry) < params["min_reward_r"] * risk:
                continue
            st[2].add(side)
            signals.append(Signal(
                strategy=NAME, symbol=symbol, side=side, order_type="market",
                created=bars.index[i] + tf,
                entry=round(entry, spec.digits), sl=round(sl, spec.digits),
                tp1=round(mid, spec.digits), tp2=round(far, spec.digits),
                tp1_close_pct=params["tp1_close_pct"],
                close_by=local_to_utc(d, close_time, tz),
                reason=(f"Overnight box {spec.fmt(b.lo)}-{spec.fmt(b.hi)}. London pushed "
                        f"{'above' if side == 'sell' else 'below'} it to {spec.fmt(extreme)} but the candle "
                        f"closed back inside: a fake-out. Targeting the middle of the box, then the far side."),
            ))
    return signals
