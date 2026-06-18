# Remove the Codex Session Widget. Per-user, no admin.
$ErrorActionPreference = "Stop"

# Kill running widget processes launched from our install location.
$exe = Join-Path $env:LOCALAPPDATA "CodexSessionWidget" "SessionWidget.py"
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*SessionWidget.py*" } |
    ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }

# Remove startup shortcut.
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$shortcutPath = Join-Path $startup "Codex Session Widget.lnk"
if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force }
Write-Output "OK removed startup shortcut"

# Remove installed widget.
$dest = Join-Path $env:LOCALAPPDATA "CodexSessionWidget"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Write-Output "OK removed $dest"

# Leave ~/.codex state + transcripts untouched.
Write-Output "Done. (Your ~/.codex sessions and Codex login are untouched.)"
