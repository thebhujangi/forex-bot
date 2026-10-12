"""Strategy C: liquidity sweep reversal at the previous day's high/low.

Idea: stop-loss orders cluster just beyond yesterday's high and low. Big players push price
through those levels to fill their own orders, then price snaps back. Strategy A's results
showed this directly: London breakouts reversed more often than they continued.

On each closed candle during the trading window:
  * Bearish sweep: the candle's high goes above yesterday's high, but it CLOSES back below it.
  * Bullish sweep: the candle's low goes below yesterday's low, but it CLOSES back above it.
  Only the first sweep of each level per day counts.

Order (entry_mode):
  retest -> SELL LIMIT / BUY LIMIT back at the swept level (price often retests it)
  market -> SELL / BUY at the next candle's open
SL just beyond the sweep candle's extreme (+ buffer); TP1 = tp1_r, TP2 = tp2_r.
"""
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES, NEW_YORK, ROLLOVER_SHIFT
from .common import local_to_utc, parse_hm

NAME = "sweep_reversal"
DESCRIPTION = "Fade a false break of yesterday's high/low (sell/buy limit on the retest, SL beyond the sweep)"


def _trading_days(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.tz_convert(NEW_YORK) + ROLLOVER_SHIFT).date


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tz = ZoneInfo(params.get("session_tz", "Europe/London"))
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    win_start, win_end = parse_hm(params["window_start"]), parse_hm(params["window_end"])
    close_time = parse_hm(params["close_time"])

    # Previous broker day's high/low/ATR, mapped onto every candle of the current day.
    day = _trading_days(bars.index)
    days = bars.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                 n=("high", "size"))
    days = days[days.n >= 0.5 * days.n.median()]           # drop holiday stubs
    prev_c = days.close.shift(1)
    tr = pd.concat([days.high - days.low, (days.high - prev_c).abs(), (days.low - prev_c).abs()],
                   axis=1).max(axis=1)
    ref = pd.DataFrame({"pdh": days.high.shift(1), "pdl": days.low.shift(1),
                        "atr": tr.rolling(params.get("atr_period", 14)).mean().shift(1)})
    m = ref.reindex(day)
    pdh, pdl, atr = m.pdh.to_numpy(), m.pdl.to_numpy(), m.atr.to_numpy()

    local = bars.index.tz_convert(tz)
    mins = local.hour * 60 + local.minute
    in_window = ((mins >= win_start.hour * 60 + win_start.minute)
                 & (mins < win_end.hour * 60 + win_end.minute) & (local.weekday < 5))

    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    min_sweep, max_sweep = params["min_sweep_atr"], params["max_sweep_atr"]
    buf_atr, expiry_bars = params["sl_buffer_atr"], params["expiry_bars"]
    tp1_r, tp2_r = params["tp1_r"], params.get("tp2_r")
    mode = params["entry_mode"]

    signals, used = [], set()
    for i in np.flatnonzero(in_window):
        a = atr[i]
        if np.isnan(a) or np.isnan(pdh[i]):
            continue
        created = bars.index[i] + tf                  # this candle has closed
        for side, level, extreme, swept, closed_back in (
            ("sell", pdh[i], h[i], h[i] - pdh[i], c[i] < pdh[i]),
            ("buy", pdl[i], l[i], pdl[i] - l[i], c[i] > pdl[i]),
        ):
            key = (day[i], side)
            if key in used or swept <= 0:
                continue
            used.add(key)                             # first touch of the level decides it
            if not closed_back or not (min_sweep * a <= swept <= max_sweep * a):
                continue
            sign = 1 if side == "buy" else -1
            sl = extreme - sign * buf_atr * a
            entry = level if mode == "retest" else c[i]
            risk = abs(entry - sl)
            if risk < 0.05 * a:
                continue
            signals.append(Signal(
                strategy=NAME, symbol=symbol, side=side,
                order_type="limit" if mode == "retest" else "market",
                created=created,
                entry=round(entry, spec.digits), sl=round(sl, spec.digits),
                tp1=round(entry + sign * tp1_r * risk, spec.digits),
                tp2=round(entry + sign * tp2_r * risk, spec.digits) if tp2_r else None,
                tp1_close_pct=params["tp1_close_pct"],
                expires=created + expiry_bars * tf if mode == "retest" else None,
                close_by=local_to_utc(local[i].date(), close_time, tz),
                reason=(f"Swept {'yesterday\'s high' if side == 'sell' else 'yesterday\'s low'} "
                        f"{spec.fmt(level)} by {spec.fmt(swept)} ({swept / a:.2f}x ATR) and closed back "
                        f"{'below' if side == 'sell' else 'above'} it: stop hunt, expecting reversal."),
            ))
    return signals
