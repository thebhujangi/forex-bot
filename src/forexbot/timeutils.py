"""Time-zone helpers. Everything inside the bot is stored in UTC."""
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
ROLLOVER_SHIFT = pd.Timedelta(hours=7)  # broker day starts at 17:00 New York

TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def server_to_utc(server_times: pd.DatetimeIndex, mode: str | int) -> pd.DatetimeIndex:
    """Convert naive MT5 server times to UTC.

    mode "ny_plus_7": server clock = New York + 7h (GMT+2 winter / GMT+3 summer).
    mode int/str number: fixed offset from UTC in hours.
    """
    if str(mode) == "ny_plus_7":
        ny = (server_times - ROLLOVER_SHIFT).tz_localize(
            NEW_YORK, ambiguous="NaT", nonexistent="shift_forward"
        )
        return ny.tz_convert("UTC")
    offset = pd.Timedelta(hours=int(mode))
    return (server_times - offset).tz_localize("UTC")


def ny_plus_7_offset_now() -> int:
    """Current UTC offset (hours) of a GMT+2/+3 'New York close' broker server."""
    now = pd.Timestamp.now(tz=NEW_YORK)
    return int(now.utcoffset().total_seconds() // 3600) + 7


def trading_day(ts: pd.Timestamp) -> date:
    """Broker trading day: rolls over at 17:00 New York (00:00 server time)."""
    return (ts.tz_convert(NEW_YORK) + ROLLOVER_SHIFT).date()


def to_utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
