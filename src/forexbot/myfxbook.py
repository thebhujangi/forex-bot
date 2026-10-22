"""Myfxbook Community Outlook: what % of retail traders are long/short each market (free API).

Retail traders tend to be on the wrong side at extremes, so a lopsided crowd is a contrarian
clue. Myfxbook only gives the current numbers, so the bot records a snapshot every hour into
data/sentiment.csv; after a few months that history can be backtested.

Login uses the Myfxbook website email/password from .env, sent in the request body (never in
the URL). The API allows roughly 100 calls a day, so the session is reused and calls are hourly.
"""
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from . import DATA_DIR, STATE_DIR
from .telegram import load_env

API = "https://www.myfxbook.com/api"
SESSION_FILE = DATA_DIR / "myfxbook_session.json"   # private: never shared or uploaded
SENTIMENT_FILE = STATE_DIR / "sentiment.csv"
MIN_POSITIONS = 200          # below this the crowd is too small to mean anything
FIELDS = ["time", "symbol", "long_pct", "short_pct", "long_positions", "short_positions",
          "avg_long_price", "avg_short_price"]


class MyfxbookError(RuntimeError):
    pass


def _get(url: str, data: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class Myfxbook:
    def __init__(self):
        env = load_env()
        self.email, self.password = env.get("MYFXBOOK_EMAIL", ""), env.get("MYFXBOOK_PASSWORD", "")
        self.session = json.loads(SESSION_FILE.read_text())["session"] if SESSION_FILE.exists() else ""

    @property
    def configured(self) -> bool:
        return bool(self.email and self.password)

    def login(self):
        d = _get(f"{API}/login.json", {"email": self.email, "password": self.password})
        if d.get("error") or not d.get("session"):
            raise MyfxbookError(f"Myfxbook login failed: {d.get('message') or 'check MYFXBOOK_EMAIL/PASSWORD in .env'}")
        self.session = d["session"]
        SESSION_FILE.write_text(json.dumps({"session": self.session}))

    def outlook(self) -> list[dict]:
        for attempt in range(2):
            if not self.session:
                self.login()
            d = _get(f"{API}/get-community-outlook.json?" + urllib.parse.urlencode({"session": self.session}))
            if not d.get("error"):
                return d.get("symbols", [])
            self.session = ""          # expired: log in again once
        raise MyfxbookError(f"Myfxbook outlook failed: {d.get('message')}")


def record(symbols: list[dict], wanted: list[str], path: Path = SENTIMENT_FILE) -> list[dict]:
    """Append this snapshot for the wanted symbols; returns the rows written."""
    now = pd.Timestamp.now(tz="UTC").isoformat()
    rows = [{"time": now, "symbol": s["name"], "long_pct": s["longPercentage"], "short_pct": s["shortPercentage"],
             "long_positions": s["longPositions"], "short_positions": s["shortPositions"],
             "avg_long_price": s["avgLongPrice"], "avg_short_price": s["avgShortPrice"]}
            for s in symbols if s["name"] in wanted]
    new = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return rows


def latest(path: Path = SENTIMENT_FILE, max_age_hours: float = 3) -> dict[str, dict]:
    """Most recent snapshot per symbol, if fresh enough and from a big enough crowd."""
    if not Path(path).exists():
        return {}
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    df = df[df.time >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=max_age_hours)]
    out = {}
    for sym, g in df.groupby("symbol"):
        r = g.iloc[-1]
        if r.long_positions + r.short_positions >= MIN_POSITIONS:
            out[sym] = r.to_dict()
    return out


def describe(row: dict) -> str:
    """One plain-English line, e.g. 'Retail traders: 74% buying, 26% selling (crowd leans hard to buying)'."""
    lp, sp = row["long_pct"], row["short_pct"]
    lean = ""
    if max(lp, sp) >= 70:
        lean = f" (crowd leans hard to {'buying' if lp > sp else 'selling'}; the crowd is often wrong at extremes)"
    return f"Retail traders: {lp:.0f}% buying, {sp:.0f}% selling{lean}"
