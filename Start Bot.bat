@echo off
rem Forex Bot: the FULL bot (signals, follow-ups, MT5 trade monitor, news, reports) using
rem Elev8's own MT5 prices, checking every 2 seconds. Hosted 24/7 on the Lenovo.
rem Only ONE copy may run anywhere, or you get every message twice. The GitHub cloud copy is
rem switched off while this runs. If the bot stops it restarts itself after 30 seconds.
rem Close this window to stop it.
cd /d "%~dp0"
title Forex Bot (24/7)
set "FOREXBOT_STATE_DIR=%~dp0state-live"
:loop
.venv\Scripts\python.exe scripts\run_bot.py
if errorlevel 3 if not errorlevel 4 (
    echo Another copy is already running. This window will close.
    timeout /t 10 >nul
    exit /b
)
echo [%date% %time%] Bot stopped. Restarting in 30 seconds (close this window to stop for good)...
timeout /t 30 /nobreak >nul
goto loop
