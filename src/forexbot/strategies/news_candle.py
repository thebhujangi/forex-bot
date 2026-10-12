"""Strategy N: trade the reaction to scheduled US economic data.

Plain words: big US numbers (jobs, inflation, retail sales...) come out at fixed times, mostly
8:30am New York (8:30pm / 9:30pm Malaysia). When the number surprises traders, the 5-minute candle
at that moment is unusually large. We don't need the forecast itself: the size of that candle
IS the market's verdict on the surprise.

  * Event candle = the candle starting at one of `event_times` (New York time).
  * It counts only if its body is at least `big_atr` x the normal candle size before the news.
  * mode "follow": trade in the candle's direction (the move continues)
    mode "fade":   trade against it (the first spike was an overreaction)
  * Entry: market at the next candle's open. SL `sl_frac` of the event candle's range away.
  * TP1/TP2 in multiples of the risk; anything still open is closed at `close_after_min`.
"""
import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import NEW_YORK, TIMEFRAME_MINUTES

NAME = "news_candle"
DESCRIPTION = "Trade the 5-minute reaction to scheduled US data releases (8:30am New York)"


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    # "Normal" candle size measured on the candles BEFORE the event.
    atr_before = pd.Series(tr).rolling(params["atr_period"]).mean().shift(1).to_numpy()

    ny = bars.index.tz_convert(NEW_YORK)
    hhmm = np.asarray(ny.strftime("%H:%M"))
    is_event = np.isin(hhmm, params["event_times"]) & np.asarray(ny.weekday < 5)

    signals = []
    for i in np.flatnonzero(is_event):
        a = atr_before[i]
        body = c[i] - o[i]
        rng = h[i] - l[i]
        if np.isnan(a) or abs(body) < params["big_atr"] * a or rng <= 0:
            continue
        move = 1 if body > 0 else -1
        sign = move if params["mode"] == "follow" else -move
        side = "buy" if sign == 1 else "sell"
        entry = c[i]
        risk = params["sl_frac"] * rng
        created = bars.index[i] + tf
        signals.append(Signal(
            strategy=NAME, symbol=symbol, side=side, order_type="market", created=created,
            entry=round(entry, spec.digits), sl=round(entry - sign * risk, spec.digits),
            tp1=round(entry + sign * params["tp1_r"] * risk, spec.digits),
            tp2=round(entry + sign * params["tp2_r"] * risk, spec.digits) if params.get("tp2_r") else None,
            tp1_close_pct=params["tp1_close_pct"],
            close_by=created + pd.Timedelta(minutes=params["close_after_min"]),
            reason=(f"US data at {hhmm[i]} New York: the 5-min candle moved "
                    f"{'up' if move > 0 else 'down'} {spec.fmt(abs(body))} "
                    f"({abs(body) / a:.1f}x a normal candle), so the news surprised traders. "
                    f"{'Riding the move' if params['mode'] == 'follow' else 'Betting the spike was an overreaction'}."),
        ))
    return signals
