"""Backtest engine.

Step 1 `simulate`: replay one signal against candles (bid prices) and record fills and exits.
Rules, all chosen to be pessimistic rather than flattering:
  * Buys fill and sells exit at the ask (bid + spread); sells fill and buys exit at the bid.
  * A pending order only fills if price actually reaches it; gaps fill at the worse open price
    for stops and at the better open price for limits (as a real broker would).
  * Stop entries, stop losses and market orders pay slippage.
  * If SL and TP are both inside the same candle, the SL is assumed to have hit first.
  * On the candle where the order fills, only the SL is checked (no same-candle profits).

Step 2 `run_portfolio`: walk the filled signals in time order with a real balance, apply
the risk manager (max open trades, daily loss limit, lot rounding) and price each trade in USD.
"""
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .instruments import Costs, SymbolSpec
from .models import Exit, SimResult, Signal
from .timeutils import trading_day, to_utc


@dataclass
class Bars:
    t: np.ndarray   # bar open times, int64 ns UTC
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray

    @classmethod
    def from_df(cls, df: pd.DataFrame) -> "Bars":
        return cls(
            t=df.index.as_unit("ns").asi8,
            o=df["open"].to_numpy(float), h=df["high"].to_numpy(float),
            l=df["low"].to_numpy(float), c=df["close"].to_numpy(float),
        )


def _ns(ts) -> int:
    return to_utc(ts).value


def _ts(ns: int) -> pd.Timestamp:
    return pd.Timestamp(ns, tz="UTC")


def _try_fill(sig: Signal, O: float, H: float, L: float, sp: float, slip: float) -> float | None:
    buy = sig.side == "buy"
    if sig.order_type == "market":
        return O + sp + slip if buy else O - slip
    if buy and sig.order_type == "limit":
        return min(sig.entry, O + sp) if L + sp <= sig.entry else None
    if buy and sig.order_type == "stop":
        return max(sig.entry, O + sp) + slip if H + sp >= sig.entry else None
    if sig.order_type == "limit":  # sell limit
        return max(sig.entry, O) if H >= sig.entry else None
    return min(sig.entry, O) - slip if L <= sig.entry else None  # sell stop


def simulate(sig: Signal, bars: Bars, cost: Costs, doubt: float = 0.0) -> SimResult:
    """doubt: how far our prices may be from the broker's (0 in backtests). The stop counts as hit
    if price came within `doubt` of it, and a target only counts once price went `doubt` past it,
    so live follow-ups never report a win that didn't happen at the broker."""
    res = SimResult(sig)
    buy = sig.side == "buy"
    sp, slip = cost.spread, cost.slippage
    expires = _ns(sig.expires) if sig.expires is not None else None
    close_by = _ns(sig.close_by) if sig.close_by is not None else None
    n = len(bars.t)
    i = int(np.searchsorted(bars.t, _ns(sig.created), side="left"))

    # --- waiting for the order to fill ---
    while i < n:
        if expires is not None and bars.t[i] >= expires:
            res.status = "expired"
            return res
        O, H, L = bars.o[i], bars.h[i], bars.l[i]
        fill = _try_fill(sig, O, H, L, sp, slip)
        if fill is not None:
            break
        if (sig.cancel_if_tp_before_fill and sig.tp1 is not None
                and (H >= sig.tp1 if buy else L + sp <= sig.tp1)):
            res.status = "cancelled"   # price ran to target without us; setup is gone
            return res
        i += 1
    else:
        res.status = "no_fill"
        return res

    res.status, res.fill_time, res.fill_price = "filled", _ts(bars.t[i]), fill
    remaining, cur_sl, sl_kind, tp1_done = 1.0, sig.sl, "sl", False
    tp1_fraction = 1.0 if sig.tp2 is None else sig.tp1_close_pct / 100.0
    # Best bid price reached since entry, for the trailing stop. Starts at the fill (bid side)
    # because we can't know whether the fill candle's extreme came before or after the fill.
    best = fill - sp if buy else fill

    def close(idx: int, price: float, fraction: float, reason: str):
        res.exits.append(Exit(_ts(bars.t[idx]), price, fraction, reason))

    # Fill candle: only the stop loss can trigger. A late market fill can already be past the
    # stop: the broker then closes at once at the fill price, never at the better stop price.
    if (bars.l[i] - doubt <= cur_sl) if buy else (bars.h[i] + sp + doubt >= cur_sl):
        price = min(cur_sl, fill - sp) - slip if buy else max(cur_sl, fill + sp) + slip
        close(i, price, remaining, "sl")
        return res
    i += 1

    # --- position open ---
    while i < n:
        O, H, L = bars.o[i], bars.h[i], bars.l[i]
        if close_by is not None and bars.t[i] >= close_by:
            close(i, O if buy else O + sp, remaining, "time")
            return res
        sl_hit = (L - doubt <= cur_sl) if buy else (H + sp + doubt >= cur_sl)
        if sl_hit:
            price = min(cur_sl, O) - slip if buy else max(cur_sl, O + sp) + slip
            close(i, price, remaining, sl_kind)
            return res
        target = sig.tp2 if tp1_done else sig.tp1
        if target is not None and ((H - doubt >= target) if buy else (L + sp + doubt <= target)):
            price = max(target, O) if buy else min(target, O + sp)
            fraction = remaining if (tp1_done or sig.tp2 is None) else tp1_fraction
            close(i, price, fraction, "tp2" if tp1_done else "tp1")
            remaining -= fraction
            if remaining <= 1e-9:
                return res
            tp1_done = True
            if sig.move_sl_to_be and (cur_sl < res.fill_price if buy else cur_sl > res.fill_price):
                cur_sl, sl_kind = res.fill_price, "be"
        # Trailing stop moves only after the candle has closed, so it applies from the next one.
        if sig.trail_distance is not None:
            best = max(best, H) if buy else min(best, L)
            trail = best - sig.trail_distance if buy else best + sig.trail_distance
            if trail > cur_sl if buy else trail < cur_sl:
                cur_sl, sl_kind = trail, "trail"
        i += 1

    last = n - 1
    close(last, bars.c[last] if buy else bars.c[last] + sp, remaining, "data_end")
    return res


