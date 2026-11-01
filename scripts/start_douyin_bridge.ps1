param(
    [string]$RoomId = "",
    [string]$DyhubUrl = "ws://127.0.0.1:8757/ws",
    [string]$OlvUrl = "ws://127.0.0.1:12393/live-ws"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bridge = Join-Path $Root "scripts\dyhub_bridge.py"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

$env:PYTHONIOENCODING = "utf-8"
Write-Host "dyhub: $DyhubUrl"
Write-Host "OLV:   $OlvUrl"
Write-Host "room:  $(if ($RoomId) { $RoomId } else { 'from URL' })"
Write-Host "Open http://127.0.0.1:12393/live-view.html for the browser stage."

& $Python $Bridge --dyhub-url $DyhubUrl --room-id $RoomId --olv-url $OlvUrl
