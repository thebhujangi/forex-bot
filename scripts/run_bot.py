"""Telegram trading assistant. Never places trades; only reads prices/your account and messages you.

Three ways to run it:
  (default)        On your PC with MT5: signals, follow-ups, news, headlines, crowd, AND it watches
                   your MT5 trades (no stop loss, breakeven, daily loss...). Commands in Telegram.
  --cloud          In the cloud (GitHub Actions): same signals/news/commands, prices from free web
                   APIs. Can't see your MT5 account.
  --monitor-only   On your PC while the cloud copy runs: ONLY watches your MT5 trades and sends
                   warnings. Doesn't read commands or send signals, so the two never clash.
  --minutes N      Stop after N minutes (the cloud runs it in 15-minute shifts).

Usage: double-click "Start Bot.bat", or  .venv\\Scripts\\python scripts\\run_bot.py [options]
"""
import argparse
import html
import json
import sys
import time
import traceback
from datetime import datetime

import pandas as pd

from forexbot import DATA_DIR, STATE_DIR
from forexbot import headlines as hl
from forexbot import myfxbook as fxb
from forexbot.assistant import MonitorContext, check_positions, position_size
from forexbot.config import load_config
from forexbot.feeds import ApiMarket, FeedError, Mt5Market
from forexbot.instruments import load_costs, load_specs
from forexbot.news import fetch_events, relevant
from forexbot.signals import (HISTORY_COLUMNS, Source, cancel_sibling, copy_buttons, follow, follow_replay,
                              format_pair, format_signal, from_dict, history_row, new_signals, reversal_alert,
                              to_dict, track, weekly_report)
from forexbot.strategies.common import parse_hm
from forexbot.telegram import Telegram, TelegramError, load_env
from forexbot.timeutils import NEW_YORK, ROLLOVER_SHIFT, TIMEFRAME_MINUTES, trading_day

sys.stdout.reconfigure(encoding="utf-8")
HISTORY_FILE = STATE_DIR / "signal_history.csv"
LOG_FILE = DATA_DIR / "bot.log"

HELP = """<b>Forex Bot assistant</b>
I send signals and warnings. I never place or close trades myself.

<b>Signals</b> (sent automatically, you don't need to ask)
📅 LONG-TERM: tested trend signals, a few a month.
⚡ INTRADAY ⚗️ EXPERIMENTAL: several a day, unproven (testing says they lose slightly).
Tap ✅ Placed or ❌ Skipped. I then tell you when it fills, when to take profit or move
your stop, when it's stopped out, when to close, and if it's going badly.

<b>Commands</b>
/signals: signals I'm following right now
/report: report card (also sent every Sunday 8pm)
/headlines: latest news about your markets
/news: big scheduled news in the next 24 hours
/crowd: what % of retail traders are buying vs selling
/size XAUUSD 4105.5 4098: lot size for entry 4105.5, stop 4098
/size XAUUSD 4098: same, using the current price as entry
/status: your MT5 balance and open trades (PC only)
/help: this message"""


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def last_close_time(tf: str, now: pd.Timestamp) -> pd.Timestamp:
    """When the most recent candle of this timeframe closed (by the clock, no data needed)."""
    if tf == "D1":
        ny = now.tz_convert(NEW_YORK)
        roll = ny.normalize() + pd.Timedelta(hours=17)
        if ny < roll:
            roll -= pd.Timedelta(days=1)
        return roll.tz_convert("UTC")
    return now.floor(f"{TIMEFRAME_MINUTES[tf]}min")


def fx_market_open(now: pd.Timestamp) -> bool:
    """Forex/gold trade from Sunday 17:00 to Friday 17:00 New York."""
    ny = now.tz_convert(NEW_YORK)
    wd, h = ny.weekday(), ny.hour
    return not (wd == 5 or (wd == 4 and h >= 17) or (wd == 6 and h < 17))


