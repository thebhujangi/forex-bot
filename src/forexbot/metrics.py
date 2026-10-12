"""Performance statistics, Monte Carlo stress test and prop firm challenge simulation."""
import numpy as np
import pandas as pd

from .timeutils import trading_day


def equity_curve(trades: pd.DataFrame, start_balance: float) -> pd.Series:
    if trades.empty:
        return pd.Series([start_balance], dtype=float)
    eq = start_balance + trades["pnl"].cumsum()
    eq.index = trades["exit_time"]
    return eq


def max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((peak - equity) / peak).max() * 100) if len(equity) else 0.0


def longest_losing_streak(r: pd.Series) -> int:
    best = cur = 0
    for v in r:
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return best


def summarize(trades: pd.DataFrame, start_balance: float) -> dict:
    if trades.empty:
        return {"trades": 0}
    eq = pd.concat([pd.Series([start_balance]), equity_curve(trades, start_balance)],
                   ignore_index=True)
    wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    gross_loss = -losses.pnl.sum()
    span = trades.exit_time.max() - trades.fill_time.min()
    months = max(span.total_seconds() / (30.44 * 86400), 1.0)
    final = float(eq.iloc[-1])
    return {
        "trades": len(trades),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "expectancy_r": trades.r.mean(),
        "avg_win_r": wins.r.mean() if len(wins) else 0.0,
        "avg_loss_r": losses.r.mean() if len(losses) else 0.0,
        "profit_factor": wins.pnl.sum() / gross_loss if gross_loss > 0 else float("inf"),
        "net_profit": final - start_balance,
        "total_return_pct": (final / start_balance - 1) * 100,
        "monthly_return_pct": ((final / start_balance) ** (1 / months) - 1) * 100,
        "max_drawdown_pct": max_drawdown_pct(eq),
        "longest_losing_streak": longest_losing_streak(trades.r),
        "trades_per_month": len(trades) / months,
        "start": trades.fill_time.min(),
        "end": trades.exit_time.max(),
    }


def breakdown(trades: pd.DataFrame, by) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(by)
    out = pd.DataFrame({
        "trades": g.size(),
        "win_rate_pct": g.apply(lambda d: (d.pnl > 0).mean() * 100),
        "expectancy_r": g.r.mean(),
        "net_profit": g.pnl.sum(),
    })
    return out.round(2)


def monthly_returns(trades: pd.DataFrame) -> pd.DataFrame:
    """Return % per calendar month (rows = year, columns = month)."""
    if trades.empty:
        return pd.DataFrame()
    t = trades.assign(year=trades.exit_time.dt.year, month=trades.exit_time.dt.month)
    growth = t.groupby(["year", "month"]).return_pct.apply(lambda x: (np.prod(1 + x / 100) - 1) * 100)
    table = growth.unstack("month").reindex(columns=range(1, 13))
    table.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return table


def monte_carlo(trades: pd.DataFrame, runs: int = 5000, seed: int = 7) -> dict:
    """Shuffle the order of trade returns to see the range of drawdowns luck could produce."""
    if len(trades) < 2:
        return {}
    rng = np.random.default_rng(seed)
    rets = trades.return_pct.to_numpy() / 100
    runs = min(runs, max(500, 5_000_000 // len(rets)))  # keep memory reasonable
    sims = rng.permuted(np.tile(rets, (runs, 1)), axis=1)
    eq = np.cumprod(1 + sims, axis=1)
    eq = np.hstack([np.ones((runs, 1)), eq])
    dd = ((np.maximum.accumulate(eq, axis=1) - eq) / np.maximum.accumulate(eq, axis=1)).max(axis=1) * 100
    return {
        "runs": runs,
        "median_max_dd_pct": float(np.median(dd)),
        "p95_max_dd_pct": float(np.percentile(dd, 95)),
        "worst_max_dd_pct": float(dd.max()),
        "prob_dd_over_10_pct": float((dd > 10).mean() * 100),
        "prob_dd_over_20_pct": float((dd > 20).mean() * 100),
    }


def prop_challenges(trades: pd.DataFrame, prop: dict) -> dict:
    """Start a fresh challenge at the beginning of every month and see how each one ends.

    Pass  = balance reaches +target.  Fail = total loss limit or daily loss limit (both
    measured against the initial balance, using closed trades; the risk manager already
    caps open risk below the daily limit).
    """
    if trades.empty:
        return {}
    target = prop["profit_target_pct"] / 100
    max_total = prop["max_total_loss_pct"] / 100
    max_daily = prop["max_daily_loss_pct"] / 100
    t = trades.sort_values("exit_time")
    first = t.fill_time.min()
    starts = pd.date_range(first.normalize() + pd.offsets.MonthBegin(1), t.exit_time.max(),
                           freq="MS", tz="UTC")
    outcomes, days_to_pass = [], []
    for s in starts:
        sub = t[t.fill_time >= s]
        bal, day, day_start, outcome = 1.0, None, 1.0, "ongoing"
        for row in sub.itertuples():
            d = trading_day(row.exit_time)
            if d != day:
                day, day_start = d, bal
            bal *= 1 + row.return_pct / 100
            if bal <= 1 - max_total or day_start - bal >= max_daily:
                outcome = "failed"
                break
            if bal >= 1 + target:
                outcome = "passed"
                days_to_pass.append((row.exit_time - s).days)
                break
        outcomes.append(outcome)
    passed, failed = outcomes.count("passed"), outcomes.count("failed")
    decided = passed + failed
    return {
        "attempts": len(outcomes),
        "passed": passed,
        "failed": failed,
        "unfinished": outcomes.count("ongoing"),
        "pass_rate_pct": passed / decided * 100 if decided else float("nan"),
        "median_days_to_pass": float(np.median(days_to_pass)) if days_to_pass else float("nan"),
    }


def verdict(summary: dict, rules: dict) -> tuple[bool, list[str]]:
    checks = [
        (summary.get("trades", 0) >= rules["min_trades"],
         f"trades {summary.get('trades', 0)} (need >= {rules['min_trades']})"),
        (summary.get("profit_factor", 0) >= rules["min_profit_factor"],
         f"profit factor {summary.get('profit_factor', 0):.2f} (need >= {rules['min_profit_factor']})"),
        (summary.get("expectancy_r", 0) >= rules["min_expectancy_r"],
         f"expectancy {summary.get('expectancy_r', 0):.2f}R (need >= {rules['min_expectancy_r']}R)"),
        (summary.get("max_drawdown_pct", 100) <= rules["max_drawdown_pct"],
         f"max drawdown {summary.get('max_drawdown_pct', 100):.1f}% (need <= {rules['max_drawdown_pct']}%)"),
    ]
    return all(ok for ok, _ in checks), [("PASS  " if ok else "FAIL  ") + msg for ok, msg in checks]
