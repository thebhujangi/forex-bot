"""How scheduled US news moves each market on Elev8 (2019-now), from real release dates (FRED) and
15-minute candles. Fixed before running:
  * size: median absolute move in the first 15 / 60 minutes, against the same clock time on weekdays
    without that release.
  * direction: how often the next 4 hours continue the first 15-minute move (50% = coin flip).
  * trade test: follow (or fade) the first 15-minute move for 4 hours, after Elev8's spread, only when
    the first move was at least 2x normal. PASS needs t >= 3 clustered by release and both
    2019-23 and 2024-26 positive.
Writes src/forexbot/news_impact.json (size and direction, used in the bot's messages) and a report.
Usage:  .venv\\Scripts\\python scripts\\research_news_impact.py     (needs FRED_API_KEY in .env)
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from forexbot import REPORTS_DIR
from forexbot.config import load_config
from forexbot.data import load_bars
from forexbot.instruments import load_costs

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
SYMBOLS = ["BTCUSD", "ETHUSD", "XRPUSD", "XAUUSD", "XAGUSD", "XBRUSD", "XTIUSD", "SPX500", "NAS100", "EURUSD", "GBPUSD"]
RELEASES = {"CPI": (10, "08:30"), "Non-Farm Payrolls": (50, "08:30"), "PPI": (46, "08:30"), "Retail Sales": (9, "08:30"),
            "GDP": (53, "08:30"), "Core PCE": (54, "08:30")}
# FOMC decisions (statement 14:00 New York), federalreserve.gov calendars
FOMC = """2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11 2020-01-29 2020-04-29
2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16 2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22
2021-11-03 2021-12-15 2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14 2023-02-01
2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13 2024-01-31 2024-03-20 2024-05-01 2024-06-12
2024-07-31 2024-09-18 2024-11-07 2024-12-18 2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29
2025-12-10 2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28""".split()


def fred_dates(rid: int) -> list[str]:
    q = urllib.parse.urlencode({"release_id": rid, "api_key": os.environ["FRED_API_KEY"], "file_type": "json",
                                "realtime_start": "2019-01-01", "realtime_end": str(date.today()), "limit": 1000})
    req = urllib.request.Request(f"https://api.stlouisfed.org/fred/release/dates?{q}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return [x["date"] for x in json.loads(r.read())["release_dates"]]


def main():
    events = {}
    for name, (rid, hm) in RELEASES.items():
        try:
            events[name] = [pd.Timestamp(f"{d} {hm}", tz="America/New_York") for d in fred_dates(rid)]
        except Exception as e:
            print(f"{name}: no dates ({type(e).__name__})")
    events["FOMC rate decision"] = [pd.Timestamp(f"{d} 14:00", tz="America/New_York") for d in FOMC]
    wed = pd.date_range("2019-01-02", date.today(), freq="W-WED")
    events["EIA crude oil stocks"] = [pd.Timestamp(f"{d:%Y-%m-%d} 10:30", tz="America/New_York") for d in wed]
    cfg = load_config()
    out, rows, trades = {}, [], []
    for sym in SYMBOLS:
        b = load_bars(sym, "M15")
        b = b[~b.index.duplicated() & (b.index >= "2019-01-01")]
        o, c = b["open"], b["close"]
        cost = load_costs(cfg, sym)
        f = pd.DataFrame({"m15": c / o - 1, "m60": c.shift(-3) / o - 1, "next4h": c.shift(-16) / c - 1, "px": c})
        step = b.index.to_series().diff().shift(-4).dt.total_seconds()
        ny = b.index.tz_convert("America/New_York")
        hm = pd.Series(ny.strftime("%H:%M"), index=b.index)
        wk = pd.Series(ny.weekday < 5, index=b.index)
        for name, ts in events.items():
            if name.startswith("EIA") and sym not in ("XBRUSD", "XTIUSD"):
                continue
            idx = pd.DatetimeIndex([t.tz_convert("UTC") for t in ts])
            e = f[f.index.isin(idx)].dropna()
            e = e[e.m60.abs() < 0.5]
            if len(e) < 20:
                continue
            base = f[(hm == hm[e.index[0]]) & wk & ~f.index.isin(idx)].dropna()
            # normal size over the previous 60 same-time candles, so old volatility isn't mixed in
            n15 = base.m15.abs().median()
            r15, r60 = e.m15.abs().median(), e.m60.abs().median()
            cont = float((np.sign(e.m15) == np.sign(e.next4h)).mean())
            out.setdefault(sym, {})[name] = {"n": len(e), "move15_pct": round(r15 * 100, 3), "move60_pct": round(r60 * 100, 3),
                                             "times_normal": round(float(r15 / n15), 1) if n15 else None,
                                             "continue_pct": round(cont * 100)}
            rows.append((sym, name, len(e), r15 * 100, r60 * 100, r15 / n15 if n15 else np.nan,
                         r60 / base.m60.abs().median(), cont * 100))
            big = e[e.m15.abs() >= 2 * n15]
            for t, x in big.iterrows():
                cc = cost.at_price(x.px)
                trades.append((sym, name, t, np.sign(x.m15) * x.next4h, (cc.spread + 2 * cc.slippage) / x.px))
    (ROOT / "src" / "forexbot" / "news_impact.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    d = pd.DataFrame(rows, columns=["symbol", "release", "n", "move15_%", "move60_%", "x_normal_15m", "x_normal_60m", "continues_%"])
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)
    print(d.round(2).to_string())
    t = pd.DataFrame(trades, columns=["symbol", "release", "time", "gross", "cost"])
    t["era"] = np.where(t.time < "2024-01-01", "2019-23", "2024-26")
    print("\nTrade test: after a first move >= 2x normal, follow / fade it for 4 hours (average % per trade after costs)")
    for name, g in list(t.groupby("release")) + [("ALL", t)]:
        line = f"{name:22} n={len(g):4} "
        for mode, s in (("follow", 1), ("fade", -1)):
            net = s * g.gross - g.cost
            m = net.mean()
            cl = (net - m).groupby(g.time).sum()
            tt = m / (np.sqrt((cl ** 2).sum()) / len(g))
            eras = " ".join(f"{k} {v * 100:+.3f}%" for k, v in net.groupby(g.era).mean().items())
            ok = tt >= 3 and (net.groupby(g.era).mean() > 0).all()
            line += f"| {mode} {m * 100:+.3f}% t {tt:+.1f} ({eras}) {'PASS' if ok else 'fail'} "
        print(line)
    d.to_csv(REPORTS_DIR / f"research_news_impact_{date.today()}.csv", index=False)


if __name__ == "__main__":
    main()
