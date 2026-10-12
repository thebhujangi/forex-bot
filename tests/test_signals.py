import pandas as pd
import pytest

from forexbot.instruments import SymbolSpec
from forexbot.models import Signal
from forexbot.signals import (Source, follow, follow_replay, format_signal, new_signals, reversal_alert, track,
                              weekly_report)

BTC = SymbolSpec("BTCUSD", "BTCUSD", 2, 1, "BTC", "USD")
SRC = Source("crypto_trend", "daily_trend", ["BTCUSD"], "LONG-TERM", "Proven", "stats line")
T0 = pd.Timestamp("2026-01-05 21:00", tz="UTC")


def sig(side="buy", **kw):
    base = dict(strategy="daily_trend", symbol="BTCUSD", side=side, order_type="market", created=T0,
                entry=80_000.0, sl=74_000.0 if side == "buy" else 86_000.0, tp1=None, trail_distance=10_000.0,
                reason="Daily close broke the 55-day high.")
    return Signal(**{**base, **kw})


def hl(high, low):
    return pd.DataFrame({"high": [high], "low": [low]}, index=[T0 + pd.Timedelta(hours=1)])


def test_trailing_stop_message_only_when_placed_and_moved_enough():
    t = track(sig(), SRC)
    assert follow(t, hl(92_000, 80_000), 91_000, BTC) == []      # not placed yet: no stop-move nag
    t.status = "placed"
    alerts = follow(t, hl(92_000, 80_000), 91_000, BTC)
    assert t.cur_sl == 82_000 and [k.split(":")[0] for k, _ in alerts] == ["trail"]
    assert follow(t, hl(93_000, 90_000), 92_500, BTC) == []      # +1000 < 2500 step: stay quiet


def test_stopped_out_reports_result_and_closes():
    t = track(sig(), SRC)
    t.status = "placed"
    follow(t, hl(95_000, 80_000), 94_000, BTC)                    # stop trails to 85,000
    alerts = follow(t, hl(94_000, 84_000), 84_900, BTC)
    assert alerts[0][0].startswith("stopped") and "+0.8x" in alerts[0][1]
    assert t.status == "closed"


def test_losing_badly_warning_once():
    t = track(sig(), SRC)
    t.status = "placed"
    assert follow(t, hl(80_500, 75_000), 75_400, BTC)[0][0].startswith("loss")
    assert follow(t, hl(80_500, 75_000), 75_300, BTC) == []


def test_reversal_closes_opposite_signal():
    t = track(sig(), SRC)
    assert reversal_alert(t, sig("sell"), BTC) is not None and t.status == "closed"
    t2 = track(sig(), SRC)
    assert reversal_alert(t2, sig("buy"), BTC) is None


def test_skipped_signal_is_followed_quietly_and_scored():
    t = track(sig(), SRC)
    t.status = "skipped"
    assert follow(t, hl(95_000, 80_000), 94_000, BTC) == []      # no stop-move pings
    assert follow(t, hl(94_000, 84_000), 84_900, BTC) == []      # no stopped ping either
    assert t.status == "closed" and not t.placed and t.result_r == pytest.approx(5_000 / 6_000)


def test_weekly_report_counts_and_totals():
    now = pd.Timestamp("2026-10-11 12:00", tz="UTC")
    hist = pd.DataFrame([
        {"id": "a", "source": "crypto_trend", "horizon": "LONG-TERM", "symbol": "BTCUSD", "side": "buy",
         "entry": 1, "sl": 0.9, "created": "2026-10-01T21:00:00+00:00", "closed_at": "2026-10-09T10:00:00+00:00",
         "placed": True, "result_r": 2.5},
        {"id": "b", "source": "crypto_trend", "horizon": "LONG-TERM", "symbol": "ETHUSD", "side": "sell",
         "entry": 1, "sl": 1.1, "created": "2026-09-01T21:00:00+00:00", "closed_at": "2026-09-20T10:00:00+00:00",
         "placed": False, "result_r": -1.0},
    ])
    open_t = track(sig(created=pd.Timestamp("2026-10-07 21:00", tz="UTC")), SRC)
    text = weekly_report(hist, [(open_t, -0.4)], now, {"trades": 3, "profit": 120.5, "balance": 10_120.5})
    assert "1 finished" in text and "BUY BTCUSD: +2.5x" in text and "you placed it" in text
    assert "crypto_trend: 2 trades · win rate 50% · total +1.5x" in text and "-0.4x risk now" in text
    assert "$+120.50" in text


