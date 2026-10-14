"""Measure how far the free cloud prices (Coinbase, Twelve Data) are from Elev8's MT5 prices.

Run on the PC with MT5 open, every few weeks or after changing broker:
    .venv\\Scripts\\python scripts\\calibrate_feeds.py
Writes feed_calibration.json (commit + push it so the cloud bot uses it). The cloud then shifts the
free prices onto Elev8's bid, and treats anything within the remaining gap ("doubt") as uncertain:
a stop that close counts as probably hit, a target that close doesn't count yet.
"""
import json
import sys

import pandas as pd

from forexbot.config import load_config
from forexbot.feeds import CALIBRATION_FILE, ApiMarket, FeedError, Mt5Market, to_broker
from forexbot import mt5_client as m
from forexbot.telegram import load_env

sys.stdout.reconfigure(encoding="utf-8")
BARS = 1000          # 5-minute candles, about 3.5 days


def measure(feed: pd.DataFrame, mt5: pd.DataFrame) -> dict | None:
    j = feed.join(mt5, lsuffix="_f", rsuffix="_m", how="inner")
    if len(j) < 100:
        return None
    gap = {k: ((j[f"{k}_m"] - j[f"{k}_f"]) / j[f"{k}_f"]).median() for k in ("low", "high", "close")}
    fixed = to_broker(feed.loc[j.index], gap)
    left = pd.concat([(fixed.low - j.low_m).abs() / j.low_m, (fixed.high - j.high_m).abs() / j.high_m])
    return {**{k: round(float(v), 7) for k, v in gap.items()},
            "doubt": round(float(left.quantile(0.95)), 7), "bars": len(j)}


def main():
    cfg = load_config()
    mt5 = m.connect(cfg)
    api = ApiMarket(load_env().get("TWELVEDATA_API_KEY", ""), calibration={})
    syms = sorted({s for src in cfg["signals"] for s in src["symbols"]})
    names = {s: m.resolve_symbol(mt5, s, cfg.get("symbol_map")) for s in syms}
    broker = Mt5Market(mt5, names, cfg["broker"]["server_time"])
    out = {}
    for s in syms:
        try:
            mine = broker.bars(s, "M5", BARS)
            r = measure(api.raw_bars(s, "M5", BARS), mine)
        except (FeedError, KeyError) as e:
            print(f"{s:8} skipped: {e}")
            continue
        if r is None:
            print(f"{s:8} skipped: not enough overlapping candles (market closed?)")
            continue
        out[s] = r
        px = float(mine.close.iloc[-1])
        print(f"{s:8} Elev8 vs free feed: lows {r['low'] * px:+.5g}, highs {r['high'] * px:+.5g}, "
              f"closes {r['close'] * px:+.5g} | still uncertain by up to {r['doubt'] * px:.5g} ({r['bars']} candles)")
    old = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8")).get("symbols", {}) if CALIBRATION_FILE.exists() else {}
    CALIBRATION_FILE.write_text(json.dumps({"measured": pd.Timestamp.now(tz="UTC").isoformat(timespec="minutes"),
                                            "note": "fractions of price: Elev8 bid minus free feed",
                                            "symbols": {**old, **out}}, indent=1) + "\n", encoding="utf-8")
    print(f"\nSaved {CALIBRATION_FILE.name}. Commit and push it so the cloud bot uses it.")


if __name__ == "__main__":
    main()
