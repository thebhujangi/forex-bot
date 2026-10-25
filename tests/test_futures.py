"""Nasdaq session momentum strategy and the futures (MNQ) messages."""
import numpy as np
import pandas as pd
import pytest

from forexbot.futures import format_futures_signal, fresh, futures_followup, ticks
from forexbot.instruments import DEFAULT_SPECS, Costs
from forexbot.signals import Source, track
from forexbot.strategies import session_momentum

MNQ = DEFAULT_SPECS["MNQ"]
PARAMS = {"timeframe": "M5", "session_tz": "America/New_York", "open_time": "09:30", "close_time": "12:00",
          "ema": 12, "atr_period": 14, "sl_atr": 1.0, "trail_atr": 3.0}
SRC = Source("nasdaq_open", "session_momentum", ["MNQ"], "INTRADAY", "Promising", "stats line",
             runs_on="pc", account_usd=50_000)


def walk(start, days, seed=3):
    idx = pd.date_range(start, periods=days * 288, freq="5min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 20_000 + np.cumsum(rng.normal(0, 8, len(idx)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 4, len(idx))
    low = np.minimum(open_, close) - rng.uniform(0, 4, len(idx))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def test_one_signal_per_weekday_at_0935_new_york_in_summer_and_winter():
    for start, utc_hour in (("2024-06-03", 13), ("2024-01-08", 14)):   # EDT = UTC-4, EST = UTC-5
        bars = walk(start, 7)
        sigs = session_momentum.generate(bars, "MNQ", PARAMS, MNQ)
        assert len(sigs) == 5                                          # Mon-Fri, none at the weekend
        for s in sigs:
            assert (s.created.hour, s.created.minute) == (utc_hour, 35)
            assert s.close_by - s.created == pd.Timedelta(hours=2, minutes=25)
            assert s.trail_distance == pytest.approx(3 * s.stop_distance, abs=0.05)


def test_side_follows_the_first_candle_versus_the_ema_and_never_peeks():
    bars = walk("2024-06-03", 3)
    sigs = session_momentum.generate(bars, "MNQ", PARAMS, MNQ)
    ema = bars.close.ewm(span=12, adjust=False).mean()
    for s in sigs:
        first = s.created - pd.Timedelta(minutes=5)
        assert s.side == ("buy" if bars.close[first] > ema[first] else "sell")
        later = bars.copy()
        later.loc[later.index >= s.created, ["open", "high", "low", "close"]] *= 1.5   # change the future
        again = [x for x in session_momentum.generate(later, "MNQ", PARAMS, MNQ) if x.created == s.created]
        assert again[0].side == s.side and again[0].sl == s.sl


def signal():
    bars = walk("2024-06-03", 2)
    return session_momentum.generate(bars, "MNQ", PARAMS, MNQ)[0]


def test_message_gives_distances_in_whole_ticks_contracts_and_malaysia_times():
    s = signal()
    text = format_futures_signal(s, SRC, MNQ, 50_000, 1.0, "Asia/Kuala_Lumpur", reference="Elev8 NAS100 (MT5)")
    stop, trail = ticks(s.stop_distance), ticks(s.trail_distance)
    assert stop % 0.25 == 0 and trail % 0.25 == 0
    assert f"<code>{stop:,.2f}</code> pts" in text and f"<code>{trail:,.2f}</code> pts" in text
    n = int(500 // (s.stop_distance * 2))                      # $500 risk / ($2 per point x stop)
    assert f"Contracts: <b>{n}</b>" in text
    assert "Close by: <b>00:00</b> Malaysia time" in text      # 12:00 New York in June
    assert "skip it" in text and "Entry:" not in text          # CFD feed: no futures price levels


def test_only_fresh_signals_are_sent():
    s = signal()
    assert fresh(s, s.created + pd.Timedelta(minutes=4))
    assert not fresh(s, s.created + pd.Timedelta(minutes=6))


def candles(rows, start):
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_followup_reports_trailing_stop_and_time_exit():
    from forexbot.models import Signal
    t0 = pd.Timestamp("2024-06-03 13:35", tz="UTC")                      # 09:35 New York
    s = Signal(strategy="session_momentum", symbol="MNQ", side="buy", order_type="market", created=t0,
               entry=20_000.0, sl=19_990.0, tp1=None, trail_distance=30.0,
               close_by=pd.Timestamp("2024-06-03 16:00", tz="UTC"), reason="test")
    # Fill at 20,000, run up to 20,050 (trail now 20,020), then fall to 20,015: closed at +2R.
    run = candles([(20_000, 20_005, 19_998, 20_004), (20_004, 20_050, 20_003, 20_045),
                   (20_045, 20_046, 20_015, 20_016)], t0)
    t = track(s, SRC)
    alerts = futures_followup(t, run, Costs(), t0 + pd.Timedelta(minutes=15))
    assert alerts[0][0].endswith(":trail") and "+2.0x" in alerts[0][1]
    assert t.status == "closed" and t.result_r == pytest.approx(2.0)

    # Quiet market: nothing until 12:00 New York, then "close now".
    t = track(s, SRC)
    flat = candles([(20_000, 20_002, 19_998, 20_001)] * 3, t0)
    assert futures_followup(t, flat, Costs(), t0 + pd.Timedelta(minutes=15)) == []
    alerts = futures_followup(t, flat, Costs(), s.close_by)
    assert alerts[0][0].endswith(":time") and "Close your MNQ now" in alerts[0][1]
    assert t.status == "closed"


def test_with_ibkr_prices_the_message_also_shows_price_levels():
    s = signal()
    text = format_futures_signal(s, SRC, MNQ, 50_000, 1.0, "Asia/Kuala_Lumpur", reference="IBKR live futures",
                                 levels=True)
    assert f"Entry: about <code>{MNQ.fmt(s.entry)}</code>" in text
    assert "different level" not in text
