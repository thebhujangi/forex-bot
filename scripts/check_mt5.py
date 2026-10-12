"""Step 1: check the bot can see your MT5 demo account (read-only).

Usage:  .venv\\Scripts\\python scripts\\check_mt5.py
"""
import sys

from forexbot.config import load_config
from forexbot import mt5_client as m

sys.stdout.reconfigure(encoding="utf-8")


def main():
    cfg = load_config()
    mt5 = m.connect(cfg)
    try:
        acc = m.account_summary(mt5)
        print(f"Connected: {acc['company']} | server {acc['server']} | "
              f"{'DEMO' if acc['demo'] else 'LIVE'} account | balance {acc['balance']:,.2f} {acc['currency']} "
              f"| leverage 1:{acc['leverage']}")
        if not acc["demo"]:
            print("!! This is a LIVE account. The bot only reads data, but backtest/demo work should use a demo.")
        print(f"MT5 'Max bars in chart' = {acc['max_bars']:,}"
              + ("  (set Tools > Options > Charts > Max bars to Unlimited for full history)"
                 if acc["max_bars"] < 5_000_000 else ""))
        print()
        print(f"{'symbol':8} {'broker name':14} {'digits':>6} {'contract':>9} {'min lot':>8} "
              f"{'step':>6} {'spread now':>11}  config spread")
        first = None
        for sym in cfg["symbols"]:
            try:
                name = m.resolve_symbol(mt5, sym, cfg.get("symbol_map"))
            except LookupError as e:
                print(f"{sym:8} !! {e}")
                continue
            first = first or name
            spec = m.symbol_spec(mt5, sym, name)
            spread = m.live_spread(mt5, name)
            conf = cfg["costs"].get(sym, {}).get("spread")
            print(f"{sym:8} {name:14} {spec.digits:>6} {spec.contract_size:>9g} {spec.volume_min:>8g} "
                  f"{spec.volume_step:>6g} {spread if spread is not None else float('nan'):>11.{spec.digits}f}  {conf}")
        if first:
            mode, msg = m.detect_server_time(mt5, first)
            print(f"\nServer clock: {msg}" + (f"  -> server_time: {mode}" if mode else ""))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
