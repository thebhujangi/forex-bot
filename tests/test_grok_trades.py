import numpy as np
import pandas as pd

from forexbot import grok_trades as gt
from forexbot.instruments import SymbolSpec
from forexbot.news import Event
from forexbot.signals import HISTORY_COLUMNS

XAU = SymbolSpec("XAUUSD", "XAUUSD", 2, 100, "XAU", "USD")
NOW = pd.Timestamp("2026-11-23 03:01", tz="UTC")
SNAP = {"symbol": "XAUUSD", "bid": 3570.0, "ask": 3570.3, "spread": 0.3, "daily_trend": "down", "atr_1h": 10.0}


def prop(**kw):
    base = dict(symbol="XAUUSD", action="sell", order_type="market", entry=3570.0, stop_loss=3580.0,
                take_profit=3550.0, valid_minutes=120, confidence=70, reason="Lower highs under 3580.")
    return {**base, **kw}


def test_a_good_proposal_becomes_an_exact_trade():
    sig, why = gt.to_signal(prop(), SNAP, XAU, [], NOW)
    assert why == "ok" and sig.side == "sell" and sig.order_type == "market"
    assert sig.entry == 3570.0 and sig.sl == 3580.0 and sig.tp1 == 3550.0     # market sells at the bid
    assert sig.close_by == NOW + pd.Timedelta(hours=24) and sig.expires is None
    assert sig.reason.startswith("Grok (70%)")
    limit, _ = gt.to_signal(prop(order_type="limit", entry=3575.0, stop_loss=3583.0, take_profit=3555.0),
                            SNAP, XAU, [], NOW)
    assert limit.entry == 3575.0 and limit.expires == NOW + pd.Timedelta(minutes=120)


def test_guard_rules_hold_back_the_risky_ones():
    reasons = {
        "trend": prop(action="buy", stop_loss=3560.0, take_profit=3590.0),          # daily trend is down
        "confidence": prop(confidence=55),
        "the stop": prop(take_profit=3560.0),                                        # target 1x the stop
        "ATR": prop(stop_loss=3572.0, take_profit=3560.0),                          # stop 0.2x ATR
        "doesn't fit": prop(order_type="limit", entry=3560.0, stop_loss=3570.0, take_profit=3540.0),  # sell limit below
        "wrong side": prop(stop_loss=3565.0),
    }
    for word, p in reasons.items():
        sig, why = gt.to_signal(p, SNAP, XAU, [], NOW)
        assert sig is None and word in why, (word, why)
    assert gt.to_signal(prop(action="none"), SNAP, XAU, [], NOW) == (None, "no trade")
    news = [Event(NOW + pd.Timedelta(minutes=20), "USD", "CPI", "High", "", "")]
    assert "news" in gt.to_signal(prop(), SNAP, XAU, news, NOW)[1]
    wide = {**SNAP, "spread": 2.0}                                                   # 10-pt stop = 5 spreads
    assert "spread" in gt.to_signal(prop(), wide, XAU, [], NOW)[1]


def test_feed_pauses_when_clearly_losing():
    def hist(results, created="2026-11-24T00:00:00+00:00"):
        return pd.DataFrame([dict(id=str(i), source="x_grok", outcome="sl" if r < 0 else "tp1", result_r=r,
                                  created=created) for i, r in enumerate(results)], columns=HISTORY_COLUMNS)
    assert gt.breaker(hist([-1, 1.5] * 4), None, 10, -0.2, 5) is None               # 8 trades: too early
    assert gt.breaker(hist([-1, -1, 1.5] * 4), None, 10, -0.2, 5) is None           # -0.17x: above the limit
    assert "average" in gt.breaker(hist([-1, -1, 1] * 4), None, 10, -0.2, 5)        # -0.33x over 12, -4x total
    assert gt.breaker(hist([-1, 1.6] * 5), None, 10, -0.2, 5) is None               # winning: keeps going
    assert "in total" in gt.breaker(hist([-1] * 5), None, 10, -0.2, 5)              # 5x risk lost
    assert gt.breaker(hist([-1] * 5), "2026-11-25T00:00:00+00:00", 10, -0.2, 5) is None   # restarted since
    expired = hist([-1] * 6); expired["outcome"] = "expired"                         # never filled: no loss
    assert gt.breaker(expired, None, 10, -0.2, 5) is None


def test_cost_counts_reasoning_tokens_and_prefers_xai_figure():
    usage = {"prompt_tokens": 7000, "completion_tokens": 800, "completion_tokens_details": {"reasoning_tokens": 200}}
    assert abs(gt.cost_usd(usage, 2.0, 6.0) - (7000 * 2 + 1000 * 6) / 1e6) < 1e-12
    assert gt.cost_usd({"cost_in_usd_ticks": 150_000_000}, 2.0, 6.0) == 0.015


def test_snapshot_and_prompt_are_compact():
    idx = pd.date_range("2026-01-01", periods=200, freq="D", tz="UTC")
    d1 = pd.DataFrame({"open": np.linspace(3000, 3600, 200), "high": np.linspace(3010, 3610, 200),
                       "low": np.linspace(2990, 3590, 200), "close": np.linspace(3005, 3605, 200)}, index=idx)
    h1 = d1.tail(40).set_axis(pd.date_range("2026-11-21", periods=40, freq="h", tz="UTC"))
    snap = gt.snapshot("XAUUSD", XAU, h1, h1.tail(20), d1, 3600.0, 3600.3, "up")
    assert snap["spread"] == 0.3 and len(snap["candles_1h_utc [time, open, high, low, close]"]) == 20
    prompt = gt.build_prompt([snap] * 9, [], NOW)
    assert len(prompt) < 40_000                                       # ~9 markets stay well under 10k tokens
