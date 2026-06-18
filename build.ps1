# Build artifacts for the widget: PNG snapshot for the README + multi-size ICO
# for the system-tray icon. Uses Pillow (already installed).
#   ./build.ps1                      collapsed snapshot + tray icon
#   ./build.ps1 -Expanded            expanded snapshot + tray icon
#   ./build.ps1 -Out docs/hero.png   custom snapshot output path
param(
    [string]$Out = "codex-session-widget.png",
    [string]$Icon = "codex-icon.ico",
    [switch]$Expanded
)
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $dir "SessionWidget.py"

$snapArgs = @($py, "--snapshot", $Out)
if ($Expanded) { $snapArgs += "--expanded" }
& python $snapArgs
if (-not (Test-Path $Out)) { Write-Error "snapshot failed" }

& python $py --icon $Icon
if (-not (Test-Path $Icon)) { Write-Error "icon failed" }
