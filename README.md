# Forex Bot: gold & forex signal bot

A personal signal bot for **XAUUSD, EURUSD, GBPUSD and USDJPY** on MT5 (broker: Elev8).
It never places trades itself. Eventually it sends each setup to Telegram (entry, SL, TP,
order type, full reasoning) and watches open trades for risk alerts. You place the trades.

**Plan:** backtest → demo signals (4–8 weeks) → prop firm challenge.

## Status

| Stage | What | Status |
|---|---|---|
| 1 | Backtest engine, risk manager, reports | ✅ built and tested |
| 1 | Elev8 history 2019 → today (M5, M15, H1, H4, D1) | ✅ downloaded and time-checked |
| 1 | Strategy A: London open breakout | ❌ failed in-sample (best of 24 variants −0.02R) |
| 1 | Strategy B: trend + pullback (limit orders) | ❌ failed in-sample (best of 12 +0.04R, PF 1.08, no plateau) |
| 1 | Strategy C: liquidity sweep reversal | ❌ failed in-sample (best of 12 −0.06R) |
| 1 | Strategy D: daily trend following (trailing stop) | ⚠️ small real edge: +0.11R on 14 unseen years (2005–2018), but only ~1–2%/yr |
| 1 | 7 intraday scalping setups (NY open, London fake-out, gap retrace, M5/M15 pullback) | ❌ all ~0 before costs, −0.16R to −0.67R after |
| 1 | **Crypto daily trend (BTC, ETH, XRP)** | ✅ **validated**: +0.41R 2019–23, **+0.26R on unseen 2024–26**, every coin and year positive |
| 1 | Crypto daily trend on 5 more coins (SOL, BNB, LTC, DOGE, ADA) | ❌ +0.48R in 2018–23 but −0.13R on unseen 2024–26: not added. BTC/ETH/XRP result now treated as "promising", not "proven" |
| 1 | Crypto intraday (9 setups on BTC/ETH) | ❌ all negative after costs |
| 1 | US news reaction (8:30 New York data candle) | ❌ tiny real drift before costs (+0.09R best), negative with news-time spreads |
| 1 | 20-year daily history (Dukascopy 2004–2023) | ✅ 5 of 8 symbols, checked against Elev8 (NZD/CAD/CHF pending: server rate-limits) |
| 2 | Telegram bot connected (@bhujangifxbot) | ✅ token in .env, chat id found, test message sent |
| 2 | Trading assistant: trade monitor, news warnings, /size /news /status | ✅ running — double-click `Start Bot.bat` |
| 1 | Next | ⏳ see "What the results say" |
| 2 | Live signal scanner + Telegram messages | ⏳ only once a strategy passes |
| 2 | Trade monitor (volatility / news / breakeven alerts) | ⏳ |
| 3 | 24/7 hosting on a Windows VPS | ⏳ |
| 1 | **Nasdaq "session momentum" (NY open, MNQ futures)** | ✅ **promising with futures costs**: +0.16R 2019–23, +0.14R 2024–26 (PF 1.15). Break-even on Elev8's CFD spread |
| 2 | Nasdaq futures signals from the PC (`nasdaq_open`) | ✅ built 2026-10-25, paper-trade first. Elev8 NAS100 prices until IBKR is connected |

## Folder layout

```
Forex Bot/
├─ config.yaml              ← all settings: risk, costs, prop rules, strategy parameters
├─ scripts/
│  ├─ check_mt5.py          ← 1. check the bot can see your MT5 demo
│  ├─ download_data.py      ← 2. download price history from Elev8
│  └─ backtest.py           ← 3. test a strategy and open an HTML report
├─ src/forexbot/
│  ├─ engine.py             ← order fills, SL/TP, spread & slippage, risk manager
│  ├─ metrics.py            ← stats, Monte Carlo stress test, prop challenge simulation
│  ├─ report.py             ← HTML report
│  ├─ backtest.py           ← ties it together
│  ├─ mt5_client.py         ← read-only MT5 connection (never sends orders)
│  └─ strategies/           ← one file per strategy
├─ tests/                   ← proofs that the engine behaves correctly
├─ data/                    ← downloaded candles (not shared)
└─ reports/                 ← backtest reports
```

