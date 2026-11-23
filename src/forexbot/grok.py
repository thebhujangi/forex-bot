"""Grok experiment: once a day, ask xAI's Grok for a 24-hour call on each market, log it, score it.

PAPER ONLY. Grok's calls are never sent as signals or traded. Backtests of language models are
contaminated (they have read past prices), so the only honest test is forward: log a call every
day, check it 24 hours later, and compare with a coin flip and with the plain trend direction.
Grok is given what the bot sees (prices, trend levels, headlines, scheduled news) and can say
"none" when it has no view.

Needs XAI_API_KEY in .env (console.x.ai -> API Keys). About US$0.01 a day.
"""
import json
import math
import urllib.request
from pathlib import Path

import pandas as pd

API_URL = "https://api.x.ai/v1/chat/completions"
COLUMNS = ["time", "symbol", "price", "call", "confidence", "reason", "model", "trend",
           "price_24h", "move_pct", "correct", "trend_correct"]

SCHEMA = {
    "type": "object",
    "properties": {"calls": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "call": {"type": "string", "enum": ["up", "down", "none"]},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason": {"type": "string"},
        },
        "required": ["symbol", "call", "confidence", "reason"], "additionalProperties": False}}},
    "required": ["calls"], "additionalProperties": False,
}

SYSTEM = ("You are a careful market analyst. For each market, say whether its price will be higher (up) "
          "or lower (down) in exactly 24 hours than the price given, or 'none' if you have no real view. "
          "Use only the data provided. Be honest about uncertainty: 'none' is a good answer when the "
          "evidence is mixed. Keep each reason to one short sentence.")


def market_facts(symbol: str, d1: pd.DataFrame, price: float) -> dict:
    c = d1["close"]
    prev = c.shift(1)
    tr = pd.concat([d1.high - d1.low, (d1.high - prev).abs(), (d1.low - prev).abs()], axis=1).max(axis=1)
    pct = lambda n: round((price / float(c.iloc[-n - 1]) - 1) * 100, 2) if len(c) > n else None
    return {"symbol": symbol, "price_now": price, "change_1d_pct": pct(1), "change_5d_pct": pct(5),
            "change_20d_pct": pct(20), "daily_range_pct": round(float(tr.iloc[-20:].mean()) / price * 100, 2),
            "high_55d": float(d1.high.iloc[-56:-1].max()), "low_55d": float(d1.low.iloc[-56:-1].min()),
            "above_100d_average": bool(price > float(c.iloc[-100:].mean()))}


def build_prompt(facts: list[dict], headlines: list[str], events: list[str], now: pd.Timestamp) -> str:
    return (f"Time now: {now:%Y-%m-%d %H:%M} UTC.\n\nMarkets (daily data):\n{json.dumps(facts, indent=1)}\n\n"
            f"Latest headlines:\n" + ("\n".join(f"- {h}" for h in headlines[:25]) or "- none") +
            "\n\nScheduled high-impact news in the next 24 hours:\n" + ("\n".join(f"- {e}" for e in events) or "- none") +
            "\n\nGive one call per market symbol listed above.")


def ask(api_key: str, model: str, prompt: str, timeout: int = 90) -> tuple[list[dict], str]:
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "daily_calls", "schema": SCHEMA, "strict": True}}}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    content = out["choices"][0]["message"]["content"]
    return json.loads(content)["calls"], out.get("model", model)


def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df


def save(df: pd.DataFrame, path: Path):
    out = df.copy()
    out["time"] = out["time"].map(lambda t: pd.Timestamp(t).isoformat())
    out[COLUMNS].to_csv(path, index=False)


def score(df: pd.DataFrame, price_at) -> pd.DataFrame:
    """Fill in the result of calls that are 24+ hours old. price_at(symbol, time) -> price or None."""
    now = pd.Timestamp.now(tz="UTC")
    # read back from the CSV, an all-empty column is float: it can't take True/False
    df[["correct", "trend_correct"]] = df[["correct", "trend_correct"]].astype(object)
    for i, row in df[df["price_24h"].isna()].iterrows():
        due = pd.Timestamp(row["time"]) + pd.Timedelta(hours=24)
        if now < due:
            continue
        p = price_at(row["symbol"], due)
        if p is None or not row["price"]:
            continue
        move = (p / row["price"] - 1) * 100
        df.loc[i, ["price_24h", "move_pct"]] = [p, round(move, 3)]
        df.loc[i, "correct"] = None if row["call"] == "none" or move == 0 else (row["call"] == "up") == (move > 0)
        df.loc[i, "trend_correct"] = None if move == 0 else (row["trend"] == "up") == (move > 0)
    return df


def report_lines(df: pd.DataFrame) -> list[str]:
    """Grok vs a coin flip vs the plain trend direction, on the same days."""
    done = df.dropna(subset=["price_24h"])
    lines = ["🧪 <b>Grok experiment</b> (paper only, not signals)"]
    if done.empty:
        return lines + ["• No scored calls yet: each call is checked 24 hours after it's made."]
    views = done[done["call"] != "none"]
    truth = lambda col: col.map(lambda x: str(x) == "True")      # CSV round-trips True/False/NaN as text
    n, hits = len(views), int(truth(views["correct"]).sum()) if len(views) else 0
    rate = hits / n * 100 if n else 0
    # how often a coin flip would do at least this well (normal approximation)
    z = (hits - 0.5 * n) / math.sqrt(0.25 * n) if n else 0
    luck = 0.5 * math.erfc(z / math.sqrt(2)) * 100
    trend = truth(done["trend_correct"].dropna())
    gain = (views["move_pct"] * views["call"].map({"up": 1, "down": -1})).mean() if n else 0
    lines += [f"• {len(done)} calls scored, Grok gave a view on {n}: right {hits} ({rate:.0f}%), "
              f"average move in its direction {gain:+.2f}%",
              f"• A coin flip does this well {luck:.0f}% of the time"
              + (" (so far, indistinguishable from luck)" if luck > 5 else " (better than luck so far)"),
              f"• Simply following the trend was right {trend.mean() * 100:.0f}% of the time" if len(trend) else "",
              "<i>Judge after about 250 calls (2-3 months).</i>"]
    return [x for x in lines if x]
