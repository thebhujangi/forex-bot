@echo off
rem The signal bot itself runs 24/7 in the cloud (GitHub Actions).
rem This PC copy only watches your MT5 trades (no stop loss, breakeven, news, daily loss)
rem and sends warnings. Keep this window open; close it to stop. MT5 opens automatically.
rem To run the FULL bot on this PC instead, first disable the cloud workflow on GitHub,
rem then remove --monitor-only below.
cd /d "%~dp0"
title Forex Bot - MT5 trade monitor
.venv\Scripts\python.exe scripts\run_bot.py --monitor-only
pause
