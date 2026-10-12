"""Hand-built candles with known outcomes, to prove the engine does what it says."""
import pandas as pd
import pytest

from forexbot.engine import Bars, run_portfolio, simulate
from forexbot.instruments import DEFAULT_SPECS, Costs
from forexbot.models import Signal

T0 = pd.Timestamp("2024-03-04 07:00", tz="UTC")
NO_COST = Costs()


def make_bars(rows):
    """rows = [(open, high, low, close), ...] on consecutive 15-minute candles from T0."""
    idx = pd.date_range(T0, periods=len(rows), freq="15min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    return Bars.from_df(df)


def buy_stop(**kw):
    base = dict(strategy="t", symbol="XAUUSD", side="buy", order_type="stop", created=T0,
                entry=2010.0, sl=2000.0, tp1=2020.0, tp2=2030.0, tp1_close_pct=50)
    return Signal(**{**base, **kw})


def test_buy_stop_hits_tp1_then_tp2():
    bars = make_bars([
        (2005, 2008, 2004, 2007),   # below entry, no fill
        (2007, 2012, 2006, 2011),   # fills at 2010
        (2011, 2021, 2010.5, 2019), # TP1 2020 -> close half, SL to 2010
        (2019, 2031, 2015, 2030),   # TP2 2030
    ])
    r = simulate(buy_stop(), bars, NO_COST)
    assert r.status == "filled" and r.fill_price == 2010
    assert [e.reason for e in r.exits] == ["tp1", "tp2"]
    assert r.r_multiple == pytest.approx(0.5 * 1 + 0.5 * 2)


def test_sl_wins_when_sl_and_tp_in_same_candle():
    bars = make_bars([(2009, 2011, 2009, 2010), (2010, 2025, 1995, 2010)])
    r = simulate(buy_stop(), bars, NO_COST)
    assert r.exit_reason == "sl" and r.r_multiple == pytest.approx(-1)


def test_breakeven_after_tp1():
    bars = make_bars([(2009, 2011, 2009, 2010), (2010, 2021, 2012, 2018), (2018, 2019, 2008, 2009)])
    r = simulate(buy_stop(), bars, NO_COST)
    assert [e.reason for e in r.exits] == ["tp1", "be"]
    assert r.r_multiple == pytest.approx(0.5)


def test_pending_order_expires():
    bars = make_bars([(2005, 2008, 2004, 2007)] * 4)
    r = simulate(buy_stop(expires=T0 + pd.Timedelta(minutes=30)), bars, NO_COST)
    assert r.status == "expired"


def test_spread_and_slippage_make_buys_worse():
    bars = make_bars([(2009, 2011, 2009, 2010), (2010, 2010.5, 1995, 1996)])
    r = simulate(buy_stop(), bars, Costs(spread=0.3, slippage=0.1))
    assert r.fill_price == pytest.approx(2010.1)          # stop entry pays slippage
    assert r.exits[0].price == pytest.approx(1999.9)      # stop loss pays slippage
    assert r.r_multiple < -1


def test_sell_stop_uses_ask_for_stop_loss():
    sig = Signal(strategy="t", symbol="XAUUSD", side="sell", order_type="stop", created=T0,
                 entry=1990.0, sl=2000.0, tp1=1980.0)
    # bid high 1999.8 + spread 0.3 = ask 2000.1 -> stop hit
    bars = make_bars([(1991, 1992, 1989, 1990), (1990, 1999.8, 1989, 1995)])
    r = simulate(sig, bars, Costs(spread=0.3))
    assert r.exit_reason == "sl"


def test_buy_limit_cancelled_if_target_reached_first():
    sig = Signal(strategy="t", symbol="XAUUSD", side="buy", order_type="limit", created=T0,
                 entry=2000.0, sl=1990.0, tp1=2020.0)
    bars = make_bars([(2005, 2010, 2003, 2008), (2008, 2021, 2007, 2020)])
    assert simulate(sig, bars, NO_COST).status == "cancelled"


def test_trailing_stop_locks_in_profit():
    sig = Signal(strategy="t", symbol="XAUUSD", side="buy", order_type="market", created=T0,
                 entry=2000.0, sl=1990.0, tp1=None, trail_distance=10.0)
    bars = make_bars([
        (2000, 2001, 1999, 2000),   # fill at 2000
        (2000, 2030, 2000, 2028),   # best 2030 -> SL trails to 2020 (from the next candle)
        (2028, 2029, 2015, 2016),   # falls through 2020
    ])
    r = simulate(sig, bars, NO_COST)
    assert r.exit_reason == "trail"
    assert r.exits[0].price == pytest.approx(2020)
    assert r.r_multiple == pytest.approx(2.0)


def test_trailing_stop_never_loosens():
    sig = Signal(strategy="t", symbol="XAUUSD", side="sell", order_type="market", created=T0,
                 entry=2000.0, sl=2010.0, tp1=None, trail_distance=10.0)
    bars = make_bars([
        (2000, 2001, 1999, 2000),
        (2000, 2001, 1980, 1985),   # best 1980 -> SL 1990
        (1985, 1989, 1984, 1988),   # no hit, stays 1990
        (1988, 1991, 1987, 1990),   # ask >= 1990 -> out at 1990
    ])
    r = simulate(sig, bars, NO_COST)
    assert r.exit_reason == "trail" and r.r_multiple == pytest.approx(1.0)


def test_signal_validation():
    with pytest.raises(ValueError):
        buy_stop(sl=2015.0)


CFG = {"account": {"starting_balance": 10_000},
       "risk": {"risk_per_trade_pct": 1.0, "max_open_trades": 3, "max_trades_per_symbol": 1,
                "daily_loss_limit_pct": 4.0}}


def test_position_size_and_pnl_gold():
    bars = make_bars([(2009, 2011, 2009, 2010), (2010, 2010.5, 1995, 1996)])
    r = simulate(buy_stop(), bars, NO_COST)
    trades, _ = run_portfolio([r], DEFAULT_SPECS, {"XAUUSD": NO_COST}, CFG)
    # $100 risk / ($10 stop * $100 per $1 per lot) = 0.10 lots
    assert trades.loc[0, "lots"] == pytest.approx(0.10)
    assert trades.loc[0, "pnl"] == pytest.approx(-100)


def test_oco_keeps_first_fill_only():
    buy = buy_stop(oco_group="g")
    sell = Signal(strategy="t", symbol="XAUUSD", side="sell", order_type="stop", created=T0,
                  entry=1990.0, sl=2000.0, tp1=1980.0, oco_group="g")
    bars = make_bars([(2005, 2011, 2004, 2010), (2010, 2012, 1985, 1986), (1986, 1990, 1979, 1980)])
    results = [simulate(s, bars, NO_COST) for s in (buy, sell)]
    trades, skipped = run_portfolio(results, DEFAULT_SPECS, {"XAUUSD": NO_COST}, CFG)
    assert len(trades) == 1 and trades.loc[0, "side"] == "buy"
    assert skipped["oco_other_side"] == 1


def test_daily_loss_limit_blocks_new_trades():
    results = []
    # five losing trades on the same day, one after another
    for k in range(5):
        start = T0 + pd.Timedelta(minutes=30 * k)
        sig = Signal(strategy="t", symbol="XAUUSD", side="buy", order_type="market", created=start,
                     entry=100.0, sl=99.0, tp1=102.0)
        idx = pd.date_range(start, periods=2, freq="15min")
        df = pd.DataFrame([(100, 100.2, 99.9, 100), (100, 100.1, 98, 98.5)],
                          columns=["open", "high", "low", "close"], index=idx)
        results.append(simulate(sig, Bars.from_df(df), NO_COST))
    trades, skipped = run_portfolio(results, DEFAULT_SPECS, {"XAUUSD": NO_COST}, CFG)
    # after 4 losses (~-3.94%) a 5th 1% loss would breach the 4% daily limit
    assert len(trades) == 4
    assert skipped["daily_loss_limit"] == 1
    assert trades.pnl.sum() / 10_000 > -0.04
