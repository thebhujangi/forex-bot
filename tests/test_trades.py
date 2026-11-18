import pandas as pd

from forexbot.instruments import SymbolSpec
from forexbot.models import Signal
from forexbot.signals import Source, track
from forexbot.trades import closed_message, link_message, match_signal

ETH = SymbolSpec("ETHUSD", "ETHUSD", 2, 10, "ETH", "USD")
SRC = Source("crypto_trend", "daily_trend", ["ETHUSD"], "LONG-TERM", "Promising", "stats")
T0 = pd.Timestamp("2026-11-10 22:00", tz="UTC")


def sell(entry, created=T0, sl=None):
    return track(Signal(strategy="daily_trend", symbol="ETHUSD", side="sell", order_type="market",
                        created=created, entry=entry, sl=sl or entry + 300, tp1=None, trail_distance=480.0), SRC)


def test_trade_links_to_the_closest_signal_opened_after_it():
    old, new = sell(2268.72, T0 - pd.Timedelta(days=12)), sell(2053.04)
    later = T0 + pd.Timedelta(hours=1)
    assert match_signal("ETHUSD", "sell", later, 2055.0, [old, new]) is new
    assert match_signal("ETHUSD", "sell", later, 2270.0, [old, new]) is old       # closest price wins
    assert match_signal("ETHUSD", "buy", later, 2055.0, [old, new]) is None        # wrong direction
    assert match_signal("ETHUSD", "sell", T0 - pd.Timedelta(hours=2), 2055.0, [new]) is None  # before the signal
    new.status = "closed"
    assert match_signal("ETHUSD", "sell", later, 2055.0, [new]) is None            # finished signals don't link


def test_closed_message_reports_money_and_risk_and_whether_signal_continues():
    t = sell(2053.04, sl=2345.08)                                                 # risk 292.04
    h = {"ticket": 7, "symbol": "ETHUSD", "side": "sell", "lots": 0.05, "open_price": 2053.04,
         "close_price": 1761.0, "profit": 146.02}
    text = closed_message(h, t, 0, ETH.fmt)
    assert "Closed in MT5: SELL ETHUSD" in text and "+$146.02" in text and "+1.0x" in text
    assert "stopped following" in text
    assert "still open" in closed_message(h, t, 1, ETH.fmt)
    assert "Not from one of my signals" in link_message("ETHUSD", "sell", 0.05, 2053.04, None, ETH.fmt)
