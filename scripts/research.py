"""Research round 2026-10-22: new strategies and outside-data filters, on Elev8's own MT5 prices.

Pass rule (fixed BEFORE running):
  * in-sample 2019-2023: expectancy >= +0.10R and profit factor >= 1.2
  * unseen 2024-2026: expectancy > 0, profit factor > 1.1, at least 20 trades
  * a filter must also beat the same strategy without it in BOTH periods
Usage:  .venv\\Scripts\\python scripts\\research.py
"""
import sys
from datetime import date

from forexbot import REPORTS_DIR
from forexbot.backtest import run_backtest
from forexbot.config import load_config

sys.stdout.reconfigure(encoding="utf-8")

PULLBACK = {"timeframe": "M15", "max_hold_bars": 32, "expiry_bars": 8, "cooldown_bars": 8}
CRYPTO, FXG, IDX = ["BTCUSD", "ETHUSD"], ["XAUUSD", "EURUSD", "GBPUSD"], ["SPX500", "NAS100", "US30"]

# (group, label, strategy, symbols, params, baseline label or None)
EXPERIMENTS = [
    ("Indices", "Dip buy RSI(2)", "dip_buy", IDX, {"mode": "rsi2"}, None),
    ("Indices", "Dip buy IBS", "dip_buy", IDX, {"mode": "ibs", "hold_days": 1}, None),
    ("Indices", "Daily trend", "daily_trend", IDX, {}, None),
    ("Gold", "Daily trend", "daily_trend", ["XAUUSD"], {}, None),
    ("Gold", "+ real-rate filter", "daily_trend", ["XAUUSD"], {"filter": "real_rate"}, "Daily trend"),
    ("Gold", "+ dollar filter", "daily_trend", ["XAUUSD"], {"filter": "dollar"}, "Daily trend"),
    ("Gold", "+ COT trend", "daily_trend", ["XAUUSD"], {"filter": "cot_trend"}, "Daily trend"),
    ("Gold", "+ COT extreme", "daily_trend", ["XAUUSD"], {"filter": "cot_extreme"}, "Daily trend"),
    ("EUR/GBP", "Daily trend", "daily_trend", ["EURUSD", "GBPUSD"], {}, None),
    ("EUR/GBP", "+ dollar filter", "daily_trend", ["EURUSD", "GBPUSD"], {"filter": "dollar"}, "Daily trend"),
    ("EUR/GBP", "+ COT trend", "daily_trend", ["EURUSD", "GBPUSD"], {"filter": "cot_trend"}, "Daily trend"),
    ("EUR/GBP", "+ COT extreme", "daily_trend", ["EURUSD", "GBPUSD"], {"filter": "cot_extreme"}, "Daily trend"),
    ("Crypto", "Daily trend", "daily_trend", CRYPTO, {}, None),
    ("Crypto", "+ funding filter", "daily_trend", CRYPTO, {"filter": "funding"}, "Daily trend"),
    ("Crypto", "Pullback 15m", "trend_pullback", CRYPTO, PULLBACK, None),
    ("Crypto", "+ funding (pullback)", "trend_pullback", CRYPTO, {**PULLBACK, "filter": "funding"}, "Pullback 15m"),
    ("Crypto", "+ calm markets only", "trend_pullback", CRYPTO, {**PULLBACK, "filter": "vol_low"}, "Pullback 15m"),
    ("Crypto", "+ busy markets only", "trend_pullback", CRYPTO, {**PULLBACK, "filter": "vol_high"}, "Pullback 15m"),
]


def stats(res) -> dict:
    s = res.summary
    if not s.get("trades"):
        return {"n": 0, "exp": 0.0, "pf": 0.0, "win": 0.0, "ret": 0.0, "dd": 0.0}
    return {"n": s["trades"], "exp": s["expectancy_r"], "pf": s["profit_factor"], "win": s["win_rate_pct"],
            "ret": s["total_return_pct"], "dd": s["max_drawdown_pct"]}


def main():
    cfg = load_config()
    rows, by_label = [], {}
    for group, label, strat, syms, params, base in EXPERIMENTS:
        ins = stats(run_backtest(cfg, strat, syms, False, params))
        oos = stats(run_backtest(cfg, strat, syms, True, params))
        ok = (ins["exp"] >= 0.10 and ins["pf"] >= 1.2 and oos["exp"] > 0 and oos["pf"] > 1.1 and oos["n"] >= 20)
        if base:
            b = by_label[(group, base)]
            ok = ok and ins["exp"] > b[0]["exp"] and oos["exp"] > b[1]["exp"]
        by_label[(group, label)] = (ins, oos)
        rows.append((group, label, ", ".join(syms), ins, oos, ok))
        print(f"{group:8} {label:22} | 2019-23: {ins['n']:4} trades {ins['win']:4.0f}% won {ins['exp']:+.2f}R "
              f"PF {ins['pf']:.2f} DD {ins['dd']:4.1f}% | 2024-26: {oos['n']:4} trades {oos['win']:4.0f}% won "
              f"{oos['exp']:+.2f}R PF {oos['pf']:.2f} DD {oos['dd']:4.1f}% | {'PASS' if ok else 'fail'}", flush=True)

    out = REPORTS_DIR / f"research_{date.today()}.md"
    REPORTS_DIR.mkdir(exist_ok=True)
    lines = ["# Research round " + str(date.today()), "", "Elev8 MT5 prices, Elev8 spreads. Pass rule: see scripts/research.py.",
             "", "| Market | Idea | 2019-23 trades | win % | per trade | PF | 2024-26 trades | win % | per trade | PF | Result |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for group, label, syms, i, o, ok in rows:
        lines.append(f"| {group} | {label} | {i['n']} | {i['win']:.0f} | {i['exp']:+.2f}R | {i['pf']:.2f} | {o['n']} | "
                     f"{o['win']:.0f} | {o['exp']:+.2f}R | {o['pf']:.2f} | {'PASS' if ok else 'fail'} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