def _resolve_oco(results: list[SimResult]) -> tuple[list[SimResult], int]:
    """Within each OCO group keep only the order that filled first."""
    keep, dropped, first_fill = [], 0, {}
    for r in sorted((r for r in results if r.filled), key=lambda r: r.fill_time):
        g = r.signal.oco_group
        if g is None:
            keep.append(r)
        elif g not in first_fill:
            first_fill[g] = r
            keep.append(r)
        else:
            dropped += 1
    return keep, dropped


def run_portfolio(results: list[SimResult], specs: dict[str, SymbolSpec],
                  costs: dict[str, Costs], cfg: dict) -> tuple[pd.DataFrame, Counter]:
    """Apply the risk manager and position sizing to filled signals, in time order."""
    risk_cfg = cfg["risk"]
    risk_pct = risk_cfg["risk_per_trade_pct"] / 100
    daily_limit = risk_cfg["daily_loss_limit_pct"] / 100
    balance = float(cfg["account"]["starting_balance"])

    skipped = Counter(r.status for r in results if not r.filled)
    candidates, skipped["oco_other_side"] = _resolve_oco(results)

    open_trades: list[dict] = []
    rows: list[dict] = []
    current_day, day_start_balance = None, balance

    def roll_day(day):
        nonlocal current_day, day_start_balance
        if current_day is None or day > current_day:
            current_day, day_start_balance = day, balance

    def realise_until(ts):
        nonlocal balance
        open_trades.sort(key=lambda t: t["exit_time"])
        while open_trades and open_trades[0]["exit_time"] <= ts:
            t = open_trades.pop(0)
            roll_day(trading_day(t["exit_time"]))
            balance += t["pnl"]
            t["row"]["balance_after"] = balance

    for r in candidates:
        s = r.signal
        realise_until(r.fill_time)
        roll_day(trading_day(r.fill_time))

        if len(open_trades) >= risk_cfg["max_open_trades"]:
            skipped["max_open_trades"] += 1
            continue
        if sum(t["symbol"] == s.symbol for t in open_trades) >= risk_cfg["max_trades_per_symbol"]:
            skipped["symbol_already_open"] += 1
            continue

        spec, cost = specs[s.symbol], costs[s.symbol]
        risk_money = balance * risk_pct
        value_per_lot = spec.money_per_price_unit(1.0, s.entry)
        lots = spec.round_lots(risk_money / (s.stop_distance * value_per_lot))
        if lots == 0:
            skipped["below_min_lot"] += 1
            continue
        actual_risk = lots * s.stop_distance * value_per_lot

        open_risk = sum(t["risk"] for t in open_trades)
        worst_balance = balance - open_risk - actual_risk
        if day_start_balance - worst_balance > daily_limit * day_start_balance:
            skipped["daily_loss_limit"] += 1
            continue

        pnl = sum(
            e.fraction * lots * (e.price - r.fill_price) * s.direction
            * spec.money_per_price_unit(1.0, e.price)
            for e in r.exits
        ) - lots * cost.commission_per_lot

        row = {
            "strategy": s.strategy, "symbol": s.symbol, "side": s.side, "order": s.label,
            "created": s.created, "fill_time": r.fill_time, "exit_time": r.exit_time,
            "entry": s.entry, "fill_price": r.fill_price, "sl": s.sl, "tp1": s.tp1, "tp2": s.tp2,
            "exit_reason": r.exit_reason, "lots": lots, "risk_usd": actual_risk,
            "balance_before": balance, "pnl": pnl, "r": r.r_multiple,
            "return_pct": pnl / balance * 100, "balance_after": np.nan, "reason": s.reason,
        }
        rows.append(row)
        open_trades.append({"exit_time": r.exit_time, "pnl": pnl, "risk": actual_risk,
                            "symbol": s.symbol, "row": row})

    realise_until(pd.Timestamp.max.tz_localize("UTC"))
    trades = pd.DataFrame(rows)
    if not trades.empty:
        trades = trades.sort_values("exit_time").reset_index(drop=True)
    return trades, skipped
