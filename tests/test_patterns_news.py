import sys
from pathlib import Path

import numpy as np
import pandas as pd

from forexbot import news_effects as fx
from forexbot.headlines import Headline, classify
from forexbot.instruments import load_specs
from forexbot.mt5_client import confirm
from forexbot.news import Event, relevant
from forexbot.strategies import chart_patterns as cp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
NOW = pd.Timestamp("2026-11-26 12:00", tz="UTC")


def frame(closes, freq="4h", wick=0.2):
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2026-01-01", periods=len(c), freq=freq, tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c}, index=idx)


def test_an_old_copy_of_a_candle_is_not_trusted():
    d = frame([1, 2, 3, 4], "D")
    stale = d.iloc[:3]                                  # MT5 hasn't started the next candle: last close may be old
    assert list(confirm(stale, stale.index).close) == [1, 2]
    assert list(confirm(stale, d.index).close) == [1, 2, 3]     # a newer candle exists: final
    assert confirm(stale.iloc[:0], stale.index[:0]).empty


def test_double_top_and_bottom_are_found_on_the_right_side():
    warm = list(100 + np.sin(np.arange(40)))

    def leg(a, b, n):
        return list(np.linspace(a, b, n + 1)[1:])
    bottom = warm + leg(warm[-1], 80, 15) + leg(80, 88, 8) + leg(88, 80, 8) + leg(80, 95, 14)
    f = cp.find(frame(bottom, wick=0.8))
    hit = f[f.name == "double_bottom"]
    assert len(hit) == 1 and hit.side.iloc[0] == "buy"
    assert hit.sl.iloc[0] < 80 and hit.entry.iloc[0] > 88 and hit.tp.iloc[0] > 94
    g = cp.find(frame(200 - np.array(bottom), wick=0.8))          # the same chart upside down
    top = g[g.name == "double_bottom"]
    assert len(top) == 1 and top.side.iloc[0] == "sell" and top.i.iloc[0] == hit.i.iloc[0]


def test_patterns_never_use_future_candles():
    rng = np.random.default_rng(3)
    d = frame(100 + rng.normal(0, 1, 1500).cumsum())
    full, cut = cp.find(d), cp.find(d.iloc[:1000])
    key = ["name", "side", "i", "sl", "tp"]
    a = set(map(tuple, full[full.i < 1000][key].round(6).values.tolist()))
    assert len(a) > 100 and a == set(map(tuple, cut[key].round(6).values.tolist()))


def test_every_pattern_has_a_label_and_signals_are_valid():
    assert set(cp.LABELS) == set(cp.ALL) and len(cp.ALL) == 43
    rng = np.random.default_rng(5)
    d = frame(1000 + rng.normal(0, 1, 800).cumsum())
    spec = load_specs()["XAUUSD"]
    sigs = cp.generate(d, "XAUUSD", {"timeframe": "H4"}, spec)
    assert sigs and all(s.close_by > s.created and s.tp1 is not None for s in sigs)
    only = cp.generate(d, "XAUUSD", {"timeframe": "H4", "patterns": ["engulfing"]}, spec)
    assert only and len(only) < len(sigs)


def ev(title, cur="USD", impact="High", mins=10):
    return Event(NOW + pd.Timedelta(minutes=mins), cur, title, impact, "", "")


def test_silver_oil_and_index_get_news_warnings():
    end = NOW + pd.Timedelta(hours=1)
    for s in ("XAGUSD", "XTIUSD", "XBRUSD", "SPX500", "NAS100"):
        assert relevant([ev("CPI m/m")], [s], NOW, end)
    oil = ev("Crude Oil Inventories", impact="Low")
    opec = ev("OPEC-JMMC Meetings", cur="All", impact="Medium")
    assert len(relevant([oil, opec], ["XTIUSD"], NOW, end)) == 2
    assert not relevant([oil, opec], ["EURUSD"], NOW, end)


def test_release_facts_come_from_measured_data():
    assert fx.release_of("Core CPI m/m") == "CPI" and fx.release_of("Non-Farm Employment Change") == "Non-Farm Payrolls"
    assert fx.release_of("Core PCE Price Index m/m") == "Core PCE" and fx.release_of("Bank Holiday") is None
    assert "Gold" in fx.impact_text("XAUUSD", ev("Non-Farm Employment Change"))
    assert fx.impact_text("XAUUSD", ev("CPI m/m", cur="GBP")) is None        # only US releases were measured


