# Installs / updates the PermitAgent pyRevit extension on this computer from the OneDrive source.
# Safe to run any time: copies only when files differ, and reports what changed.
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'PermitAgent.extension'
$extRoot = Join-Path $env:APPDATA 'pyRevit\Extensions'
$dst = Join-Path $extRoot 'PermitAgent.extension'

"computer: $env:COMPUTERNAME"
if (-not (Test-Path (Join-Path $env:APPDATA 'pyRevit'))) { "WARNING: pyRevit folder not found in APPDATA - is pyRevit installed?" }
if (-not (Test-Path $src)) { throw "source not found: $src" }
New-Item -ItemType Directory -Force $extRoot | Out-Null

$changed = @()
Get-ChildItem -Recurse -File $src | Where-Object { $_.Extension -ne '.pyc' } | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length + 1)
    $target = Join-Path $dst $rel
    $same = (Test-Path $target) -and ((Get-FileHash $_.FullName).Hash -eq (Get-FileHash $target).Hash)
    if (-not $same) {
        New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
        Copy-Item -Force $_.FullName $target
        $changed += $rel
    }
}
# files that no longer exist in the source (e.g. a renamed button) and stale compiled files are removed
Get-ChildItem -Recurse -File $dst | ForEach-Object {
    $rel = $_.FullName.Substring($dst.Length + 1)
    if ($_.Extension -eq '.pyc' -or -not (Test-Path (Join-Path $src $rel))) {
        Remove-Item -Force -Confirm:$false $_.FullName
        if ($_.Extension -ne '.pyc') { $changed += "(removed) $rel" }
    }
}
Get-ChildItem -Recurse -Directory $dst | Sort-Object { $_.FullName.Length } -Descending |
    Where-Object { -not (Get-ChildItem -Force $_.FullName) } | Remove-Item -Force -Confirm:$false

if ($changed.Count -eq 0) { "UP TO DATE - no files changed (no Revit restart needed)" }
else {
    "UPDATED $($changed.Count) file(s):"
    $changed | ForEach-Object { "  $_" }
    "RESTART REVIT so the new code is loaded"
}
$rv = Get-Process Revit -ErrorAction SilentlyContinue
if ($rv) { "Revit is running: " + ($rv.MainWindowTitle -join ' | ') } else { "Revit is not running" }