INTRA = Source("x_test", "london_breakout", ["BTCUSD"], "INTRADAY", "EXPERIMENTAL", "stats")


def m15(rows, start=T0):
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def intraday_sig(**kw):
    base = dict(strategy="london_breakout", symbol="BTCUSD", side="buy", order_type="stop", created=T0,
                entry=80_100.0, sl=79_900.0, tp1=80_300.0, tp2=80_500.0, tp1_close_pct=50,
                expires=T0 + pd.Timedelta(hours=1), close_by=T0 + pd.Timedelta(hours=8))
    return Signal(**{**base, **kw})


def test_intraday_replay_fill_tp1_tp2():
    from forexbot.instruments import Costs
    t = track(intraday_sig(), INTRA)
    t.status = "placed"
    bars = m15([(80_000, 80_050, 79_950, 80_020)])
    assert follow_replay(t, bars, Costs(), BTC, T0 + pd.Timedelta(minutes=20)) == []    # pending
    bars = m15([(80_000, 80_050, 79_950, 80_020), (80_020, 80_150, 80_010, 80_140),
                (80_140, 80_320, 80_130, 80_300), (80_300, 80_520, 80_290, 80_500)])
    keys = [k.split(":")[0] for k, _ in follow_replay(t, bars, Costs(), BTC, T0 + pd.Timedelta(hours=1))]
    assert keys == ["filled", "exit", "exit", "done"]
    assert t.status == "closed" and t.result_r == pytest.approx(0.5 * 1 + 0.5 * 2)


def test_linked_orders_one_message_and_other_side_cancelled():
    from forexbot.instruments import Costs
    from forexbot.signals import cancel_sibling, format_pair
    buy = intraday_sig(oco_group="g1")
    sell = intraday_sig(side="sell", entry=79_900.0, sl=80_100.0, tp1=79_700.0, tp2=79_500.0, oco_group="g1")
    text = format_pair([buy, sell], INTRA, BTC, 10_000, 1.0, [], "Asia/Kuala_Lumpur")
    assert "TWO ORDERS" in text and "BUY STOP" in text and "SELL STOP" in text
    tb, ts = track(buy, INTRA), track(sell, INTRA)
    tb.status = ts.status = "placed"
    bars = m15([(80_000, 80_150, 79_990, 80_140)])               # buy side fills
    follow_replay(tb, bars, Costs(), BTC, T0 + pd.Timedelta(minutes=30))
    alerts = cancel_sibling(tb, [tb, ts], BTC, T0 + pd.Timedelta(minutes=30))
    assert alerts[0][0].startswith("oco") and ts.status == "closed" and ts.outcome == "oco"


def test_intraday_unfilled_order_expires():
    from forexbot.instruments import Costs
    t = track(intraday_sig(), INTRA)
    t.status = "placed"
    bars = m15([(80_000, 80_050, 79_950, 80_020)] * 5)
    alerts = follow_replay(t, bars, Costs(), BTC, T0 + pd.Timedelta(hours=2))
    assert alerts[0][0].startswith("expired") and t.outcome == "expired" and t.result_r == 0


def test_format_mentions_horizon_lot_and_min_lot_warning():
    text = format_signal(sig(), SRC, BTC, 10_000, 1.0, [], "Asia/Kuala_Lumpur")
    assert "LONG-TERM" in text and "BUY NOW" in text
    # $100 risk / $6,000 stop per 1 BTC lot = 0.016 -> 0.01 lots
    assert "<b>0.01</b>" in text
    tiny = format_signal(sig(sl=60_000.0), SRC, BTC, 1_000, 1.0, [], "Asia/Kuala_Lumpur")
    assert "smallest size" in tiny


def test_new_signals_only_from_latest_candles():
    idx = pd.date_range("2025-01-01 21:00", periods=200, freq="1D", tz="UTC")
    close = pd.Series(range(200), index=idx, dtype=float) + 100
    bars = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    bars.iloc[-1] = [400, 420, 399, 419]                                # breakout on the last candle
    params = {"timeframe": "D1", "entry_days": 55, "atr_period": 20, "sl_atr": 3.0, "trail_atr": 5.0}
    all_sigs = new_signals(SRC, bars, "BTCUSD", params, BTC, None)
    fresh = new_signals(SRC, bars, "BTCUSD", params, BTC, idx[-1])
    assert len(fresh) == 1 and fresh[0].created == idx[-1] + pd.Timedelta(days=1)
    assert len(all_sigs) >= len(fresh)
