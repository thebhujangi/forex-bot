@echo off
rem Pull the latest code from GitHub and restart the bot (Start Bot.bat restarts it within 30 s).
rem The bot's memory (state-live\) and private keys (.env) are never touched.
cd /d "%~dp0"
git pull --ff-only || (echo Update failed: see above. The bot was NOT restarted. & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -q -e . || (echo Package install failed. & pause & exit /b 1)
rem Stop THIS folder's bot only: the .venv launcher plus the Python process it started.
powershell -NoProfile -Command "$all = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"; $mine = @($all | Where-Object { $_.CommandLine -like '*run_bot.py*' -and $_.ExecutablePath -like '%~dp0*' }); $ids = $mine.ProcessId; $kids = @($all | Where-Object { $ids -contains $_.ParentProcessId }); ($kids + $mine) | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; 'Stopped process ' + $_.ProcessId }"
echo Updated. The bot restarts within 30 seconds.
