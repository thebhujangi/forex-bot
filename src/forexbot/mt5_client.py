"""Read-only connection to the MetaTrader 5 terminal that is open on this PC.

The bot never sends orders and never needs your password: it attaches to the MT5 window
you are already logged into, and only reads prices and symbol information.
"""
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .instruments import SymbolSpec
from .timeutils import eu_dst_offset_now, ny_plus_7_offset_now, server_to_utc

ALIASES = {"XAUUSD": ["GOLD"]}


def _mt5():
    import MetaTrader5 as mt5
    return mt5


def connect(cfg: dict):
    mt5 = _mt5()
    path = (cfg.get("mt5") or {}).get("terminal_path")
    ok = mt5.initialize(path) if path else mt5.initialize()
    if not ok:
        raise RuntimeError(
            f"Could not connect to MetaTrader 5 ({mt5.last_error()}).\n"
            "Open the MT5 desktop app, log in to your demo account, then run this again."
        )
    return mt5


def account_summary(mt5) -> dict:
    a = mt5.account_info()
    t = mt5.terminal_info()
    return {
        "company": a.company, "server": a.server, "login": a.login, "currency": a.currency,
        "balance": a.balance, "leverage": a.leverage,
        "demo": a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        "terminal": t.name, "max_bars": t.maxbars,
    }


def resolve_symbol(mt5, canonical: str, overrides: dict) -> str:
    if canonical in (overrides or {}):
        name = overrides[canonical]
    else:
        names = [s.name for s in mt5.symbols_get()]
        if canonical in names:
            name = canonical
        else:
            prefixes = [canonical] + ALIASES.get(canonical, [])
            cands = [n for n in names if any(n.upper().startswith(p) for p in prefixes)]
            if not cands:
                raise LookupError(f"{canonical}: no matching symbol at this broker. "
                                  f"Add it under symbol_map in config.yaml.")
            name = min(cands, key=len)
    if not mt5.symbol_select(name, True):
        raise LookupError(f"{name}: could not enable in Market Watch ({mt5.last_error()})")
    return name


def symbol_spec(mt5, canonical: str, broker_symbol: str) -> SymbolSpec:
    i = mt5.symbol_info(broker_symbol)
    return SymbolSpec(
        symbol=canonical, broker_symbol=broker_symbol, digits=i.digits,
        contract_size=i.trade_contract_size, currency_base=i.currency_base,
        currency_profit=i.currency_profit, volume_min=i.volume_min,
        volume_step=i.volume_step, volume_max=i.volume_max,
    )


def latest_tick(mt5, broker_symbol: str, wait: float = 3.0):
    """Latest tick; a symbol just added to Market Watch needs a moment for its first quote."""
    deadline = time.time() + wait
    while True:
        tick = mt5.symbol_info_tick(broker_symbol)
        if (tick and tick.bid) or time.time() > deadline:
            return tick
        time.sleep(0.2)


def live_spread(mt5, broker_symbol: str) -> float | None:
    tick = latest_tick(mt5, broker_symbol)
    return tick.ask - tick.bid if tick and tick.ask and tick.bid else None


def detect_server_time(mt5, broker_symbol: str) -> tuple[str | None, str]:
    """Work out the server clock from the latest tick. Only reliable while the market is open."""
    tick = latest_tick(mt5, broker_symbol)
    if tick is None:
        return None, "no tick available"
    diff = tick.time - time.time()
    offset = round(diff / 3600)
    if abs(diff - offset * 3600) > 600:
        return None, "market looks closed (last tick is old), can't detect the server clock"
    eu, ny = eu_dst_offset_now(), ny_plus_7_offset_now()
    if offset == eu and eu != ny:
        return "eu_dst", f"server is UTC{offset:+d} now -> GMT+2/+3 changing on the EU dates"
    if offset == ny and eu != ny:
        return "ny_plus_7", f"server is UTC{offset:+d} now -> GMT+2/+3 changing on the US dates"
    if offset == ny:
        return "ny_plus_7", (f"server is UTC{offset:+d} now -> GMT+2/+3 (US and EU clocks agree today; "
                             f"re-check in the week between the EU and US clock changes)")
    return str(offset), f"server is a fixed UTC{offset:+d}"


def canonical_symbol(broker_symbol: str, known: list[str]) -> str | None:
    """Map a broker name like 'XAUUSD.m' or 'GOLD' back to ours ('XAUUSD')."""
    up = broker_symbol.upper()
    for s in known:
        if up.startswith(s) or any(up.startswith(a) for a in ALIASES.get(s, [])):
            return s
    return None


def open_positions(mt5, known: list[str]) -> list:
    """Open trades on the account, read-only, as assistant.Position objects."""
    from .assistant import Position
    out = []
    for p in mt5.positions_get() or ():
        sym = canonical_symbol(p.symbol, known)
        if sym is None:
            continue
        out.append(Position(
            ticket=p.ticket, symbol=sym, side="buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
            lots=p.volume, open_price=p.price_open, sl=p.sl, tp=p.tp, price=p.price_current,
            profit=p.profit + p.swap,
        ))
    return out


def candle_spike(mt5, broker_symbol: str, lookback: int = 24) -> float | None:
    """Size of the last closed 5-minute candle compared with the average of the ones before."""
    rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M5, 0, lookback + 2)
    if rates is None or len(rates) < lookback + 2:
        return None
    rng = rates["high"] - rates["low"]
    normal = rng[:-2].mean()
    return float(rng[-2] / normal) if normal > 0 else None


def recent_bars(mt5, broker_symbol: str, timeframe: str, count: int, server_time: str) -> pd.DataFrame:
    """The latest `count` candles in UTC, dropping the one still forming."""
    rates = mt5.copy_rates_from_pos(broker_symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"), 0, count)
    if rates is None or not len(rates):
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    df = pd.DataFrame(rates)
    idx = server_to_utc(pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s")), server_time)
    df = df[["open", "high", "low", "close"]].set_axis(idx)
    df = df[df.index.notna()]
    step = pd.Timedelta(minutes={"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240,
                                 "D1": 1440}[timeframe])
    return df[df.index + step <= pd.Timestamp.now(tz="UTC")]


def side_price(mt5, broker_symbol: str, side: str) -> float | None:
    """Price at which an open trade would close: bid for buys, ask for sells."""
    tick = latest_tick(mt5, broker_symbol)
    if not tick or not tick.bid:
        return None
    return tick.bid if side == "buy" else tick.ask


TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


def download_bars(mt5, broker_symbol: str, timeframe: str, start: datetime, end: datetime,
                  server_time: str) -> pd.DataFrame:
    tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
    frames, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=180), end)
        rates = mt5.copy_rates_range(broker_symbol, tf, cur, nxt)
        if rates is not None and len(rates):
            frames.append(pd.DataFrame(rates))
        cur = nxt
    if not frames:
        raise RuntimeError(f"{broker_symbol} {timeframe}: no data returned ({mt5.last_error()})")
    df = pd.concat(frames).drop_duplicates("time").sort_values("time")
    idx = server_to_utc(pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s")), server_time)
    out = df[["open", "high", "low", "close", "tick_volume", "spread"]].set_axis(idx)
    out = out[out.index.notna()]
    return out[~out.index.duplicated()]


def utc(d) -> datetime:
    return pd.Timestamp(d).to_pydatetime().replace(tzinfo=timezone.utc)
