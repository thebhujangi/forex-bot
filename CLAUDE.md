# Forex Bot: notes for Claude

Personal signal bot (not auto-trading): reads Elev8 MT5 prices, sends signals and follow-ups to
Telegram (@bhujangifxbot). The owner is a beginner: explain trading terms plainly.

## Where it runs (since 2026-10-27)
- **Host: the Lenovo laptop** (`ranfateh-lenovo`, Tailscale 100.69.48.93, user `joesh`), folder
  `C:\Bots\Forex Bot`, started by `Start Bot.bat` at logon (Task Scheduler task "Forex Bot").
  Full mode, Elev8's own MT5 prices, checks every 2 s (`bot.live_check_seconds`).
- Live memory: `state-live\` on the Lenovo (git-ignored). `state\` in git is the old cloud copy.
- **Only one copy may run anywhere**, or every message arrives twice. The GitHub Actions workflow
  ("Forex Bot (cloud)") is disabled while the Lenovo hosts it; re-enable it only as a fallback
  after stopping the Lenovo copy.
- The development copy is on the main PC: `Desktop\Hustle Documents\Trading Bots\Forex Bot`.

## Changing the bot
1. Edit + test on the main PC (`.venv\Scripts\python -m pytest -q`), commit, push to
   github.com/thebhujangi/forex-bot (public repo: never commit `.env`, `data/`, `Research/`).
2. On the Lenovo run `Update Bot.bat` (git pull + reinstall + restart; memory and keys untouched).

## Rules learned the hard way
- Only strategies that pass BOTH 2019-23 and unseen 2024-26 tests on Elev8 data go live
  (`scripts/research.py`). Today that's only `crypto_trend` (BTC/ETH/XRP, ~1 signal/month,
  ~0.3%/month at 1% risk). Don't promise returns; the report shows actual vs the 2% target.
- Read `config.yaml` with `encoding="utf-8"`. Timestamps in history mix formats: parse with
  `format="ISO8601"`. Telegram messages max 4096 chars.
- Binance/Bybit/BitMEX are blocked in Malaysia: use Deribit/OKX for crypto futures data.
