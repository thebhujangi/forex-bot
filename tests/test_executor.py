"""Place trade / Skip trade, against a pretend MT5 (no real orders are ever sent by these tests)."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd

from forexbot import executor as ex
from forexbot.instruments import load_specs
from forexbot.models import Signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
NOW = pd.Timestamp("2026-11-27 06:00", tz="UTC")


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 0, 1, 2, 3
    ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 5, 6, 8
    ORDER_TIME_GTC, ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 0, 1, 2
    TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE = 10008, 10009
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1

    def __init__(self, bid=100.0, ask=100.2, algo=True, refuse=None):
        self.bid, self.ask, self.algo, self.refuse = bid, ask, algo, refuse
        self.sent, self.positions, self.orders, self.n = [], [], [], 1000

    def symbol_info(self, s):
        return NS(filling_mode=1, digits=2)

    def symbol_info_tick(self, s):
        return NS(bid=self.bid, ask=self.ask)

    def terminal_info(self):
        return NS(connected=True, trade_allowed=self.algo)

    def account_info(self):
        return NS(trade_expert=True, balance=10_000.0)

    def last_error(self):
        return (1, "x")

    def positions_get(self):
        return list(self.positions)

    def orders_get(self):
        return list(self.orders)

    def order_send(self, r):
        self.sent.append(r)
        if self.refuse:
            return NS(retcode=self.refuse, comment="no", order=0, price=0)
        self.n += 1
        a = r["action"]
        if a == self.TRADE_ACTION_DEAL and "position" in r:
            self.positions = [p for p in self.positions if p.ticket != r["position"]]
        elif a == self.TRADE_ACTION_DEAL:
            self.positions.append(NS(ticket=self.n, symbol=r["symbol"], type=r["type"], volume=r["volume"],
                                     price_open=r["price"], sl=r["sl"], tp=r.get("tp", 0), magic=r["magic"],
                                     comment=r["comment"]))
        elif a == self.TRADE_ACTION_PENDING:
            self.orders.append(NS(ticket=self.n, magic=r["magic"], comment=r["comment"]))
            return NS(retcode=self.TRADE_RETCODE_PLACED, order=self.n, price=0, comment="")
        elif a == self.TRADE_ACTION_SLTP:
            p = next(p for p in self.positions if p.ticket == r["position"])
            p.sl = r["sl"]
        elif a == self.TRADE_ACTION_REMOVE:
            self.orders = [o for o in self.orders if o.ticket != r["order"]]
        return NS(retcode=self.TRADE_RETCODE_DONE, order=self.n, price=r.get("price", 0), comment="")


SPEC = load_specs()["XAUUSD"]


def sig(**k):
    d = dict(strategy="x", symbol="XAUUSD", side="buy", order_type="market", created=NOW, entry=100.0, sl=95.0, tp1=110.0)
    d.update(k)
    return Signal(**d)


def test_market_trade_has_entry_stop_target_and_one_percent_lots():
    m = FakeMT5()
    r = ex.place(m, "XAUUSD", sig(), SPEC, "abc12345", 10_000, 1.0)
    assert len(r) == 1 and r[0].ok
    req = m.sent[0]
    assert req["sl"] == 95.0 and req["tp"] == 110.0 and req["comment"] == "fxbot abc12345" and req["magic"] == ex.MAGIC
    assert req["price"] == 100.2                                  # buys at the ask
    risk = req["volume"] * (100.2 - 95.0) * SPEC.contract_size
    assert 80 <= risk <= 100                                      # about 1% of $10,000, never more


def test_two_targets_become_two_half_orders_and_a_pair_places_both_pending_orders():
    m = FakeMT5()
    r = ex.place(m, "XAUUSD", sig(order_type="limit", entry=99.0, tp1=104.0, tp2=109.0), SPEC, "a", 10_000, 1.0)
    assert [x.ok for x in r] == [True, True]
    assert [q["tp"] for q in m.sent] == [104.0, 109.0] and m.sent[0]["volume"] == m.sent[1]["volume"]
    assert m.sent[0]["action"] == m.TRADE_ACTION_PENDING and m.sent[0]["price"] == 99.0


def test_nothing_is_placed_when_the_trade_is_already_over_or_mt5_refuses():
    m = FakeMT5(bid=94.0, ask=94.2)
    r = ex.place(m, "XAUUSD", sig(), SPEC, "a", 10_000, 1.0)
    assert not r[0].ok and "past the stop" in r[0].text and m.sent == []
    m = FakeMT5(refuse=10027)
    r = ex.place(m, "XAUUSD", sig(order_type="limit", entry=99.0, tp1=104.0, tp2=109.0), SPEC, "a", 10_000, 1.0)
    assert len(r) == 1 and "Algo Trading" in r[0].text           # stops after the first refusal: no half-placed pair
    assert "Algo Trading" in ex.ready(FakeMT5(algo=False))


def test_only_the_bots_own_trades_are_touched():
    m = FakeMT5()
    ex.place(m, "XAUUSD", sig(), SPEC, "mine", 10_000, 1.0)
    m.positions.append(NS(ticket=1, symbol="XAUUSD", type=0, volume=1, price_open=100, sl=90, tp=0, magic=0, comment=""))
    assert ex.move_stop(m, "mine", 97.0, 2) == []
    assert ex.move_stop(m, "mine", 96.0, 2) == []                 # never moves a stop backwards
    assert {p.ticket: p.sl for p in m.positions} == {1001: 97.0, 1: 90}
    n, bad = ex.close_all(m, "mine")
    assert n == 1 and not bad and [p.ticket for p in m.positions] == [1]


def make_bot(monkeypatch, m):
    import run_bot as rb
    from forexbot.config import load_config
    monkeypatch.setattr(rb, "log", lambda x: None)
    bot = rb.Bot.__new__(rb.Bot)
    bot.cfg, bot.specs, bot.mt5, bot.names = load_config(), load_specs(), m, {"XAUUSD": "XAUUSD"}
    bot.state, bot.tracked, bot.cloud = {"sent": {}}, [], False
    bot.ensure_mt5 = lambda: True
    bot.balance = lambda: 10_000.0
    return bot, rb


def test_place_trade_button_places_once_and_the_bot_then_manages_it(monkeypatch):
    m = FakeMT5()
    bot, rb = make_bot(monkeypatch, m)
    src = rb.Source(name="commodity_trend", strategy="daily_trend", symbols=["XAUUSD"], horizon="LONG-TERM", tier="", stats="")
    t = rb.track(sig(tp1=None, trail_distance=8.0), src)
    bot.tracked = [t]
    text = bot.execute([t])
    assert "Placed in MT5" in text and t.auto and t.status == "placed" and len(m.positions) == 1
    assert "already placed" in bot.execute([t]) and len(m.positions) == 1        # a second tap doesn't double it
    t.cur_sl = 99.0
    assert "Stop moved in MT5" in bot.manage(t, f"trail:{t.id}:99.00", "Move your stop")
    assert m.positions[0].sl == 99.0
    t2 = rb.track(sig(side="sell", entry=100, sl=105, tp1=None), src)
    assert "Closed in MT5" in bot.manage(t, f"reverse:{t.id}", "Trend has reversed")
    assert m.positions == []
    assert bot.manage(t2, f"trail:{t2.id}:1", "Move your stop") == "Move your stop"   # not placed by the bot: just the message


def test_skip_trade_places_nothing(monkeypatch):
    m = FakeMT5()
    bot, rb = make_bot(monkeypatch, m)
    src = rb.Source(name="x", strategy="daily_trend", symbols=["XAUUSD"], horizon="LONG-TERM", tier="", stats="")
    t = rb.track(sig(), src)
    bot.tracked = [t]
    sent = []
    bot.tg = NS(chat_id="1", answer_button=lambda *a: None, send=sent.append)
    bot.handle_button({"id": "q", "data": f"skip:{t.id}", "message": {"chat": {"id": 1}}})
    assert m.sent == [] and t.status == "skipped" and "skipped" in sent[0]
    bot.handle_button({"id": "q", "data": f"place:{t.id}", "message": {"chat": {"id": 1}}})
    assert len(m.sent) == 1 and "Placed in MT5" in sent[1]
