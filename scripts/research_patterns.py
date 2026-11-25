"""Research round 2026-11-26: do chart patterns make money on Elev8's prices?

Fixed BEFORE running (nothing below was tuned on results):
  * Patterns: every one in forexbot/strategies/chart_patterns.py (14 classic shapes, 7 price-action,
    7 harmonic, 15 candlestick/bar patterns), one textbook rule each, buys and sells pooled.
  * Charts: 1-hour, 4-hour and daily. Markets: BTC, ETH, XRP, gold, silver, Brent, WTI, S&P 500,
    Nasdaq 100, EURUSD, GBPUSD, pooled (no picking single markets afterwards).
  * Trade: enter at the next hourly candle, textbook stop and target, time limit; Elev8's spread and
    slippage; if stop and target are in the same hour the stop is assumed first; one open trade per
    pattern, chart and market.
  * The unit tested is one pattern on one chart (43 x 3 = 129 tests).
  * t is clustered by week, so 11 markets moving together don't count as 11 independent results.
  * Random control: 100 copies with the same number of trades per market and period, same side, stop,
    target and time limit (in ATR), but entered at random times.
  * PASS: t >= 3, unseen 2024-26 average > 0 and >= half of 2019-23, >= 30 unseen trades, positive at
    double costs, and at most 1 of 100 random copies does as well.
  * PROMISING: 2019-23 > 0, unseen > 0 with >= 20 trades, positive at double costs, t >= 2, at most
    5 of 100 random copies do as well.
  * Luck check: the same PROMISING rule is applied to the random copies themselves, to show how many
    passes luck alone gives in a search this size.
Usage:  .venv\\Scripts\\python scripts\\research_patterns.py
"""
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import numpy as np
import pandas as pd

from forexbot import REPORTS_DIR
from forexbot.config import load_config
from forexbot.data import load_bars
from forexbot.instruments import load_costs
from forexbot.strategies import chart_patterns as cp

sys.stdout.reconfigure(encoding="utf-8")
SYMBOLS = ["BTCUSD", "ETHUSD", "XRPUSD", "XAUUSD", "XAGUSD", "XBRUSD", "XTIUSD", "SPX500", "NAS100", "EURUSD", "GBPUSD"]
TF_MIN = {"H1": 60, "H4": 240, "D1": 1440}
SPLIT = pd.Timestamp("2024-01-01", tz="UTC").value
REPS = 100
NS_MIN = 60_000_000_000


