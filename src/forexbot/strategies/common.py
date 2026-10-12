from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd


def parse_hm(s: str) -> time:
    h, m = (int(x) for x in str(s).split(":"))
    return time(h, m)


def local_to_utc(d: date, t: time, tz: ZoneInfo) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(d, t)).tz_localize(
        tz, ambiguous=True, nonexistent="shift_forward"
    ).tz_convert("UTC")


def daily_bars(bars: pd.DataFrame, tz: ZoneInfo) -> pd.DataFrame:
    """Daily OHLC by local date. Weekend candles are ignored so the Sunday-evening open
    doesn't create a fake short day."""
    local = bars.index.tz_convert(tz)
    weekday = local.weekday < 5
    return bars[weekday].groupby(local[weekday].date).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")
    )


def daily_trend(bars: pd.DataFrame, tz: ZoneInfo, ema_days: int) -> pd.Series:
    """+1 / -1 per local date: was *yesterday's* close above / below its EMA? (no look-ahead)"""
    close = daily_bars(bars, tz)["close"]
    ema = close.ewm(span=ema_days, adjust=False).mean()
    return (close > ema).map({True: 1, False: -1}).shift(1)


def daily_atr(bars: pd.DataFrame, tz: ZoneInfo, period: int = 14) -> pd.Series:
    """ATR of *completed previous* days, indexed by local date (no look-ahead)."""
    days = daily_bars(bars, tz)
    prev_close = days["close"].shift(1)
    tr = pd.concat([
        days["high"] - days["low"],
        (days["high"] - prev_close).abs(),
        (days["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().shift(1)
