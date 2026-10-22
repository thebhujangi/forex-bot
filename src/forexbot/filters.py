"""Signal filters that use outside data (see extdata.py). A filter only removes signals; it never
creates them. Each one looks only at data that was public when the signal was created.

  real_rate    gold: buy only while US real rates are falling (20 days), sell only while rising
  dollar       gold/EUR/GBP: buy only while the dollar is falling (20 days), sell only while rising
  cot_trend    gold/EUR/GBP: trade with big speculators: buy if their net long grew over 4 weeks
  cot_extreme  gold/EUR/GBP: skip buys when speculators are more crowded long than 90% of the last
               3 years (and sells when more crowded short)
  funding      crypto: skip buys when futures funding (7-day average) is in the top 20% of the
               last year, i.e. longs are crowded and paying a lot; mirror for sells
  vol_low / vol_high   only trade when daily volatility (ATR % of price) is below / above its
               1-year median
"""
import pandas as pd

from . import extdata
from .models import Signal

FILTERS = ("real_rate", "dollar", "cot_trend", "cot_extreme", "funding", "vol_low", "vol_high")


def _rising(series: pd.Series, lookback) -> pd.Series:
    return series > series.shift(freq=lookback).reindex(series.index, method="ffill")


def _trailing_rank(s: pd.Series, window) -> pd.Series:
    """Where each value sits within the previous `window` of values (0 = lowest, 1 = highest)."""
    return s.rolling(window, min_periods=30).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)


def _direction_ok(sig: Signal, up: bool | None, invert: bool = False) -> bool:
    if up is None:
        return False
    up = up != invert
    return up if sig.side == "buy" else not up


def apply(signals: list[Signal], name: str | None, symbol: str, bars: pd.DataFrame | None = None) -> list[Signal]:
    if not name or not signals:
        return signals
    if name not in FILTERS:
        raise ValueError(f"Unknown filter {name}. Available: {', '.join(FILTERS)}")
    times = pd.DatetimeIndex([s.created for s in signals])

    if name in ("real_rate", "dollar"):
        s = extdata.load("DFII10" if name == "real_rate" else "DTWEXBGS")
        up = extdata.as_of(_rising(s, pd.Timedelta(days=28)), times)
        # Gold and EUR/GBP rise when real rates / the dollar fall, so "with the filter" is inverted.
        return [g for g, u in zip(signals, up) if pd.notna(u) and _direction_ok(g, bool(u), invert=True)]

    if name in ("cot_trend", "cot_extreme"):
        s = extdata.load(f"cot_{symbol}")
        if name == "cot_trend":
            up = extdata.as_of(_rising(s, pd.Timedelta(days=28)), times)
            return [g for g, u in zip(signals, up) if pd.notna(u) and _direction_ok(g, bool(u))]
        rank = extdata.as_of(_trailing_rank(s, 156), times)        # 156 weeks = 3 years
        return [g for g, r in zip(signals, rank) if pd.notna(r)
                and not (g.side == "buy" and r > 0.9) and not (g.side == "sell" and r < 0.1)]

    if name == "funding":
        f = extdata.load(f"funding_{symbol}")
        avg7 = f.rolling(pd.Timedelta(days=7)).mean()
        daily = avg7.resample("1D").last().dropna()
        rank = extdata.as_of(_trailing_rank(daily, 365), times)
        return [g for g, r in zip(signals, rank) if pd.notna(r)
                and not (g.side == "buy" and r > 0.8) and not (g.side == "sell" and r < 0.2)]

    # Volatility regime from daily candles built from the strategy's own bars.
    d = bars.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    prev = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    atr_pct = (tr.rolling(14).mean() / d.close).shift(1)            # yesterday's, so it's known
    rank = extdata.as_of(_trailing_rank(atr_pct.dropna(), 365), times)
    keep_low = name == "vol_low"
    return [g for g, r in zip(signals, rank) if pd.notna(r) and ((r < 0.5) == keep_low)]
