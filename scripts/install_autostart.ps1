# Starts the Forex Bot automatically (minimised) whenever you log in to Windows.
# Undo: delete "Forex Bot.lnk" from your Startup folder (Win+R, type shell:startup).
$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $startup "Forex Bot.lnk"))
$lnk.TargetPath = Join-Path $root "Start Bot.bat"
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7          # minimised
$lnk.Description = "Forex Bot Telegram assistant"
$lnk.Save()
Write-Output "Autostart installed: $(Join-Path $startup 'Forex Bot.lnk')"
