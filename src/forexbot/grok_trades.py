"""⚗️ Grok trade signals (user's request 2026-11-23): every hour Grok reads the live Elev8 candles
and proposes exact trades (entry, stop, target) or none. No cap on how many.

UNTESTED. A language model can't be backtested honestly (it has read past prices), so these are
judged live, on demo, in the report card. Grok only proposes; the bot's own guard rules decide what
reaches the phone, to cut the trades most likely to lose:
  - only WITH the daily trend (the one thing that tested positive on crypto and metals here)
  - target at least 1.5x the stop distance, stop 0.5-3x the hourly ATR and 10x+ the spread
    (smaller stops are mostly eaten by the spread)
  - no new trade 30 minutes either side of high-impact news for that market
  - one open Grok trade per market, pending orders valid at most 4 hours, trades closed after 24 h
  - Grok's own confidence 60+
The bot also pauses the feed when the live results are clearly losing (see bot.grok_trades).
"""
import json
import urllib.request

import pandas as pd

from .grok import API_URL, market_facts
from .instruments import SymbolSpec
from .models import Signal
from .news import Event, relevant

SCHEMA = {
    "type": "object",
    "properties": {"trades": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "action": {"type": "string", "enum": ["buy", "sell", "none"]},
            "order_type": {"type": "string", "enum": ["market", "limit", "stop"]},
            "entry": {"type": "number"},
            "stop_loss": {"type": "number"},
            "take_profit": {"type": "number"},
            "valid_minutes": {"type": "integer", "minimum": 15, "maximum": 240},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string"},
        },
        "required": ["symbol", "action", "order_type", "entry", "stop_loss", "take_profit",
                     "valid_minutes", "confidence", "reason"], "additionalProperties": False}}},
    "required": ["trades"], "additionalProperties": False,
}

SYSTEM = ("You are a disciplined intraday trader. For each market, propose at most one trade for the next "
          "few hours, or action 'none'. A trade needs an exact entry (for 'market' use the current price), a "
          "stop loss beyond a real level and a take profit at least 1.5 times as far as the stop. Spread is a "
          "real cost: avoid stops so tight that the spread eats them. Trading with the daily trend is "
          "preferred. Most hours most markets have no good trade, so 'none' is usually right. Use only the "
          "data provided. Keep each reason to one short sentence.")

DEFAULTS = {"with_trend_only": True, "min_reward_risk": 1.5, "min_stop_atr": 0.5, "max_stop_atr": 3.0,
            "min_stop_spreads": 10, "min_confidence": 60, "news_minutes": 30, "max_valid_minutes": 240,
            "max_hold_hours": 24, "max_entry_atr": 1.5}


def atr(bars: pd.DataFrame, n: int = 14) -> float:
    prev = bars["close"].shift(1)
    tr = pd.concat([bars.high - bars.low, (bars.high - prev).abs(), (bars.low - prev).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-n:].mean())


def snapshot(symbol: str, spec: SymbolSpec, h1: pd.DataFrame, m15: pd.DataFrame, d1: pd.DataFrame,
             bid: float, ask: float, trend: str) -> dict:
    """What Grok sees for one market: compact candles plus the daily picture."""
    r = lambda x: round(float(x), spec.digits)
    candles = lambda b: [[f"{t:%d %H:%M}", r(o), r(h), r(lo), r(c)] for t, o, h, lo, c in
                         zip(b.index, b.open, b.high, b.low, b.close)]
    return {"symbol": symbol, "bid": r(bid), "ask": r(ask), "spread": r(ask - bid), "daily_trend": trend,
            "atr_1h": r(atr(h1)), "daily": market_facts(symbol, d1, (bid + ask) / 2),
            "candles_1h_utc [time, open, high, low, close]": candles(h1.tail(20)),
            "candles_15m_utc": candles(m15.tail(12))}


def build_prompt(snaps: list[dict], events: list[str], now: pd.Timestamp) -> str:
    return (f"Time now: {now:%Y-%m-%d %H:%M} UTC.\n\nMarkets:\n{json.dumps(snaps, separators=(',', ':'))}\n\n"
            "Scheduled high-impact news in the next 12 hours:\n" + ("\n".join(f"- {e}" for e in events) or "- none")
            + "\n\nGive one entry per market symbol listed above.")


def ask(api_key: str, model: str, prompt: str, timeout: int = 120) -> tuple[list[dict], dict]:
    """Grok's proposals and the token usage of the call."""
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "trades", "schema": SCHEMA, "strict": True}}}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    return json.loads(out["choices"][0]["message"]["content"])["trades"], out.get("usage") or {}


