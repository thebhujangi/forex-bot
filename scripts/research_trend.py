"""Research round 2026-11-19: the daily trend rule on many Elev8 markets and 3 speeds.

Why: one rule on 3 coins gives ~12 trades a year. Trend following's evidence is for MANY markets.
Each market group x speed (20/55/100-day breakout) is one test: 5 groups x 3 speeds = 15 tests.

Pass rule (fixed BEFORE running, stricter after the Nov 2026 validation research):
  * all 2019-2026 trades of the group together: t-stat >= 3.0 (allows for ~15 tests + earlier ones)
  * unseen 2024-26: average > 0, at least 50% of the 2019-23 average, and >= 30 trades
  * still positive with DOUBLE Elev8's costs
Each signal is measured on its own (no 3-trade portfolio cap) so every market's edge is counted.
Usage:  .venv\\Scripts\\python scripts\\research_trend.py
"""
import copy
import sys
from datetime import date

import numpy as np
import pandas as pd

from forexbot import REPORTS_DIR
from forexbot.backtest import run_backtest
from forexbot.config import load_config

sys.stdout.reconfigure(encoding="utf-8")

GROUPS = {
    "Crypto": ["BTCUSD", "ETHUSD", "XRPUSD"],
    "Metals & energy": ["XAUUSD", "XAGUSD", "XBRUSD", "XTIUSD"],
    "Forex majors": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"],
    "Forex crosses": ["AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CHFJPY", "EURAUD", "EURCAD", "EURCHF",
                      "EURGBP", "EURJPY", "EURNZD", "GBPCHF"],
    "Indices": ["SPX500", "NAS100", "US30", "UK100", "JPN225", "EUSTX50", "FRA40", "ESP35", "AUS200"],
}
SPEEDS = [20, 55, 100]


def stats(r: pd.Series) -> dict:
    r = r.astype(float)
    n = len(r)
    t = r.mean() / (r.std(ddof=1) / np.sqrt(n)) if n > 2 and r.std(ddof=1) > 0 else 0.0
    return {"n": n, "mean": r.mean() if n else 0.0, "t": t, "win": (r > 0).mean() * 100 if n else 0.0}


def run(cfg, syms, days, oos):
    return run_backtest(cfg, "daily_trend", syms, oos, {"entry_days": days}).trades


def main():
    cfg = load_config()
    cfg["risk"].update(max_open_trades=1000, daily_loss_limit_pct=1000)   # measure every signal
    dear = copy.deepcopy(cfg)
    for sym, c in dear["costs"].items():
        dear["costs"][sym] = {k: (v * 2 if k != "commission_per_lot" else v) for k, v in c.items()}

    rows = []
    for group, syms in GROUPS.items():
        for days in SPEEDS:
            ins, oos = run(cfg, syms, days, False), run(cfg, syms, days, True)
            both = pd.concat([ins, oos])
            costly = pd.concat([run(dear, syms, days, False), run(dear, syms, days, True)])
            a, b, c, d = stats(ins["r"]), stats(oos["r"]), stats(both["r"]), stats(costly["r"])
            ok = (c["t"] >= 3.0 and b["mean"] > 0 and b["mean"] >= 0.5 * a["mean"] and b["n"] >= 30
                  and d["mean"] > 0)
            years = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(cfg["data"]["start"], tz="UTC")).days / 365.25
            per_sym = both.groupby("symbol")["r"].agg(["size", "mean"]).round(2).to_dict("index") if len(both) else {}
            rows.append(dict(group=group, days=days, ins=a, oos=b, both=c, dear=d, ok=ok,
                             per_year=c["n"] / years, per_sym=per_sym))
            print(f"{group:16} {days:3}-day | 2019-23 {a['n']:4} tr {a['mean']:+.2f}R | 2024-26 {b['n']:4} tr "
                  f"{b['mean']:+.2f}R | all t={c['t']:+.1f} {c['n'] / years:5.1f}/yr | 2x costs "
                  f"{d['mean']:+.2f}R | {'PASS' if ok else 'fail'}", flush=True)

    out = REPORTS_DIR / f"research_trend_{date.today()}.md"
    REPORTS_DIR.mkdir(exist_ok=True)
    lines = [f"# Trend rule on many markets ({date.today()})", "", "Pass rule: see scripts/research_trend.py", "",
             "| Group | Speed | 2019-23 | 2024-26 | t (all) | trades/yr | 2x costs | Result |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['group']} | {r['days']}-day | {r['ins']['n']} tr, {r['ins']['mean']:+.2f}R | "
                     f"{r['oos']['n']} tr, {r['oos']['mean']:+.2f}R | {r['both']['t']:+.1f} | {r['per_year']:.1f} | "
                     f"{r['dear']['mean']:+.2f}R | {'PASS' if r['ok'] else 'fail'} |")
    lines += ["", "## Per market (all years)"]
    for r in rows:
        lines.append(f"- {r['group']} {r['days']}-day: " + ", ".join(
            f"{s} {v['size']}tr {v['mean']:+.2f}R" for s, v in r["per_sym"].items()))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
