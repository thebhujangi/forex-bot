"""Places and manages trades in MT5, but ONLY for signals you tapped "Place trade" on in Telegram.

The bot never trades on its own. Each order it sends carries the comment "fxbot <signal id>" and a
fixed magic number, so it only ever touches its own orders, never trades you opened yourself.
It works on whatever account MT5 is logged into (demo or live); MT5's "Algo Trading" button must be on.

Kept free of Telegram so it can be tested with a fake MT5.
"""
from dataclasses import dataclass

from .assistant import position_size
from .instruments import SymbolSpec
from .models import Signal

MAGIC = 26112026

RETCODES = {
    10004: "the price changed (requote)", 10006: "the broker rejected it", 10013: "the order was invalid",
    10014: "the lot size isn't allowed", 10015: "the entry price isn't valid any more (price moved past it)",
    10016: "the stop loss or take profit is too close to the price", 10017: "trading is disabled for this market",
    10018: "the market is closed", 10019: "there isn't enough free margin", 10021: "there's no price right now",
    10024: "too many requests", 10026: "the broker has disabled automated trading",
    10027: "Algo Trading is switched off in MT5 (click the 'Algo Trading' button at the top of MT5)",
    10030: "the broker doesn't accept this order filling type", 10031: "no connection to the broker",
}


@dataclass
class Placed:
    ok: bool
    text: str                    # one line for Telegram
    ticket: int | None = None
    lots: float = 0.0
    price: float | None = None


def comment(signal_id: str) -> str:
    return f"fxbot {signal_id}"


def _why(res, mt5) -> str:
    if res is None:
        return f"MT5 didn't answer ({mt5.last_error()})"
    return RETCODES.get(res.retcode, f"MT5 said: {res.comment} ({res.retcode})")


def _filling(mt5, info) -> int:
    mode = getattr(info, "filling_mode", 0) or 0
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def lots_for(spec: SymbolSpec, balance: float, risk_pct: float, entry: float, sl: float, orders: int) -> tuple[float, str]:
    """Lots for each order so the whole trade loses `risk_pct` at the stop. If even the smallest lot is
    more, the smallest lot is used and the message says how much it really risks."""
    size = position_size(spec, balance, risk_pct / orders, entry, sl)
    if size["lots"]:
        return size["lots"], ""
    pct = orders * size["min_lot_risk_usd"] / balance * 100
    return spec.volume_min, f" (smallest lot size, risks {pct:.1f}% instead of {risk_pct:g}%)"