def cost_usd(usage: dict, price_in: float, price_out: float) -> float:
    """Cost of one call from its token counts (prices in US$ per million tokens). Reasoning tokens are
    counted on top of the answer, so this errs on the high side."""
    if usage.get("cost_in_usd_ticks"):
        return usage["cost_in_usd_ticks"] / 1e10
    out = usage.get("completion_tokens", 0) + (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    return (usage.get("prompt_tokens", 0) * price_in + out * price_out) / 1e6


def to_signal(p: dict, snap: dict, spec: SymbolSpec, events: list[Event], now: pd.Timestamp,
              rules: dict | None = None) -> tuple[Signal | None, str]:
    """Grok's proposal as a Signal, or None and the guard rule it failed."""
    g = {**DEFAULTS, **(rules or {})}
    side = p["action"]
    if side == "none":
        return None, "no trade"
    if g["with_trend_only"] and snap["daily_trend"] != ("up" if side == "buy" else "down"):
        return None, f"against the daily trend ({snap['daily_trend']})"
    if p["confidence"] < g["min_confidence"]:
        return None, f"confidence {p['confidence']} < {g['min_confidence']}"
    sign = 1 if side == "buy" else -1
    market = snap["ask"] if side == "buy" else snap["bid"]
    kind = p["order_type"]
    entry = market if kind == "market" else round(p["entry"], spec.digits)
    a = snap["atr_1h"]
    if kind != "market":
        # buy limit below the price / buy stop above it (and the reverse for sells), not too far away
        if (kind == "limit") != (sign * (market - entry) > 0) or abs(entry - market) > g["max_entry_atr"] * a:
            return None, f"{kind} entry {entry} doesn't fit the price {market}"
    sl, tp = round(p["stop_loss"], spec.digits), round(p["take_profit"], spec.digits)
    d = sign * (entry - sl)
    if d <= 0 or sign * (tp - entry) <= 0:
        return None, "stop or target on the wrong side"
    if not g["min_stop_atr"] * a <= d <= g["max_stop_atr"] * a:
        return None, f"stop {d / a:.2f}x the hourly ATR (allowed {g['min_stop_atr']}-{g['max_stop_atr']})"
    if d < g["min_stop_spreads"] * snap["spread"]:
        return None, f"stop only {d / snap['spread']:.0f}x the spread"
    if sign * (tp - entry) < g["min_reward_risk"] * d:
        return None, f"target only {sign * (tp - entry) / d:.2f}x the stop"
    window = pd.Timedelta(minutes=g["news_minutes"])
    if relevant(events, [snap["symbol"]], now - window, now + window):
        return None, "high-impact news within 30 minutes"
    valid = pd.Timedelta(minutes=min(p["valid_minutes"], g["max_valid_minutes"]))
    return Signal(strategy="grok", symbol=snap["symbol"], side=side, order_type=kind, created=now, entry=entry,
                  sl=sl, tp1=tp, tp1_close_pct=100.0, move_sl_to_be=False,
                  expires=None if kind == "market" else now + valid,
                  close_by=now + pd.Timedelta(hours=g["max_hold_hours"]),
                  reason=f"Grok ({p['confidence']}%): {p['reason']}"), "ok"


def closed_trades(history: pd.DataFrame, since: str | None = None) -> pd.DataFrame:
    """Grok trades that triggered and finished (unfilled pending orders cost nothing)."""
    h = history[(history["source"] == "x_grok") & ~history["outcome"].fillna("").isin(["expired", "oco"])]
    if since and len(h):
        h = h[pd.to_datetime(h["created"], utc=True, format="ISO8601") >= pd.Timestamp(since)]
    return h


def breaker(history: pd.DataFrame, since: str | None, min_trades: int, below_r: float,
            max_loss_r: float) -> str | None:
    """Why the Grok feed should pause (results since `since`), or None. Pauses when the average is
    clearly negative after `min_trades`, or the total loss reaches `max_loss_r` (x risk) at any point."""
    h = closed_trades(history, since)
    total = float(h["result_r"].sum()) if len(h) else 0.0
    if total <= -max_loss_r:
        return f"{len(h)} trades lost {total:+.1f}x risk in total (limit -{max_loss_r:g}x)"
    if len(h) >= min_trades and h["result_r"].mean() < below_r:
        return f"{len(h)} trades average {h['result_r'].mean():+.2f}x risk (limit {below_r:+g}x)"
    return None