def resample_h4(h1: pd.DataFrame) -> pd.DataFrame:
    """4-hour candles on the Elev8 server clock (EET), as MT5 draws them."""
    loc = h1.index.tz_convert("Europe/Athens").tz_localize(None)
    g = h1.groupby(loc.floor("4h"))
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    out.index = out.index.tz_localize("Europe/Athens", ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")
    return out[~out.index.duplicated()]


def sim(B, created, sgn, entry, sl, tp, close_by, sp, slip):
    """Fast copy of engine.simulate for market orders with one target. Returns (R, exit time) or None."""
    t, O, H, L, C = B
    i = int(np.searchsorted(t, created))
    if i >= len(t) - 1:
        return None
    risk = abs(entry - sl)
    fill = O[i] + sp + slip if sgn > 0 else O[i] - slip
    if (L[i] <= sl) if sgn > 0 else (H[i] + sp >= sl):
        px = min(sl, fill - sp) - slip if sgn > 0 else max(sl, fill + sp) + slip
        return sgn * (px - fill) / risk, t[i]
    e = int(np.searchsorted(t, close_by))
    a, b = i + 1, min(max(e, i + 1), len(t))
    if sgn > 0:
        s_hit, t_hit = L[a:b] <= sl, H[a:b] >= tp
    else:
        s_hit, t_hit = H[a:b] + sp >= sl, L[a:b] + sp <= tp
    si = int(s_hit.argmax()) if s_hit.any() else 1 << 30
    ti = int(t_hit.argmax()) if t_hit.any() else 1 << 30
    if si <= ti and si < 1 << 30:
        k = a + si
        px = min(sl, O[k]) - slip if sgn > 0 else max(sl, O[k] + sp) + slip
    elif ti < 1 << 30:
        k = a + ti
        px = max(tp, O[k]) if sgn > 0 else min(tp, O[k] + sp)
    elif b < len(t):
        k = b
        px = O[k] if sgn > 0 else O[k] + sp
    else:
        k = len(t) - 1
        px = C[k] if sgn > 0 else C[k] + sp
    return sgn * (px - fill) / risk, t[k]


def one(args):
    sym, tf = args
    cfg = load_config()
    cost = load_costs(cfg, sym)
    h1 = load_bars(sym, "H1")
    h1 = h1[~h1.index.duplicated()]
    bars = h1 if tf == "H1" else resample_h4(h1) if tf == "H4" else load_bars(sym, "D1")
    B = (h1.index.as_unit("ns").asi8, *(h1[x].to_numpy(float) for x in ("open", "high", "low", "close")))
    step = TF_MIN[tf] * NS_MIN
    bt = bars.index.as_unit("ns").asi8
    close = bars["close"].to_numpy(float)
    atr = cp.atr_of(*(bars[x].to_numpy(float) for x in ("high", "low", "close")))
    start = pd.Timestamp(cfg["data"]["start"], tz="UTC").value
    ok = np.flatnonzero((atr > 0) & (bt >= start) & (np.arange(len(bt)) >= 60))
    pools = {True: ok[bt[ok] + step < SPLIT], False: ok[bt[ok] + step >= SPLIT]}
    f = cp.find(bars)
    f = f[(bt[f.i] >= start)].sort_values("i")
    rng = np.random.default_rng(zlib.crc32(f"{sym}{tf}".encode()))
    rows = []
    for name, g in f.groupby("name"):
        busy = -1
        for r in g.itertuples(index=False):
            created = bt[r.i] + step
            if created < busy:
                continue
            sgn = 1 if r.side == "buy" else -1
            c1 = cost.at_price(r.entry)
            res = sim(B, created, sgn, r.entry, r.sl, r.tp, created + r.hold * step, c1.spread, c1.slippage)
            if res is None:
                continue
            busy = res[1]
            x2 = sim(B, created, sgn, r.entry, r.sl, r.tp, created + r.hold * step, 2 * c1.spread, 2 * c1.slippage)
            z0 = sim(B, created, sgn, r.entry, r.sl, r.tp, created + r.hold * step, 0.0, 0.0)
            is_in = created < SPLIT
            d_sl, d_tp = abs(r.entry - r.sl) / r.atr, abs(r.tp - r.entry) / r.atr
            rand = np.full(REPS, np.nan)
            pool = pools[is_in]
            for q, j in enumerate(rng.choice(pool, REPS)):
                e, a, cr = close[j], atr[j], bt[j] + step
                c2 = cost.at_price(e)
                y = sim(B, cr, sgn, e, e - sgn * d_sl * a, e + sgn * d_tp * a, cr + r.hold * step, c2.spread, c2.slippage)
                if y is not None:
                    rand[q] = y[0]
            rows.append((name, tf, sym, r.side, created, is_in, res[0], x2[0], z0[0], rand))
    return rows


def clustered_t(x: np.ndarray, week: np.ndarray) -> float:
    m = x.mean()
    s = pd.Series(x - m).groupby(week).sum().to_numpy()
    se = np.sqrt((s ** 2).sum()) / len(x)
    return m / se if se > 0 else 0.0


def verdict(ins, oos, n_oos, t, x2, beat):
    if t >= 3 and oos > 0 and oos >= 0.5 * ins and n_oos >= 30 and x2 > 0 and beat <= 0.01:
        return "PASS"
    if ins > 0 and oos > 0 and n_oos >= 20 and x2 > 0 and t >= 2 and beat <= 0.05:
        return "PROMISING"
    return "fail"


def main():
    jobs = [(s, tf) for tf in TF_MIN for s in SYMBOLS]
    rows = []
    with ProcessPoolExecutor(12) as ex:
        for r in ex.map(one, jobs):
            rows += r
    d = pd.DataFrame(rows, columns=["name", "tf", "sym", "side", "created", "ins", "r", "r2x", "r0", "rand"])
    d["week"] = d.created // (7 * 86_400_000_000_000)
    d.drop(columns="rand").to_csv(REPORTS_DIR / f"research_patterns_trades_{date.today()}.csv.gz", index=False)
    fam = {p: f for f, ps in (("Classic", cp.CLASSIC), ("Price action", cp.PRICE_ACTION), ("Harmonic", cp.HARMONIC),
                              ("Candlestick", cp.CANDLES)) for p in ps}
    d["family"] = d.name.map(fam)
    out, luck = [], np.zeros(REPS)

    def row(g, label, tf, count=True):
        R = np.vstack(g.rand.to_numpy())
        ins, oos = g.ins.to_numpy(), ~g.ins.to_numpy()
        x, wk = g.r.to_numpy(), g.week.to_numpy()
        means = np.nanmean(R, axis=0)
        beat = float((means >= x.mean()).mean())
        t = clustered_t(x, wk)
        a, b = x[ins].mean() if ins.any() else 0.0, x[oos].mean() if oos.any() else 0.0
        v = verdict(a, b, int(oos.sum()), t, g.r2x.mean(), beat)
        if count:
            for q in range(REPS):                    # the same rule applied to pure luck
                y = np.nan_to_num(R[:, q])
                if ins.any() and oos.any() and y[ins].mean() > 0 and y[oos].mean() > 0 and oos.sum() >= 20:
                    if clustered_t(y, wk) >= 2 and (means >= means[q]).mean() <= 0.06:
                        luck[q] += 1
        out.append(dict(pattern=label, tf=tf, n=len(g), ins=a, oos=b, n_oos=int(oos.sum()), win=(x > 0).mean(), t=t,
                        zero_cost=g.r0.mean(), x2=g.r2x.mean(), rand=means.mean(), beat=beat, verdict=v))

    for tf in TF_MIN:
        for name in cp.ALL:
            g = d[(d.tf == tf) & (d.name == name)]
            if len(g) >= 10:
                row(g, name, tf)
        for family in ("Classic", "Price action", "Harmonic", "Candlestick"):
            row(d[(d.tf == tf) & (d.family == family)], f"ALL {family}", tf, count=False)
    o = pd.DataFrame(out)
    o.to_csv(REPORTS_DIR / f"research_patterns_{date.today()}.csv", index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 400)
    print(o.round(3).to_string())
    single = o[~o.pattern.str.startswith("ALL")]
    print(f"\n{len(d):,} trades, {len(single)} tests. PASS {int((single.verdict == 'PASS').sum())}, "
          f"PROMISING {int((single.verdict == 'PROMISING').sum())}. Luck alone gives on average {luck.mean():.2f} "
          f"PROMISING (95th percentile {np.percentile(luck, 95):.0f}). Positive before costs: "
          f"{int((single.zero_cost > 0).sum())} of {len(single)}.")


if __name__ == "__main__":
    main()