## How to run

Open a terminal in this folder, then:

```bash
.venv\Scripts\python scripts\check_mt5.py
```

```bash
.venv\Scripts\python scripts\download_data.py
```

```bash
.venv\Scripts\python scripts\backtest.py london_breakout
```

Try a parameter change without editing the config:

```bash
.venv\Scripts\python scripts\backtest.py london_breakout --set tp2_r=3 sl_mode=opposite
```

Run the tests:

```bash
.venv\Scripts\python -m pytest
```

## Running 24/7 in the cloud (GitHub Actions)

`.github/workflows/bot.yml` runs `scripts/run_bot.py --cloud --minutes 13` every 15 minutes on
GitHub's servers, with prices from Coinbase (crypto) and Twelve Data (forex/gold), and saves the
bot's memory (`state/`) back to the repo after each shift. Secrets go in the repo's
**Settings → Secrets and variables → Actions**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TWELVEDATA_API_KEY`, `MYFXBOOK_EMAIL`, `MYFXBOOK_PASSWORD`. Once it's live, the PC runs only the
MT5 trade monitor: `Start Bot.bat` → `run_bot.py --monitor-only`. Never run the full bot on the
PC and in the cloud at the same time (both would read your Telegram commands).

## Nasdaq futures feed (runs on the PC)

Every weekday at **09:35 New York** (21:35 Malaysia time in US summer time, 22:35 in winter) the PC bot
looks at the first 5-minute Nasdaq candle. Closed above its 12 EMA → BUY, below → SELL, one
trade a day on **MNQ** (Micro Nasdaq futures at Interactive Brokers). Stop 1× the 5-minute ATR,
trailing stop 3× ATR, anything still open is closed at **12:00 New York** (midnight / 1am Malaysia).
Strategy: `src/forexbot/strategies/session_momentum.py`; messages and follow-ups: `src/forexbot/futures.py`.

Why futures: the raw edge is about +0.2R per trade before costs. Elev8's NAS100 spread (~3 pts)
eats almost all of it; MNQ costs ~1.2 pts all-in (1-tick spread + slippage + $0.62 per side).

Why the PC: it needs exact prices and has to reach you within seconds. Entering 5 minutes late
halved the edge in the 2019–23 test, so the bot skips a signal more than 5 minutes old.
`Start Bot.bat` (`--monitor-only`) runs this feed alongside the MT5 trade monitor, so **the PC
must be on and MT5 open around the New York open**. The cloud copy skips feeds marked `runs_on: pc`.

Prices (`futures_prices` in config.yaml):
- `mt5` (now): Elev8's NAS100 CFD. It moves point for point with the futures but sits at a different
  level, so messages give the stop and trailing stop as **distances in points from your fill**.
- `ibkr` (once your account is approved): the real MNQ from IB Gateway/TWS on this PC, read-only.
  Run `scripts\check_ibkr.py` first; if IBKR is down the bot warns you and falls back to `mt5`.

Results of these signals go to `data/pc_signal_history.csv` (not yet in the weekly report card).

## Telegram assistant

Double-click **`Start Bot.bat`** (MT5 must be open and logged in). Keep the window open; close it
to stop. The bot reads MT5 only — it never places, changes or closes trades.

**Signals** come from the feeds under `signals:` in `config.yaml`:

| Feed | Markets | Type | Confidence |
|---|---|---|---|
| crypto_trend | BTCUSD, ETHUSD, XRPUSD | 📅 LONG-TERM (days–weeks) | Promising: passed the unseen 2024–26 test (5 other coins failed it) |
| fx_gold_trend | XAUUSD, EURUSD, GBPUSD | 📅 LONG-TERM | Experimental: small edge only |
| (none yet) | | ⚡ INTRADAY | Every intraday setup tested so far loses after costs |

Daily signals arrive just after the daily close (about 5am Malaysia time, 6am in the
northern winter). Tap ✅ Placed / ❌ Skipped. Then the bot follows each signal and pings you
to **move your stop**, when the **stop is reached**, if it's **losing badly** (75% of the
risk), or if the **trend reverses** (opposite signal on the same market).

| In Telegram | What you get |
|---|---|
| `/signals` | Signals being followed right now, with current result |
| `/crowd` | % of retail traders buying vs selling (Myfxbook, recorded hourly to `data/sentiment.csv`) |
| `/size XAUUSD 4105.5 4098` | Lot size for that entry and stop at your normal risk, plus 1x/2x targets |
| `/size XAUUSD 4098` | Same, using the current price as the entry |
| `/news` | Big news for gold/EUR/GBP in the next 24 hours, Malaysia time |
| `/status` | Balance, today's result vs your limit, open trades |
| automatic | No-stop-loss warning, move-stop-to-breakeven, big news within 30 min, price spikes, Friday weekend warning, −3% warning and −4% stop, 8am morning brief |

Also: `/report` (report card, sent automatically Sundays 8pm), `/headlines` (latest news about
your markets; big ones are pushed automatically, max 4/hour, quiet 00:30–07:00). The bot starts
by itself when you log in to Windows (shortcut in your Startup folder; remove it to stop that),
opens MT5 if needed, refuses to run twice, and switches Telegram server if your provider loses
its route to one.

Settings are under `bot:` in `config.yaml`. Secrets live in `.env` (never share it).
News comes from the free Forex Factory weekly feed (cached for an hour).

## How the backtest stays honest

- **No peeking at the future.** Signals only use candles that had already closed.
- **Real costs.** Buys fill at the ask and sells at the bid; spread, slippage and commission are
  charged on every trade.
- **Pessimistic when unsure.** If SL and TP are both inside one candle, it counts as a loss.
  On random data with zero costs this bias measured only −0.03R, so it is small.
- **Held-back data.** Strategies are built on 2019–2023 only. 2024 onwards is tested **once**
  at the end with `--oos`. Changing the strategy after seeing those results makes the test unfair.
- **Same code live and in testing.** The live signal scanner will call the exact same
  `generate()` function that was backtested.
- **Sanity check.** On random-walk prices (no edge possible) the engine correctly shows a loss
  roughly equal to trading costs. A profit there would have meant a bug.

## What the results say (2026-10-08)

On Elev8 data 2019–2023, all three classic intraday setups are roughly **break-even before
costs** and clearly negative after spread and slippage. On short timeframes the costs are a big
fraction of each stop (e.g. ~0.15R per EURUSD trade with a 12-pip stop). Wider stops on higher
timeframes shrink that cost, and that's the direction the next candidates should go.

Daily trend following (Strategy D) is the only one with a positive result: +0.23R on gold,
EURUSD, GBPUSD and USDJPY. It turned negative once AUDUSD, NZDUSD, USDCAD and USDCHF were added,
and a daily system only produces ~15–40 trades a year, so 5 years of data can't separate a real
edge from luck. It needs a much longer history (15–20 years) to judge.

About 70 variants have been tested so far. The more you test, the more likely one looks good by
chance, so any future "winner" needs a higher bar and the untouched 2024+ data.

The out-of-sample period (2024 onwards) has **not** been looked at.

## Pass bar for moving to demo signals

Set in `config.yaml` → `verdict`: at least 150 trades, profit factor ≥ 1.3, expectancy ≥ +0.10R,
max drawdown ≤ 15%, and the same must hold on the held-back out-of-sample data.

## Risk settings (config.yaml)

- 1% risk per trade on a $10,000 balance
- Max 3 open trades, 1 per symbol
- New trades blocked if the worst case (today's losses + all open risk + the new trade) could
  exceed 4% for the day. The prop firm limit is 5%, so this keeps a buffer.

*Backtests describe the past; they do not guarantee future results.*