CRYPTO = {"BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "BNBUSD", "LTCUSD", "DOGEUSD", "ADAUSD"}


class Bot:
    def __init__(self, cfg: dict, cloud: bool = False, monitor_only: bool = False):
        self.cfg, self.bc = cfg, cfg["bot"]
        self.cloud, self.monitor_only = cloud, monitor_only
        # The PC trade monitor keeps its own memory so it never clashes with the signal bot.
        self.state_file = STATE_DIR / ("monitor_state.json" if monitor_only else "bot_state.json")
        self.tz = self.bc["timezone"]
        self.tg = Telegram()
        self.specs = load_specs()
        self.costs = {}
        self.mt5 = None
        self.names: dict[str, str] = {}
        self.sources = [Source(**s) for s in cfg.get("signals", [])]
        self.state = self._load_state()
        self.tracked = [from_dict(d) for d in self.state.get("tracked", [])]
        self.headlines: list = []
        self.fxb = fxb.Myfxbook()
        self.market = ApiMarket(load_env().get("TWELVEDATA_API_KEY", "")) if cloud else None

    @property
    def all_symbols(self) -> list[str]:
        syms = list(self.bc["watch_symbols"])
        for s in self.sources:
            syms += [x for x in s.symbols if x not in syms]
        return syms

    def due(self, name: str, seconds: float) -> bool:
        """Simple timers that survive restarts (the cloud copy restarts every 15 minutes)."""
        timers = self.state.setdefault("timers", {})
        if time.time() - timers.get(name, 0) < seconds:
            return False
        timers[name] = time.time()
        return True

    # ---------- state ----------
    def _load_state(self) -> dict:
        s = {"sent": {}, "offset": None, "initial_sl": {}, "day": None, "day_start_equity": 0.0,
             "last_brief": None, "tracked": [], "last_signal_bar": {}}
        if self.state_file.exists():
            s.update(json.loads(self.state_file.read_text(encoding="utf-8")))
        return s

    def _save_state(self):
        now = pd.Timestamp.now(tz="UTC")
        self.state["sent"] = {k: v for k, v in self.state["sent"].items()
                              if v >= (now - pd.Timedelta(days=7)).isoformat()}
        old = (now - pd.Timedelta(days=30)).isoformat()
        self.tracked = [t for t in self.tracked if t.active or not t.recorded or t.created >= old]
        self.state["tracked"] = [to_dict(t) for t in self.tracked]
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def send_once(self, key: str, text: str, buttons=None):
        if key in self.state["sent"]:
            return
        self.tg.send(text, buttons=buttons)
        self.state["sent"][key] = pd.Timestamp.now(tz="UTC").isoformat()
        log(f"sent alert {key}")

    # ---------- MT5 (PC only) ----------
    def ensure_mt5(self) -> bool:
        if self.cloud:
            return False
        if self.mt5 is not None and self.mt5.terminal_info() is not None and self.mt5.account_info():
            return True
        from forexbot import mt5_client as m
        try:
            self.mt5 = m.connect(self.cfg)
            self.names = {s: m.resolve_symbol(self.mt5, s, self.cfg.get("symbol_map")) for s in self.all_symbols}
            for s, name in self.names.items():
                self.specs[s] = m.symbol_spec(self.mt5, s, name)
            self.market = Mt5Market(self.mt5, self.names, self.cfg["broker"]["server_time"])
            self.state["sent"].pop("mt5_down", None)
            return True
        except Exception as e:
            self.mt5 = None
            log(f"MT5 not available: {e}")
            self.send_once("mt5_down", "🔌 <b>Can't reach MT5 on your PC.</b> Open MetaTrader 5 and log in; "
                                       "I'll reconnect automatically.")
            return False

    def market_ready(self) -> bool:
        return self.cloud or self.ensure_mt5()

    def balance(self) -> float:
        if self.ensure_mt5():
            return self.mt5.account_info().balance
        return float(self.cfg["account"]["starting_balance"])

    def cost(self, symbol: str, price: float):
        if symbol not in self.costs:
            self.costs[symbol] = load_costs(self.cfg, symbol)
        return self.costs[symbol].at_price(price)

    # ---------- commands ----------
    def handle_updates(self, wait: int):
        for u in self.tg.updates(self.state["offset"], timeout=wait):
            self.state["offset"] = u["update_id"] + 1
            if "callback_query" in u:
                self.handle_button(u["callback_query"])
                continue
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(self.tg.chat_id):
                continue   # only you can use this bot
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            cmd, *args = text.split()
            cmd = cmd.split("@")[0].lower()
            try:
                reply = {"/size": self.cmd_size, "/news": self.cmd_news, "/status": self.cmd_status,
                         "/signals": self.cmd_signals, "/crowd": self.cmd_crowd, "/report": self.cmd_report,
                         "/headlines": self.cmd_headlines,
                         "/help": lambda a: HELP, "/start": lambda a: HELP}.get(cmd, lambda a: None)(args)
            except Exception as e:
                reply = f"Sorry, that didn't work: {html.escape(str(e))}"
            self.tg.send(reply or "I don't know that command. Send /help.")
            log(f"command {cmd}")

    def handle_button(self, q: dict):
        if str(q.get("message", {}).get("chat", {}).get("id")) != str(self.tg.chat_id):
            return
        action, _, ids = (q.get("data") or "").partition(":")
        targets = [t for t in self.tracked if t.id in ids.split(",") and t.active]
        if not targets:
            self.tg.answer_button(q["id"], "That signal is already finished.")
            return
        if action == "closed":
            for t in targets:
                price = self.market.price(t.symbol, t.side) if t.fill_notified and self.market_ready() else None
                t.close(price if price is not None else t.entry, outcome="closed_in_mt5")
            self.tg.answer_button(q["id"], "Got it")
            self.tg.send(f"👍 OK, {targets[0].symbol} is finished. No more messages about it.")
            log(f"signal {ids} closed by you")
            return
        for t in targets:
            t.status = "placed" if action == "placed" else "skipped"
            t.placed = t.status == "placed"
        self.tg.answer_button(q["id"], "Got it")
        t = targets[0]
        both = " orders" if len(targets) > 1 else ""
        if t.status == "placed":
            self.tg.send(f"👍 Following your {t.symbol}{both}. Make sure every stop loss is set in MT5. "
                         f"I'll message you when anything changes.")
        else:
            self.tg.send(f"OK, skipped {t.symbol}{both}. I'll still score it in the weekly report.")
        log(f"signal {ids} {t.status}")

    def current_r(self, t) -> float | None:
        price = self.market.price(t.symbol, t.side) if self.market_ready() else None
        risk = abs(t.entry - t.sl)
        return t.sign * (price - t.entry) / risk if price and risk else None

    def save_history(self):
        done = [t for t in self.tracked if t.status == "closed" and not t.recorded]
        if not done:
            return
        new = not HISTORY_FILE.exists()
        pd.DataFrame([history_row(t) for t in done], columns=HISTORY_COLUMNS).to_csv(
            HISTORY_FILE, mode="a", header=new, index=False)
        for t in done:
            t.recorded = True

    def cmd_report(self, args=None) -> str:
        self.save_history()
        hist = pd.read_csv(HISTORY_FILE) if HISTORY_FILE.exists() else pd.DataFrame(columns=HISTORY_COLUMNS)
        now = pd.Timestamp.now(tz="UTC")
        account = None
        if self.ensure_mt5():
            deals = self.mt5.history_deals_get((now - pd.Timedelta(days=7)).to_pydatetime(),
                                               (now + pd.Timedelta(days=1)).to_pydatetime()) or ()
            closing = [d for d in deals if d.entry == self.mt5.DEAL_ENTRY_OUT]
            account = {"trades": len(closing), "profit": sum(d.profit + d.swap + d.commission for d in closing),
                       "balance": self.mt5.account_info().balance}
        open_sigs = [(t, self.current_r(t)) for t in self.tracked if t.active]
        balance, risk = self.balance(), self.cfg["risk"]["risk_per_trade_pct"]
        targets = self.cfg.get("targets") or {}
        return weekly_report(hist, open_sigs, now, account, per_r=balance * risk / 100,
                             start_balance=balance, risk_pct=risk, target_pct=targets.get("monthly_return_pct"),
                             tested_pct=targets.get("tested_monthly_pct"), tested={s.name for s in self.sources})

    def weekly_report(self):
        now = pd.Timestamp.now(tz=self.tz)
        when = parse_hm(self.bc.get("weekly_report", "20:00"))
        week = f"{now:%G-W%V}"
        if now.weekday() != 6 or (now.hour, now.minute) < (when.hour, when.minute) \
                or self.state.get("last_report") == week:
            return
        self.tg.send(self.cmd_report())
        self.state["last_report"] = week      # only once it has really gone out
        log("sent weekly report")

    def cmd_signals(self, args=None) -> str:
        active = [t for t in self.tracked if t.active and t.status != "skipped"]
        if not active:
            return ("No signals open right now. They're sent automatically: long-term ones a few times a "
                    "month, experimental intraday ones several times a day while markets are active.")
        rows = []
        for t in active:
            spec = self.specs[t.symbol]
            r = self.current_r(t)
            rows.append(f"• {'📅' if t.horizon == 'LONG-TERM' else '⚡'} {t.side.upper()} {t.symbol} @ "
                        f"{spec.fmt(t.entry)} · stop {spec.fmt(t.cur_sl)} · "
                        f"{f'{r:+.1f}x risk' if r is not None else 'price n/a'} · "
                        f"{'placed' if t.status == 'placed' else 'not placed yet'}")
        return "<b>Signals I'm following</b>\n" + "\n".join(rows)

    def cmd_crowd(self, args=None) -> str:
        crowd = fxb.latest()
        rows = [f"• <b>{s}</b>: {html.escape(fxb.describe(crowd[s]))}" for s in self.all_symbols if s in crowd]
        if not rows:
            return "No crowd data yet (recorded hourly from Myfxbook; needs 200+ open positions)."
        return "👥 <b>Retail crowd</b> (Myfxbook)\n" + "\n".join(rows)

    def _headline_text(self, h) -> str:
        tags = ", ".join("all USD markets" if x == "USD" else x for x in h.markets)
        return (f"{'🚨' if h.big else '📰'} <b>{html.escape(h.title)}</b>\n"
                f"{html.escape(h.source)} · {h.time.tz_convert(self.tz):%a %H:%M} · affects {tags}\n"
                f"<a href=\"{html.escape(h.link)}\">Read more</a>")

    def cmd_headlines(self, args=None) -> str:
        if not self.headlines:
            self.headlines = hl.fetch_all(self.all_symbols)
        recent = self.headlines[:8]
        if not recent:
            return "No recent headlines about your markets."
        return "📰 <b>Latest headlines</b> (🚨 = big)\n\n" + "\n\n".join(self._headline_text(h) for h in recent)

    def check_headlines(self):
        """Every 10 minutes; push only big, fresh headlines about your markets."""
        if not self.due("headlines", 600):
            return
        self.headlines = hl.fetch_all(self.all_symbols)
        seen = self.state.setdefault("seen_headlines", {})
        first_run = not seen
        now_local = pd.Timestamp.now(tz=self.tz)
        quiet = parse_hm(self.bc["headline_quiet_start"]) <= now_local.time() or \
            now_local.time() < parse_hm(self.bc["headline_quiet_end"])
        pushed = [t for t in self.state.get("pushed_times", []) if time.time() - t < 3600]
        fresh_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=2)
        for h in reversed(self.headlines):                   # oldest first
            if h.key in seen:
                continue
            seen[h.key] = pd.Timestamp.now(tz="UTC").isoformat()
            if first_run or not h.big or h.time < fresh_cutoff:
                continue
            if quiet and h.source not in hl.ALWAYS_BIG:
                continue
            if len(pushed) >= self.bc["headline_max_per_hour"]:
                continue
            self.tg.send(self._headline_text(h))
            pushed.append(time.time())
            log(f"pushed headline: {h.title[:60]}")
        self.state["pushed_times"] = pushed
        cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)).isoformat()
        self.state["seen_headlines"] = {k: v for k, v in seen.items() if v >= cutoff}

    def record_crowd(self):
        """Hourly Myfxbook snapshot (their API allows ~100 calls a day)."""
        if not self.fxb.configured or not self.due("crowd", 3600):
            return
        try:
            rows = fxb.record(self.fxb.outlook(), self.all_symbols)
            log(f"recorded crowd sentiment for {len(rows)} symbols")
        except Exception as e:
            log(f"Myfxbook unavailable: {e}")

    def cmd_size(self, args) -> str:
        usage = "Use: <code>/size XAUUSD 4105.5 4098</code> (symbol, entry, stop) or <code>/size XAUUSD 4098</code>"
        if len(args) < 2:
            return usage
        sym = args[0].upper()
        if sym not in self.specs:
            return f"I don't know {html.escape(sym)}. Try XAUUSD, EURUSD, GBPUSD, BTCUSD or ETHUSD."
        nums = [float(a) for a in args[1:]]
        risk_pct = self.cfg["risk"]["risk_per_trade_pct"]
        if len(nums) == 1:
            sl = nums[0]
            entry = self.market.price(sym, "buy") if self.market_ready() else None
            if entry is None:
                return "I can't get a price right now, so give the entry too: " + usage
        else:
            entry, sl = nums[0], nums[1]
            risk_pct = nums[2] if len(nums) > 2 else risk_pct
        balance = self.balance()
        spec = self.specs[sym]
        r = position_size(spec, balance, risk_pct, entry, sl)
        side = "BUY" if sl < entry else "SELL"
        lines = [f"<b>{side} {sym}</b>  entry {spec.fmt(entry)}  stop {spec.fmt(sl)} (distance {spec.fmt(r['distance'])})",
                 f"Balance ${balance:,.2f}{' (from config)' if self.cloud else ''}, risk {risk_pct:g}% = ${r['target_usd']:,.2f}"]
        if r["lots"] == 0:
            lines.append(f"❌ Even the smallest size ({spec.volume_min:g} lots) would risk "
                         f"${r['min_lot_risk_usd']:,.2f}, more than you allowed. Use a closer stop or skip it.")
        else:
            lines.append(f"➡️ <b>{r['lots']:g} lots</b> (you'd lose ${r['risk_usd']:,.2f} if the stop is hit)")
            sign = 1 if side == "BUY" else -1
            for k in (1, 2):
                lines.append(f"Target {k}x risk: {spec.fmt(entry + sign * k * r['distance'])}")
        return "\n".join(lines)

    def cmd_news(self, args=None) -> str:
        now = pd.Timestamp.now(tz="UTC")
        ev = relevant(fetch_events(), self.all_symbols, now, now + pd.Timedelta(hours=24),
                      tuple(self.bc["news_impacts"]))
        if not ev:
            return "📰 No big scheduled news for your markets in the next 24 hours."
        rows = [f"• <b>{e.time.tz_convert(self.tz):%a %H:%M}</b> {e.currency} {html.escape(e.title)}"
                + (f" (forecast {html.escape(e.forecast)})" if e.forecast else "") for e in ev]
        return "📰 <b>Big news, next 24 hours</b> (Malaysia time)\n" + "\n".join(rows) + \
               "\n\nAvoid opening intraday trades in the 15 minutes before these."

    def cmd_status(self, args=None) -> str:
        if not self.ensure_mt5():
            return ("I'm running in the cloud, so I can't see your MT5 account. The assistant on your PC "
                    "still warns you about your open trades while your PC is on.")
        from forexbot import mt5_client as m
        acc = self.mt5.account_info()
        start = self.state.get("day_start_equity") or acc.equity
        day_pct = (acc.equity - start) / start * 100 if start else 0
        lines = [f"💼 Balance ${acc.balance:,.2f} · equity ${acc.equity:,.2f}",
                 f"Today: {day_pct:+.2f}% (${acc.equity - start:+,.2f}) · limit -{self.cfg['risk']['daily_loss_limit_pct']:g}%"]
        positions = m.open_positions(self.mt5, list(self.specs))
        lines += [f"• {p.side.upper()} {p.symbol} {p.lots:g} @ {self.specs[p.symbol].fmt(p.open_price)} · "
                  f"SL {self.specs[p.symbol].fmt(p.sl) if p.sl else 'NONE ⚠️'} · ${p.profit:+,.2f}"
                  for p in positions] or ["No open trades."]
        return "\n".join(lines)

    # ---------- your MT5 trades (PC only) ----------
    def monitor(self):
        if not self.ensure_mt5():
            return
        from forexbot import mt5_client as m
        acc = self.mt5.account_info()
        now = pd.Timestamp.now(tz="UTC")
        day = str(trading_day(now))
        if self.state["day"] != day:
            self.state["day"], self.state["day_start_equity"] = day, acc.equity
        positions = [p for p in m.open_positions(self.mt5, list(self.specs)) if p.symbol in self.specs]
        init = self.state["initial_sl"]
        for p in positions:
            if p.sl and str(p.ticket) not in init:
                init[str(p.ticket)] = p.sl
        self.state["initial_sl"] = {t: v for t, v in init.items() if t in {str(p.ticket) for p in positions}}
        spikes = {s: m.candle_spike(self.mt5, self.names[s]) for s in {p.symbol for p in positions} if s in self.names}
        try:
            events = fetch_events()
        except Exception as e:
            log(f"news feed unavailable: {e}")
            events = []
        ctx = MonitorContext(
            now=now, specs=self.specs, events=events,
            initial_sl={int(k): v for k, v in self.state["initial_sl"].items()},
            equity=acc.equity, day_start_equity=self.state["day_start_equity"], spikes=spikes,
            news_minutes=self.bc["news_warning_minutes"], breakeven_at_r=self.bc["breakeven_at_r"],
            spike_multiple=self.bc["spike_multiple"], daily_warn_pct=self.bc["daily_warn_pct"],
            daily_limit_pct=self.cfg["risk"]["daily_loss_limit_pct"], local_tz=self.tz,
        )
        for a in check_positions(positions, ctx):
            self.send_once(a.key, a.text)

    # ---------- signals ----------
    def scan_signals(self):
        """Once a minute: for every feed, look at newly closed candles only."""
        if not self.sources or not self.due("scan", 60) or not self.market_ready():
            return
        now = pd.Timestamp.now(tz="UTC")
        events = []
        try:
            events = fetch_events()
        except Exception:
            pass
        for src in self.sources:
            params = {**self.cfg["strategies"][src.strategy], **src.params}
            tf = params["timeframe"]
            expected = last_close_time(tf, now)
            for sym in src.symbols:
                key = f"{src.name}:{sym}"
                seen = self.state["last_signal_bar"].get(key)
                if seen and pd.Timestamp(seen) >= expected:
                    continue                      # nothing new has closed yet
                if sym not in CRYPTO and not fx_market_open(now) and seen:
                    continue                      # forex/gold closed for the weekend
                try:
                    # Intraday strategies need ~2 weeks of candles for their daily-range filter.
                    bars = self.market.bars(sym, tf, 150 if tf == "D1" else params.get("lookback_bars", 2000))
                except FeedError as e:
                    log(f"prices unavailable for {sym} {tf}: {e}")
                    continue
                if len(bars) < 60:
                    continue
                last_close = bars.index[-1] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])
                after = pd.Timestamp(seen) if seen else bars.index[-1]   # first run: latest candle only
                fresh = [s for s in new_signals(src, bars, sym, params, self.specs[sym], after)
                         if tf == "D1" or now - s.created <= pd.Timedelta(minutes=max(TIMEFRAME_MINUTES[tf], 30))]
                groups: dict = {}
                for s in fresh:                       # linked buy/sell pairs go out as one message
                    groups.setdefault(s.oco_group or id(s), []).append(s)
                for sigs in groups.values():
                    for sig in sigs:
                        if src.horizon == "LONG-TERM":   # an opposite trend signal ends the old trade
                            for t in self.tracked:
                                alert = reversal_alert(t, sig, self.specs[sym])
                                if alert:
                                    self.send_once(*alert)
                    crowd = fxb.latest().get(sym)
                    crowd = fxb.describe(crowd) if crowd else None
                    args = (src, self.specs[sym], self.balance(), self.cfg["risk"]["risk_per_trade_pct"], events, self.tz, crowd)
                    text = format_pair(sigs, *args) if len(sigs) == 2 else format_signal(sigs[0], *args)
                    new = [track(s, src) for s in sigs]
                    ids = ",".join(t.id for t in new)
                    self.tg.send(text, buttons=copy_buttons(text)
                                 + [[("✅ Placed", f"placed:{ids}"), ("❌ Skipped", f"skip:{ids}")]])
                    self.tracked += new                   # only after Telegram has it
                    log(f"signal sent {ids} {src.name} {sym} {'/'.join(s.side for s in sigs)}")
                self.state["last_signal_bar"][key] = last_close.isoformat()

    def follow_signals(self):
        """Update open signals: intraday every 5 min, long-term hourly."""
        if not self.due("follow", 300) or not self.market_ready():
            return
        now = pd.Timestamp.now(tz="UTC")
        checked = self.state.setdefault("last_checked", {})
        for t in [t for t in self.tracked if t.active]:
            intraday = t.horizon == "INTRADAY"
            if time.time() - checked.get(t.id, 0) < (240 if intraday else 3300):
                continue
            created = pd.Timestamp(t.created)
            try:
                if intraday:
                    # 5-minute candles: finer than the 15-minute signal candles, so it's clearer
                    # whether the stop or the target came first.
                    n = int((now - created).total_seconds() // 300) + 3
                    bars = self.market.bars(t.symbol, "M5", min(max(n, 4), 1000))
                    bars = bars[bars.index >= created]
                    was_filled = t.fill_notified
                    alerts = follow_replay(t, bars, self.cost(t.symbol, t.entry), self.specs[t.symbol], now,
                                           doubt=self.market.doubt(t.symbol, t.entry))
                    if t.fill_notified and not was_filled:
                        alerts += cancel_sibling(t, self.tracked, self.specs[t.symbol], now)
                else:
                    n = int((now - created).total_seconds() // 3600) + 3
                    bars = self.market.bars(t.symbol, "H1", min(max(n, 4), 5000))
                    price = self.market.price(t.symbol, t.side)
                    if price is None:
                        continue
                    alerts = follow(t, bars[bars.index >= created], price, self.specs[t.symbol])
            except FeedError as e:
                log(f"follow-up skipped for {t.symbol}: {e}")
                continue
            checked[t.id] = time.time()
            for key, text in alerts:
                # Anything that may leave a live trade open gets a way to tell me it's over.
                live = t.active and key.split(":")[0] in ("filled", "maybe", "exit", "loss", "trail")
                self.send_once(key, text, copy_buttons(text)
                               + ([[("🏁 It's closed in MT5", f"closed:{t.id}")]] if live else []) or None)
        self.state["last_checked"] = {k: v for k, v in checked.items() if any(t.id == k for t in self.tracked)}

    def morning_brief(self):
        now = pd.Timestamp.now(tz=self.tz)
        if now.weekday() >= 5 or self.state.get("last_brief") == str(now.date()):
            return
        t = parse_hm(self.bc["morning_brief"])
        if (now.hour, now.minute) < (t.hour, t.minute):
            return
        self.state["last_brief"] = str(now.date())
        self.tg.send(f"☀️ <b>Good morning. {now:%A %d %b}</b>\n\n" + self.cmd_news()
                     + f"\n\nRules: risk {self.cfg['risk']['risk_per_trade_pct']:g}% per trade, always set "
                       f"a stop loss, stop for the day at -{self.cfg['risk']['daily_loss_limit_pct']:g}%.")
        log("sent morning brief")

    # ---------- main loop ----------
    def step(self, wait: int):
        if self.monitor_only:
            self.monitor()
            time.sleep(wait)
            return
        self.handle_updates(wait=wait)
        self.record_crowd()
        self.check_headlines()
        self.scan_signals()
        self.follow_signals()
        if not self.cloud:
            self.monitor()
        self.morning_brief()
        self.weekly_report()
        self.save_history()

    def run(self, minutes: float | None = None):
        mode = "cloud" if self.cloud else "PC (trade monitor only)" if self.monitor_only else "PC"
        log(f"assistant starting ({mode})")
        if not self.cloud:
            self.ensure_mt5()
        if not self.cloud and not self.monitor_only:
            try:
                self.tg.send("🤖 <b>Assistant is running.</b> Signals arrive automatically. Send /help for commands.")
            except TelegramError as e:
                log(f"start-up message not sent ({e}); carrying on")
        stop_at = time.time() + minutes * 60 if minutes else None
        while stop_at is None or time.time() < stop_at:
            wait = self.bc["check_seconds"]
            if stop_at:
                wait = int(max(1, min(wait, stop_at - time.time())))
            try:
                self.step(wait)
            except TelegramError as e:
                log(f"telegram error: {e}")
                time.sleep(10)
            except KeyboardInterrupt:
                raise
            except Exception:
                log("error:\n" + traceback.format_exc())
                time.sleep(10)
            finally:
                self._save_state()
        log("shift finished")


def single_instance(name: str):
    """Hold an exclusive lock while this copy runs (Windows). None if another copy has it."""
    if sys.platform != "win32":
        return True
    import msvcrt
    f = open(DATA_DIR / f"{name}.lock", "w")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cloud", action="store_true", help="use free web APIs instead of MT5")
    ap.add_argument("--monitor-only", action="store_true", help="only watch your MT5 trades")
    ap.add_argument("--minutes", type=float, help="stop after this many minutes")
    args = ap.parse_args()
    lock = single_instance("monitor" if args.monitor_only else "bot")
    if lock is None:
        log("Another copy of the bot is already running, so this one is closing.")
        return 0
    try:
        Bot(load_config(), cloud=args.cloud, monitor_only=args.monitor_only).run(args.minutes)
    except KeyboardInterrupt:
        log("stopped")
    except TelegramError as e:
        log(f"Problem: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