def place(mt5, broker_symbol: str, sig: Signal, spec: SymbolSpec, signal_id: str, balance: float,
          risk_pct: float, sl: float | None = None) -> list[Placed]:
    """Send the signal's order(s): one per take profit (a two-target signal becomes two orders of half
    the size, as MT5 allows one take profit per order). `sl` overrides the signal's stop (a trailing
    stop that has already moved)."""
    info = mt5.symbol_info(broker_symbol)
    tick = mt5.symbol_info_tick(broker_symbol)
    if info is None or tick is None or not tick.bid:
        return [Placed(False, f"no price for {sig.symbol} in MT5 right now")]
    sl = round(sl if sl is not None else sig.sl, spec.digits)
    buy = sig.side == "buy"
    sign = 1 if buy else -1
    market = sig.order_type == "market"
    price = (tick.ask if buy else tick.bid) if market else sig.entry
    if sign * (price - sl) <= 0:
        return [Placed(False, f"price {spec.fmt(price)} is already past the stop loss {spec.fmt(sl)}, so the trade is over")]
    targets = [t for t in (sig.tp1, sig.tp2) if t is not None] or [None]
    if market and targets[-1] is not None and sign * (targets[-1] - price) <= 0:
        return [Placed(False, f"price {spec.fmt(price)} is already past the target {spec.fmt(targets[-1])}")]
    lots, note = lots_for(spec, balance, risk_pct, price, sl, len(targets))
    kinds = {("buy", "limit"): mt5.ORDER_TYPE_BUY_LIMIT, ("sell", "limit"): mt5.ORDER_TYPE_SELL_LIMIT,
             ("buy", "stop"): mt5.ORDER_TYPE_BUY_STOP, ("sell", "stop"): mt5.ORDER_TYPE_SELL_STOP,
             ("buy", "market"): mt5.ORDER_TYPE_BUY, ("sell", "market"): mt5.ORDER_TYPE_SELL}
    out = []
    for k, tp in enumerate(targets):
        req = {"action": mt5.TRADE_ACTION_DEAL if market else mt5.TRADE_ACTION_PENDING,
               "symbol": broker_symbol, "volume": lots, "type": kinds[(sig.side, sig.order_type)],
               "price": round(price, spec.digits), "sl": sl, "deviation": 30, "magic": MAGIC,
               "comment": comment(signal_id), "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(mt5, info)}
        if tp is not None:
            req["tp"] = round(tp, spec.digits)
        if not market:
            req["type_filling"] = mt5.ORDER_FILLING_RETURN
        res = mt5.order_send(req)
        ok = res is not None and res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        what = f"{sig.label} {lots:g} lots" + (f", take profit {spec.fmt(tp)}" if tp is not None else "")
        if ok:
            at = res.price or price
            out.append(Placed(True, f"{what} at {spec.fmt(at)}, stop {spec.fmt(sl)}{note if k == 0 else ''} "
                                    f"(ticket {res.order})", res.order, lots, at))
        else:
            out.append(Placed(False, f"{what}: not placed, {_why(res, mt5)}"))
            break                          # don't leave a half-placed pair; say what happened
    return out


def ours(mt5, signal_id: str) -> tuple[list, list]:
    """This signal's open positions and pending orders in MT5 (found by the comment the bot gave them)."""
    c = comment(signal_id)
    pos = [p for p in mt5.positions_get() or () if p.magic == MAGIC and p.comment == c]
    orders = [o for o in mt5.orders_get() or () if o.magic == MAGIC and o.comment == c]
    return pos, orders


def move_stop(mt5, signal_id: str, sl: float, digits: int) -> list[str]:
    """Move the stop loss of this signal's open positions (trailing stop / breakeven). Problems only."""
    problems = []
    for p in ours(mt5, signal_id)[0]:
        problems += move_stop_one(mt5, p, sl, digits)     # never moves a stop the wrong way
    return problems


def breakeven(mt5, signal_id: str, digits: int) -> list[str]:
    """After the first target: the stop of what is left goes to its own entry price."""
    problems = []
    for p in ours(mt5, signal_id)[0]:
        problems += move_stop_one(mt5, p, p.price_open, digits)
    return problems


def move_stop_one(mt5, p, sl: float, digits: int) -> list[str]:
    better = sl > p.sl if p.type == mt5.POSITION_TYPE_BUY else (p.sl == 0 or sl < p.sl)
    if not better:
        return []
    res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": p.symbol,
                          "sl": round(sl, digits), "tp": p.tp, "magic": MAGIC})
    return [] if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE else [f"ticket {p.ticket}: {_why(res, mt5)}"]


def delete_pending(mt5, signal_id: str) -> tuple[int, list[str]]:
    done, problems = 0, []
    for o in ours(mt5, signal_id)[1]:
        res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket, "magic": MAGIC})
        if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
            done += 1
        else:
            problems.append(f"order {o.ticket}: {_why(res, mt5)}")
    return done, problems


def close_all(mt5, signal_id: str) -> tuple[int, list[str]]:
    """Close this signal's open positions at the market and delete its pending orders."""
    done, problems = delete_pending(mt5, signal_id)
    for p in ours(mt5, signal_id)[0]:
        tick = mt5.symbol_info_tick(p.symbol)
        buy = p.type == mt5.POSITION_TYPE_BUY
        info = mt5.symbol_info(p.symbol)
        res = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol,
                              "volume": p.volume, "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                              "price": tick.bid if buy else tick.ask, "deviation": 30, "magic": MAGIC,
                              "comment": p.comment, "type_time": mt5.ORDER_TIME_GTC,
                              "type_filling": _filling(mt5, info)})
        if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
            done += 1
        else:
            problems.append(f"ticket {p.ticket}: {_why(res, mt5)}")
    return done, problems


def ready(mt5) -> str | None:
    """Why MT5 can't take orders from the bot right now, or None."""
    t, a = mt5.terminal_info(), mt5.account_info()
    if t is None or a is None:
        return "MT5 isn't connected"
    if not t.connected:
        return "MT5 has no connection to Elev8"
    if not t.trade_allowed:
        return RETCODES[10027]
    if not a.trade_expert:
        return "the account doesn't allow automated trading"
    return None
