import numpy as np
import pandas as pd

from forexbot.instruments import DEFAULT_SPECS
from forexbot.strategies import london_breakout

PARAMS = {
    "timeframe": "M15", "session_tz": "Europe/London", "range_start": "00:00", "range_end": "07:00",
    "entry_deadline": "11:00", "close_time": "20:00", "sl_mode": "mid", "buffer_atr": 0.03,
    "min_range_atr": 0.0, "max_range_atr": 99, "atr_period": 3, "tp1_r": 1.0,
    "tp1_close_pct": 50, "tp2_r": 2.0, "skip_weekdays": [],
}


def random_walk(start, days, seed=1):
    idx = pd.date_range(start, periods=days * 96, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 2000 + np.cumsum(rng.normal(0, 0.8, len(idx)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 0.5, len(idx))
    low = np.minimum(open_, close) - rng.uniform(0, 0.5, len(idx))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def test_signals_are_sent_at_london_07_00_and_never_peek_ahead():
    bars = random_walk("2024-06-03", 15)   # June: London is UTC+1
    signals = london_breakout.generate(bars, "XAUUSD", PARAMS, DEFAULT_SPECS["XAUUSD"])
    assert signals, "expected some signals"
    for s in signals:
        assert s.created.hour == 6 and s.created.minute == 0          # 07:00 London = 06:00 UTC
        rng_bars = bars[(bars.index >= s.created - pd.Timedelta(hours=7)) & (bars.index < s.created)]
        if s.side == "buy":
            assert s.entry > rng_bars.high.max()
        else:
            assert s.entry < rng_bars.low.min()
    # buy and sell for the same day share an OCO group
    groups = pd.Series([s.oco_group for s in signals]).value_counts()
    assert (groups == 2).all()


def test_winter_time_shifts_with_london():
    bars = random_walk("2024-01-08", 15)   # January: London is UTC+0
    signals = london_breakout.generate(bars, "XAUUSD", PARAMS, DEFAULT_SPECS["XAUUSD"])
    assert all(s.created.hour == 7 for s in signals)


def test_dip_buy_only_buys_dips_in_an_uptrend_and_never_peeks():
    from forexbot.strategies import dip_buy
    idx = pd.date_range("2024-01-01", periods=260, freq="1D", tz="UTC")
    close = pd.Series(np.linspace(100, 200, 260), index=idx)
    close.iloc[250:253] = [190.0, 185.0, 181.0]                       # three sharp down days
    bars = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    params = {"timeframe": "D1", "mode": "rsi2", "trend_sma": 200, "rsi_below": 10, "ibs_below": 0.2,
              "atr_period": 14, "sl_atr": 3.0, "hold_days": 7}
    sigs = dip_buy.generate(bars, "SPX500", params, DEFAULT_SPECS["XAUUSD"])
    assert sigs and all(s.side == "buy" for s in sigs)
    assert {s.created for s in sigs} <= {idx[i] + pd.Timedelta(days=1) for i in (250, 251, 252)}
    later = dip_buy.generate(bars.iloc[:252], "SPX500", params, DEFAULT_SPECS["XAUUSD"])
    assert [s.created for s in later] == [s.created for s in sigs if s.created <= idx[251] + pd.Timedelta(days=1)]
    falling = bars.iloc[::-1].set_axis(idx)                            # downtrend: never buys
    assert dip_buy.generate(falling, "SPX500", params, DEFAULT_SPECS["XAUUSD"]) == []
