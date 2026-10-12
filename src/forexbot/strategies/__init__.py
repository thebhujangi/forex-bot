"""Strategy registry.

Every strategy module exposes:
    NAME: str
    DESCRIPTION: str
    generate(bars, symbol, params, spec) -> list[Signal]

`generate` must only use candles that had closed by each signal's `created` time.
The same function is used for backtests and for live signals, so what you test is what you get.
"""
from . import (daily_trend, fvg_retest, london_breakout, news_candle, range_fade, sweep_reversal,
               trend_pullback)

STRATEGIES = {m.NAME: m for m in (london_breakout, trend_pullback, sweep_reversal, daily_trend,
                                   range_fade, fvg_retest, news_candle)}


def get_strategy(name: str):
    try:
        return STRATEGIES[name]
    except KeyError:
        raise KeyError(f"Unknown strategy '{name}'. Available: {', '.join(STRATEGIES)}") from None
