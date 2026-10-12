"""Strategy H: gap retrace after a strong candle (known to SMC/ICT traders as a "fair value gap").

Plain words: when one candle shoots up (or down) very fast, it leaves a "gap": a price zone
that only that single fast candle traded through. Price often comes back to that zone before
continuing. We place a limit order inside the gap, in the direction of the fast move.

Bullish gap on 3 closed candles (i-2, i-1, i):
  * candle i-1 is a big up-candle: body >= displacement_atr x ATR
  * candle i's low is above candle i-2's high -> gap = [high(i-2), low(i)]
  * gap size >= min_gap_atr x ATR, and price above its EMA (optional trend filter)
  -> BUY LIMIT at the middle of the gap, SL below candle i-1's low, TP1/TP2 in multiples of risk.
Mirror for bearish. Only during the session window; unfilled orders expire after expiry_bars.
"""
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES
from .common import local_to_utc, parse_hm

NAME = "fvg_retest"
DESCRIPTION = "Limit order back into the gap left by a sudden strong candle, in the direction of that move"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tz = ZoneInfo(params.get("session_tz", "Europe/London"))
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    w_start, w_end = parse_hm(params["window_start"]), parse_hm(params["window_end"])
    close_time = parse_hm(params["close_time"])

    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    atr = pd.Series(tr).rolling(params["atr_period"]).mean().to_numpy()
    ema = pd.Series(c).ewm(span=params["trend_ema"], adjust=False).mean().to_numpy() \
        if params.get("trend_ema") else None

    local = bars.index.tz_convert(tz)
    mins = np.asarray(local.hour * 60 + local.minute)
    ok_time = ((mins >= w_start.hour * 60 + w_start.minute) & (mins < w_end.hour * 60 + w_end.minute)
               & np.asarray(local.weekday < 5))

    body = c - o
    signals = []
    last_signal = {"buy": -10**9, "sell": -10**9}
    for i in range(max(2, params["atr_period"] + 2), len(c)):
        if not ok_time[i] or np.isnan(atr[i - 1]):
            continue
        a = atr[i - 1]            # ATR before the move, so the big candle doesn't inflate it
        for side in ("buy", "sell"):
            sign = 1 if side == "buy" else -1
            if sign * body[i - 1] < params["displacement_atr"] * a:
                continue
            gap_lo, gap_hi = (h[i - 2], l[i]) if side == "buy" else (h[i], l[i - 2])
            if gap_hi - gap_lo < params["min_gap_atr"] * a:
                continue
            if ema is not None and sign * (c[i] - ema[i]) <= 0:
                continue
            if i - last_signal[side] < params["cooldown_bars"]:
                continue
            last_signal[side] = i
            entry = (gap_lo + gap_hi) / 2
            sl = (l[i - 1] if side == "buy" else h[i - 1]) - sign * params["sl_buffer_atr"] * a
            risk = abs(entry - sl)
            created = bars.index[i] + tf
            signals.append(Signal(
                strategy=NAME, symbol=symbol, side=side, order_type="limit", created=created,
                entry=round(entry, spec.digits), sl=round(sl, spec.digits),
                tp1=round(entry + sign * params["tp1_r"] * risk, spec.digits),
                tp2=round(entry + sign * params["tp2_r"] * risk, spec.digits) if params.get("tp2_r") else None,
                tp1_close_pct=params["tp1_close_pct"],
                expires=created + params["expiry_bars"] * tf,
                close_by=local_to_utc(local[i].date(), close_time, tz),
                cancel_if_tp_before_fill=False,   # price starts beyond the entry by design
                reason=(f"Sudden {'up' if side == 'buy' else 'down'} candle left a gap "
                        f"{spec.fmt(gap_lo)}-{spec.fmt(gap_hi)}. Waiting for price to come back into it, "
                        f"then {'buy' if side == 'buy' else 'sell'} in the direction of the move. "
                        f"Wrong if price goes past {spec.fmt(sl)}."),
            ))
    return signals
