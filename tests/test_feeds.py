import pandas as pd

from forexbot.feeds import daily_from_hourly, only_closed


def test_daily_candles_roll_at_5pm_new_york_and_skip_current_day():
    # 3 days of hourly candles starting Sunday 21:00 UTC (17:00 New York in summer)
    idx = pd.date_range("2026-10-04 21:00", periods=72, freq="1h", tz="UTC")
    h1 = pd.DataFrame({"open": range(72), "high": range(1, 73), "low": range(72), "close": range(72)},
                      index=idx, dtype=float)
    now = pd.Timestamp("2026-10-07 22:30", tz="UTC")           # Wednesday's day has just begun
    d1 = daily_from_hourly(h1, now)
    assert list(d1.index.strftime("%m-%d %H:%M")) == ["10-04 21:00", "10-05 21:00", "10-06 21:00"]
    assert d1.iloc[0].open == 0 and d1.iloc[0].close == 23 and d1.iloc[0].high == 24


def test_only_closed_drops_forming_candle():
    idx = pd.date_range("2026-10-07 10:00", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 1, "high": 1, "low": 1, "close": 1}, index=idx, dtype=float)
    assert len(only_closed(df, "M15", pd.Timestamp("2026-10-07 10:40", tz="UTC"))) == 2


def test_elev8_server_clock_changes_on_the_eu_dates():
    from forexbot.timeutils import server_to_utc
    idx = pd.DatetimeIndex(["2026-10-19 00:00", "2026-10-25 23:00", "2026-11-02 00:00"])
    utc = server_to_utc(idx, "eu_dst")
    # week of 19 Oct: summer (UTC+3); week of 26 Oct opens Sunday 21:00 UTC; after 1 Nov still UTC+2
    assert list(utc.strftime("%Y-%m-%d %H:%M")) == ["2026-10-18 21:00", "2026-10-25 21:00", "2026-11-01 22:00"]
