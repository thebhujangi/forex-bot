@echo off
rem Starts the Telegram trading assistant. Keep this window open; close it to stop the bot.
rem MetaTrader 5 must be open and logged in.
cd /d "%~dp0"
title Forex Bot - Telegram assistant
.venv\Scripts\python.exe scripts\run_bot.py
pause
