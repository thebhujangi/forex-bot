"""Research round 2026-11-19 (2): the daily trend rule on EVERY Elev8 market not tested before.

Input: an inventory CSV of all Elev8 symbols (group, first daily candle, median recorded spread %).
Fixed BEFORE running:
  * eligible: fully tradeable on Elev8 (trade mode 4; mode 3 = close-only, can't open); shares/ETFs need history from 2020-06-30 or earlier and median spread
    <= 0.5%; other crypto from 2021-12-31 or earlier and spread <= 1%. Markets tested on 19 Nov
    (research_trend.py) are skipped.
  * groups: US shares, shares outside the US (Europe + Asia-Pacific: too few to split), ETFs, other crypto, natural gas (spread
    <= 2%, history from 2019). A group is the unit
    tested; no picking single markets after seeing results.
  * variants: 20/55/100-day breakout; shares and ETFs also buy-only (they drift up over time).
  * PASS (proven): all-years t >= 3, unseen 2024-26 average > 0 and >= 50% of 2019-23, >= 30 unseen
    trades, positive at double costs.
  * PROMISING (demo): 2019-23 average > 0, unseen average > 0 with >= 20 trades, positive at double
    costs, all-years t >= 1.5 (the bar metals & oil cleared).
Costs: Elev8's median recorded spread for each market, slippage a third of that. Elev8 has swaps
switched off (swap mode 0), so holding for weeks costs nothing extra.
Data fixes (Elev8 share history has both problems): one-off bad candles (>25% away from the
neighbouring closes, e.g. MSFT weekend bars at 240 instead of 405) are dropped, and share splits
(NVDA/NFLX 10:1, WMT 3:1) are back-adjusted so they don't look like crashes. Fixed copies go to
data/clean/, the raw downloads are untouched.
Usage:  .venv\\Scripts\\python scripts\\research_all.py INVENTORY.csv
"""
import copy
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from forexbot import DATA_DIR, REPORTS_DIR
from forexbot.backtest import run_backtest
from forexbot.config import load_config
from forexbot.data import load_bars, save_bars
from forexbot.instruments import SPECS_FILE

CLEAN_DIR = DATA_DIR / "clean"
SPLITS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)


