"""Run a strategy over history: generate signals -> simulate -> risk manager -> stats."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import DATA_DIR
from .data import data_source, load_bars
from . import filters
from .engine import Bars, run_portfolio, simulate
from .instruments import load_costs, load_specs
from .metrics import monte_carlo, prop_challenges, summarize, verdict
from .strategies import get_strategy


@dataclass
class BacktestResult:
    strategy: str
    description: str
    params: dict
    symbols: list[str]
    period: str
    out_of_sample: bool
    signals: int
    trades: pd.DataFrame
    skipped: Counter
    summary: dict
    monte_carlo: dict
    prop: dict
    passed: bool
    checks: list[str] = field(default_factory=list)


def run_backtest(cfg: dict, strategy: str, symbols: list[str] | None = None,
                 out_of_sample: bool = False, params_override: dict | None = None,
                 data_dir: Path = DATA_DIR, start=None) -> BacktestResult:
    """In-sample by default. out_of_sample=True tests ONLY the held-back period."""
    module = get_strategy(strategy)
    params = {**cfg["strategies"][strategy], **(params_override or {})}
    symbols = symbols or params["symbols"]
    split = pd.Timestamp(cfg["backtest"]["in_sample_end"])
    if out_of_sample:
        start, end = split + pd.Timedelta(days=1), None
    else:
        start, end = pd.Timestamp(start or cfg["data"]["start"]), split

    specs = load_specs(data_dir)
    costs = {s: load_costs(cfg, s) for s in symbols}
    # Signals are decided on the strategy's timeframe but replayed on finer candles, so the
    # engine knows more precisely whether the SL or the TP was reached first.
    exec_tf = (params.get("execution_timeframe") or cfg["backtest"].get("execution_timeframe")
               or params["timeframe"])
    results = []
    for sym in symbols:
        # Load warm-up history before the start so indicators like EMAs and ATR are ready.
        warmup = pd.Timedelta(days=params.get("warmup_days", 60))
        src = data_source(cfg, sym)
        df = load_bars(src, params["timeframe"], data_dir, start=start - warmup, end=end)
        signals = [s for s in module.generate(df, sym, params, specs[sym])
                   if s.created >= start.tz_localize("UTC")]
        signals = filters.apply(signals, params.get("filter"), sym, df)
        try:
            exec_df = load_bars(src, exec_tf, data_dir, start=start, end=end)
        except FileNotFoundError:
            exec_df = df
        bars = Bars.from_df(exec_df)
        results += [simulate(s, bars, costs[sym].at_price(s.entry)) for s in signals]

    trades, skipped = run_portfolio(results, specs, costs, cfg)
    balance = cfg["account"]["starting_balance"]
    summary = summarize(trades, balance)
    passed, checks = verdict(summary, cfg["verdict"])
    period = f"{start.date()} to {end.date() if end is not None else 'latest'}"
    return BacktestResult(
        strategy=strategy, description=module.DESCRIPTION, params=params, symbols=symbols,
        period=period, out_of_sample=out_of_sample, signals=len(results), trades=trades,
        skipped=skipped, summary=summary, monte_carlo=monte_carlo(trades),
        prop=prop_challenges(trades, cfg["prop"]), passed=passed, checks=checks,
    )
