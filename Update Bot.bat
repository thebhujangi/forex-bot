@echo off
rem Pull the latest code from GitHub and restart the bot (Start Bot.bat restarts it within 30 s).
rem The bot's memory (state-live\) and private keys (.env) are never touched.
cd /d "%~dp0"
git pull --ff-only || (echo Update failed: see above. The bot was NOT restarted. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -q -e . || (echo Package install failed. & pause & exit /b 1)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*run_bot.py*' -and $_.ExecutablePath -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'Restarting the bot (process ' + $_.ProcessId + ')' }"
echo Updated. The bot restarts within 30 seconds.