def clean_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop one-off bad candles, then back-adjust share splits. Returns the fixed bars and notes."""
    notes = []
    med = df.close.rolling(5, center=True, min_periods=3).median()
    bad = (df.close / med - 1).abs() > 0.25
    if bad.any():
        notes.append(f"dropped {int(bad.sum())} bad candle(s)")
        df = df[~bad]
    df = df.copy()
    cols = ["open", "high", "low", "close"]
    ratio = df.close / df.close.shift(1)
    for i in np.flatnonzero(((ratio < 0.6) | (ratio > 1.6)).to_numpy()):
        r = ratio.iloc[i]
        for k in SPLITS:
            for f in (k, 1 / k):                    # split (price / k) or reverse split (price x k)
                if abs(r * f - 1) < 0.08:
                    df.iloc[:i, [df.columns.get_loc(c) for c in cols]] /= f
                    notes.append(f"{'split' if f > 1 else 'reverse split'} {k}:1 on {df.index[i].date()}")
                    break
            else:
                continue
            break
    return df, notes


def build_clean(symbols: list[str]) -> None:
    CLEAN_DIR.mkdir(exist_ok=True)
    (CLEAN_DIR / SPECS_FILE).write_bytes((DATA_DIR / SPECS_FILE).read_bytes())
    for s in symbols:
        for tf in ("D1", "H1"):
            if not (DATA_DIR / f"{s}_{tf}.csv.gz").exists():
                continue
            df, notes = clean_bars(load_bars(s, tf, DATA_DIR))
            save_bars(df, s, tf, CLEAN_DIR)
            if notes:
                print(f"data fix {s} {tf}: {'; '.join(notes)}", flush=True)

sys.stdout.reconfigure(encoding="utf-8")

TESTED = {"BTCUSD", "ETHUSD", "XRPUSD", "XAUUSD", "XAGUSD", "XBRUSD", "XTIUSD", "EURUSD", "GBPUSD", "AUDUSD",
          "NZDUSD", "USDCAD", "USDCHF", "USDJPY", "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CHFJPY", "EURAUD",
          "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "GBPCHF", "SPX500", "NAS100", "US30", "UK100",
          "JPN225", "EUSTX50", "FRA40", "ESP35", "AUS200"}


def eligible(inv: pd.DataFrame) -> dict[str, list[str]]:
    inv = inv[(inv.trade_mode == 4) & inv.first_d1.notna() & inv.median_spread_pct.notna()
              & ~inv.symbol.isin(TESTED) & ~inv.symbol.str.contains(r"\.Daily", regex=True)].copy()
    inv["first_d1"] = pd.to_datetime(inv.first_d1)
    shares = inv.group.str.startswith(("Stocks", "ETF"))
    ok_shares = shares & (inv.first_d1 <= "2020-06-30") & (inv.median_spread_pct <= 0.5)
    ok_crypto = (inv.group == "Crypto") & (inv.first_d1 <= "2021-12-31") & (inv.median_spread_pct <= 1.0)
    ok_gas = (inv.symbol == "XNGUSD") & (inv.first_d1 <= "2019-01-01") & (inv.median_spread_pct <= 2.0)
    groups: dict[str, list[str]] = {}
    for _, r in inv[ok_shares | ok_crypto | ok_gas].iterrows():
        if r.group == "Crypto":
            name = "Other crypto"
        elif r.symbol == "XNGUSD":
            name = "Natural gas"
        elif r.group == "Stocks":
            name = "Shares: US" if r["sub"] == "US" else "Shares: outside US"
        else:
            name = r.group
        groups.setdefault(name, []).append(r.symbol)
    return groups


def stats(r: pd.Series) -> dict:
    r = r.astype(float)
    n = len(r)
    sd = r.std(ddof=1) if n > 2 else 0
    return {"n": n, "mean": r.mean() if n else 0.0, "t": r.mean() / (sd / np.sqrt(n)) if sd else 0.0}


def main():
    inv = pd.read_csv(sys.argv[1])
    groups = eligible(inv)
    cfg = load_config()
    cfg["risk"].update(max_open_trades=10_000, daily_loss_limit_pct=10_000)
    spreads = inv.set_index("symbol").median_spread_pct
    for g in groups.values():
        for s in g:
            if s not in cfg["costs"]:
                cfg["costs"][s] = {"spread_pct": float(spreads[s]), "slippage_pct": float(spreads[s]) / 3,
                                   "commission_per_lot": 0}
    dear = copy.deepcopy(cfg)
    for s, c in dear["costs"].items():
        dear["costs"][s] = {k: (v * 2 if k != "commission_per_lot" else v) for k, v in c.items()}
    years = (pd.Timestamp.now() - pd.Timestamp(cfg["data"]["start"])).days / 365.25

    build_clean([x for g in groups.values() for x in g])
    rows = []
    for gname, syms in groups.items():
        syms = [s for s in syms if (DATA_DIR / f"{s}_D1.csv.gz").exists() and (DATA_DIR / f"{s}_H1.csv.gz").exists()]
        sides = ["both", "long"] if gname.startswith(("Shares", "ETF")) else ["both"]
        for days in (20, 55, 100):
            for side in sides:
                p = {"entry_days": days, "sides": side}
                try:
                    ins = run_backtest(cfg, "daily_trend", syms, False, p, CLEAN_DIR).trades
                    oos = run_backtest(cfg, "daily_trend", syms, True, p, CLEAN_DIR).trades
                    costly = pd.concat([run_backtest(dear, "daily_trend", syms, o, p, CLEAN_DIR).trades for o in (False, True)])
                except Exception as e:                       # one bad market shouldn't stop the round
                    print(f"{gname} {days} {side}: skipped ({e})", flush=True)
                    continue
                a, b, c, d = stats(ins["r"]), stats(oos["r"]), stats(pd.concat([ins, oos])["r"]), stats(costly["r"])
                proven = c["t"] >= 3 and b["mean"] > 0 and b["mean"] >= 0.5 * a["mean"] and b["n"] >= 30 and d["mean"] > 0
                promising = a["mean"] > 0 and b["mean"] > 0 and b["n"] >= 20 and d["mean"] > 0 and c["t"] >= 1.5
                verdict = "PASS" if proven else "PROMISING" if promising else "fail"
                rows.append(dict(group=gname, n_markets=len(syms), days=days, sides=side, ins=a, oos=b, all=c,
                                 dear=d, verdict=verdict, per_year=c["n"] / years, symbols=syms))
                print(f"{gname[:28]:28} ({len(syms):3} mkts) {days:3}d {side:4} | 2019-23 {a['n']:5} tr {a['mean']:+.2f}R | "
                      f"2024-26 {b['n']:5} tr {b['mean']:+.2f}R | t {c['t']:+.1f} | {c['n'] / years:6.1f}/yr | "
                      f"2x {d['mean']:+.2f}R | {verdict}", flush=True)

    out = REPORTS_DIR / f"research_all_{date.today()}.md"
    REPORTS_DIR.mkdir(exist_ok=True)
    lines = [f"# Trend rule on every other Elev8 market ({date.today()})", "", "Rules: see scripts/research_all.py", "",
             "| Group | Markets | Speed | Sides | 2019-23 | 2024-26 | t | trades/yr | 2x costs | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['group']} | {r['n_markets']} | {r['days']}d | {r['sides']} | {r['ins']['n']} tr {r['ins']['mean']:+.2f}R | "
                     f"{r['oos']['n']} tr {r['oos']['mean']:+.2f}R | {r['all']['t']:+.1f} | {r['per_year']:.1f} | "
                     f"{r['dear']['mean']:+.2f}R | {r['verdict']} |")
    lines += ["", "## Markets per group"] + [f"- {g}: {', '.join(s)}" for g, s in groups.items()]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pd.DataFrame([{k: v for k, v in r.items() if k not in ("ins", "oos", "all", "dear")}
                  | {"ins_mean": r["ins"]["mean"], "oos_mean": r["oos"]["mean"], "t": r["all"]["t"]} for r in rows]
                 ).to_csv(out.with_suffix(".csv"), index=False)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
