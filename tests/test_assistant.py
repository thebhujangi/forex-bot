import pandas as pd
import pytest

from forexbot.assistant import MonitorContext, Position, check_positions, position_size
from forexbot.instruments import DEFAULT_SPECS
from forexbot.news import Event, parse_events, relevant

NOW = pd.Timestamp("2026-10-08 12:00", tz="UTC")   # Thursday


def ctx(**kw):
    base = dict(now=NOW, specs=DEFAULT_SPECS, events=[], initial_sl={}, equity=10_000, day_start_equity=10_000)
    return MonitorContext(**{**base, **kw})


def gold(**kw):
    base = dict(ticket=1, symbol="XAUUSD", side="buy", lots=0.1, open_price=4100.0, sl=4090.0, tp=0,
                price=4101.0, profit=10.0)
    return Position(**{**base, **kw})


def test_position_size_gold_and_eurusd():
    r = position_size(DEFAULT_SPECS["XAUUSD"], 10_000, 1.0, 4105.5, 4098.0)
    assert r["lots"] == pytest.approx(0.13)            # $100 / ($7.5 x 100 per lot) = 0.133
    r = position_size(DEFAULT_SPECS["EURUSD"], 10_000, 1.0, 1.1200, 1.1180)
    assert r["lots"] == pytest.approx(0.5)             # $100 / (20 pips x $10) = 0.5


def test_position_size_too_small_returns_zero():
    r = position_size(DEFAULT_SPECS["XAUUSD"], 10_000, 0.1, 4100, 3900)
    assert r["lots"] == 0 and r["min_lot_risk_usd"] == pytest.approx(200)


def keys(alerts):
    return [a.key.split(":")[0] for a in alerts]


def test_no_stop_loss_alert():
    assert keys(check_positions([gold(sl=0)], ctx())) == ["nosl"]


def test_breakeven_suggested_after_1r_only_once_sl_still_at_risk():
    assert "be" in keys(check_positions([gold(price=4110.5)], ctx()))
    assert "be" not in keys(check_positions([gold(price=4105)], ctx()))
    assert "be" not in keys(check_positions([gold(price=4115, sl=4100)], ctx()))   # already at BE


def test_breakeven_uses_initial_stop_for_sell():
    p = gold(side="sell", open_price=4100, sl=4110, price=4089)
    assert "be" in keys(check_positions([p], ctx(initial_sl={1: 4110})))


def test_news_warning_only_for_relevant_currency_and_window():
    ev = [Event(NOW + pd.Timedelta(minutes=20), "USD", "CPI m/m", "High", "0.3%", "0.2%"),
          Event(NOW + pd.Timedelta(minutes=20), "JPY", "BOJ", "High", "", ""),
          Event(NOW + pd.Timedelta(minutes=90), "USD", "Later", "High", "", "")]
    alerts = check_positions([gold()], ctx(events=ev))
    news = [a for a in alerts if a.key.startswith("news")]
    assert len(news) == 1 and "CPI" in news[0].text and "20 min" in news[0].text


def test_daily_loss_warning_and_stop():
    assert keys(check_positions([], ctx(equity=9_650))) == ["daily_warn"]
    assert keys(check_positions([], ctx(equity=9_550))) == ["daily_stop"]


def test_weekend_warning_friday_afternoon_new_york():
    friday = pd.Timestamp("2026-10-09 15:30", tz="America/New_York").tz_convert("UTC")
    assert "weekend" in keys(check_positions([gold()], ctx(now=friday)))


def test_spike_alert():
    assert "spike" in keys(check_positions([gold()], ctx(spikes={"XAUUSD": 4.2})))


def test_parse_forex_factory_feed():
    raw = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-07T08:30:00-04:00",
            "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
           {"title": "bad", "country": "USD", "date": "not a date"}]
    ev = parse_events(raw)
    assert len(ev) == 1 and ev[0].time == pd.Timestamp("2026-10-07 12:30", tz="UTC")
    assert relevant(ev, ["EURUSD"], ev[0].time - pd.Timedelta(minutes=1), ev[0].time + pd.Timedelta(minutes=1))
