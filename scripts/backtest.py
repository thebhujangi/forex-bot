"""Step 3: backtest a strategy and write an HTML report to reports/.

Usage:
  .venv\\Scripts\\python scripts\\backtest.py london_breakout             # in-sample (default)
  .venv\\Scripts\\python scripts\\backtest.py london_breakout --oos       # held-back data: use ONCE, at the end
  .venv\\Scripts\\python scripts\\backtest.py london_breakout --set tp2_r=3 sl_mode=opposite
"""
import argparse
import os
import sys
from pathlib import Path

import yaml

from forexbot import DATA_DIR, REPORTS_DIR
from forexbot.backtest import run_backtest
from forexbot.config import load_config
from forexbot.report import save_report

sys.stdout.reconfigure(encoding="utf-8")


def parse_overrides(items):
    # times like 07:00 stay strings (YAML would read them as base-60 numbers)
    return {k: v if ":" in v else yaml.safe_load(v) for k, v in (i.split("=", 1) for i in items or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--oos", action="store_true", help="test only the held-back out-of-sample period")
    ap.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="override strategy parameters")
    ap.add_argument("--config")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--start", help="first date to test (default: data.start in config)")
    ap.add_argument("--out-dir", default=str(REPORTS_DIR))
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.oos:
        print("*** OUT-OF-SAMPLE run. If you change the strategy after seeing this, the held-back "
              "data is no longer a fair test. ***\n")
    res = run_backtest(cfg, args.strategy, args.symbols, args.oos,
                       parse_overrides(args.set), Path(args.data_dir), args.start)

    s = res.summary
    print(f"{res.strategy} | {', '.join(res.symbols)} | {res.period}")
    if s.get("trades"):
        print(f"  trades {s['trades']}  win {s['win_rate_pct']:.1f}%  expectancy {s['expectancy_r']:+.2f}R  "
              f"PF {s['profit_factor']:.2f}  return {s['total_return_pct']:+.1f}%  "
              f"maxDD {s['max_drawdown_pct']:.1f}%  avg month {s['monthly_return_pct']:+.2f}%")
        if res.prop:
            print(f"  prop challenges: {res.prop['passed']} passed / {res.prop['failed']} failed "
                  f"(pass rate {res.prop['pass_rate_pct']:.0f}%)")
    print("\n".join("  " + c for c in res.checks))
    print(f"  => {'PASS' if res.passed else 'FAIL'}")

    path = save_report(res, cfg, Path(args.out_dir))
    print(f"\nReport: {path}")
    if not args.no_open and sys.platform == "win32":
        os.startfile(path)


if __name__ == "__main__":
    main()