def test_headline_effects_and_lean():
    assert fx.headline_effects("OPEC+ agrees surprise output cut")[0] == {"oil": 1}
    assert fx.headline_effects("Fed signals rate cut in December")[0]["gold"] == 1
    war = fx.headline_effects("Missile strike hits tanker")[0]
    assert war["oil"] == 1 and war["stocks"] == -1
    assert fx.headline_effects("Israel and Hamas agree ceasefire as war pauses")[0]["oil"] == -1
    assert fx.headline_effects("Gold surges to record high") == ({}, [])     # reports a price, isn't a cause
    assert fx.headline_effects("BoE flags further rate hikes")[0] == {"pound": 1}      # lifts its own currency
    assert fx.headline_effects("Hawkish BoC outlook lifts Canadian Dollar")[0] == {}
    assert fx.headline_effects("Euro slips on hawkish Fed")[0]["euro"] == -1
    h = [Headline("x", "Missile strike hits tanker", "a", NOW, ["USD"], True),
         Headline("y", "Missile strike hits tanker!", "b", NOW, ["USD"], True),
         Headline("z", "Missile strike hits port", "c", NOW - pd.Timedelta(hours=9), ["USD"], True)]
    assert fx.lean("XTIUSD", h, NOW)[0] == 1                                  # duplicate and old ones don't count
    assert "goes against this SELL" in fx.lean_text("XTIUSD", "sell", h, NOW)


def test_general_news_is_pushed_only_with_a_clear_effect():
    w = ["XTIUSD", "SPX500"]
    assert classify("CNBC", "Wall Street stocks surge at the open", w) == (["SPX500"], False)
    assert classify("OilPrice", "OPEC output cut: emergency meeting", w) == (["XTIUSD"], True)


class FakeMarket:
    def __init__(self, bars):
        self.b = bars

    def bars(self, *a, **k):
        return self.b


def make_bot(bars, monkeypatch):
    import run_bot as rb
    from forexbot.config import load_config
    monkeypatch.setattr(rb, "log", lambda m: None)
    bot = rb.Bot.__new__(rb.Bot)
    bot.cfg = load_config()
    bot.sources = [s for s in (rb.Source(**x) for x in bot.cfg["signals"]) if s.name == "crypto_trend"]
    bot.state, bot.tracked, bot.specs = {"sent": {}, "last_signal_bar": {}}, [], load_specs()
    bot.market, bot.sent = FakeMarket(bars), []
    bot.send_signals = lambda src, sym, sigs, events: bot.sent.append((sym, sigs[0].side))
    return bot, rb


def eleven_minutes_ago(bot):
    bot.state["decided"]["crypto_trend:BTCUSD"]["at"] = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=11)).isoformat()


def test_a_signal_missed_on_an_old_candle_is_sent_once_the_candle_is_corrected(monkeypatch):
    final = frame([100 + (i % 5) for i in range(80)] + [90.0], "D")
    old = final.copy()
    old.iloc[-1, old.columns.get_loc("close")] = 101.0            # what MT5 handed out at first
    bot, rb = make_bot(old, monkeypatch)
    src, params = bot.sources[0], {**bot.cfg["strategies"]["daily_trend"]}
    assert rb.new_signals(src, old, "BTCUSD", params, bot.specs["BTCUSD"], final.index[-1]) == []
    bot.note_candle(src, "BTCUSD", params, old, [])
    eleven_minutes_ago(bot)
    bot.market = FakeMarket(final)
    bot.recheck_candles([])
    assert bot.sent == [("BTCUSD", "sell")]
    bot.recheck_candles([])
    assert len(bot.sent) == 1                                     # and only once


def test_a_wrong_signal_is_withdrawn(monkeypatch):
    final = frame([100 + (i % 5) for i in range(80)] + [101.0], "D")
    old = final.copy()
    old.iloc[-1, old.columns.get_loc("close")] = 90.0
    bot, rb = make_bot(old, monkeypatch)
    src, params = bot.sources[0], {**bot.cfg["strategies"]["daily_trend"]}
    wrong = rb.new_signals(src, old, "BTCUSD", params, bot.specs["BTCUSD"], final.index[-1])
    assert len(wrong) == 1
    bot.tracked = [rb.track(wrong[0], src)]
    bot.note_candle(src, "BTCUSD", params, old, wrong)
    eleven_minutes_ago(bot)
    bot.market = FakeMarket(final)
    bot.recheck_candles([])
    assert bot.sent == [] and "ignore my SELL BTCUSD" in bot.state["outbox"][0]["text"]


def test_a_losing_pattern_feed_pauses_itself(monkeypatch, tmp_path):
    bot, rb = make_bot(frame([1, 2]), monkeypatch)
    src = next(rb.Source(**x) for x in bot.cfg["signals"] if x["name"] == "x_patterns_h4")
    hist = tmp_path / "h.csv"
    monkeypatch.setattr(rb, "HISTORY_FILE", hist)
    row = dict(id="a", source="x_patterns_h4", horizon="INTRADAY", symbol="BTCUSD", side="buy", entry=1, sl=0.5,
               created=NOW.isoformat(), closed_at=NOW.isoformat(), placed=False, result_r=-0.5, outcome="sl")
    pd.DataFrame([row] * 19).to_csv(hist, index=False)
    assert bot.feed_paused(src) is False
    bot.state["timers"] = {}
    pd.DataFrame([row] * 20).to_csv(hist, index=False)
    assert bot.feed_paused(src) is True and "paused" in bot.state["outbox"][0]["text"]
