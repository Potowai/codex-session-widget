# Install the Codex Session Widget as a per-user startup shortcut on Windows 11.
#
#   ./install.ps1              widget only (transcript estimate, no network)
#
# Everything is per-user — nothing is installed system-wide, no admin needed.
# Copies the widget to %LOCALAPPDATA%\CodexSessionWidget and creates a shortcut
# in the Startup folder so it starts at login. Uses pythonw.exe so no console
# window appears.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:LOCALAPPDATA "CodexSessionWidget"
$exe  = Join-Path $dest "SessionWidget.py"
$log  = Join-Path $env:TEMP "codex-session-widget.log"

# --- prerequisites ----------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $pyExe = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $pyExe) { Write-Error "python/pythonw not found on PATH. Install Python 3.10+ first." }
} else {
    $pyExe = $python.Source
}
$pyw = $pyExe -replace "python\.exe$", "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $pyExe }  # fallback to console python

Write-Output "-> python: $pyExe"
Write-Output "-> pythonw: $pyw"

# --- 1. copy the widget + icon ----------------------------------------------
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Force (Join-Path $dir "SessionWidget.py") $exe
$iconSrc = Join-Path $dir "codex-icon.ico"
if (Test-Path $iconSrc) {
    Copy-Item -Force $iconSrc (Join-Path $dest "codex-icon.ico")
    Write-Output "OK widget + icon copied -> $dest"
} else {
    Write-Output "OK widget copied (no .ico - tray will draw a fallback icon) -> $dest"
}

# --- 2. startup shortcut ----------------------------------------------------
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
New-Item -ItemType Directory -Force -Path $startup | Out-Null
$shortcutPath = Join-Path $startup "Codex Session Widget.lnk"

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($shortcutPath)
$sc.TargetPath = $pyw
$sc.Arguments = "`"$exe`""
$sc.WorkingDirectory = $dest
$sc.WindowStyle = 7  # minimized (no console flash even with python.exe)
$sc.Description = "Codex Session Widget"
if (Test-Path $iconSrc) { $sc.IconLocation = "$dest\codex-icon.ico,0" }
else { $sc.IconLocation = "$pyw,0" }
$sc.Save()
Write-Output "OK startup shortcut -> $shortcutPath"

# --- 3. launch now ----------------------------------------------------------
Start-Process -FilePath $pyw -ArgumentList "`"$exe`"" -WorkingDirectory $dest -WindowStyle Hidden
Write-Output ""
Write-Output "Done. The widget appears top-right (drag to move, right-click for menu)."
Write-Output "Uninstall any time with ./uninstall.ps1"
