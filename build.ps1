# Build a PNG snapshot of the widget for the README (uses Pillow).
#   ./build.ps1                      collapsed snapshot
#   ./build.ps1 -Expanded            expanded snapshot
#   ./build.ps1 -Out docs/hero.png   custom output path
param(
    [string]$Out = "codex-session-widget.png",
    [switch]$Expanded
)
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $dir "SessionWidget.py"

$snapArgs = @($py, "--snapshot", $Out)
if ($Expanded) { $snapArgs += "--expanded" }
& python $snapArgs
if (-not (Test-Path $Out)) { Write-Error "snapshot failed" }
