param(
    [switch]$FullPrecision
)

$ErrorActionPreference = "Stop"
$runtime = "C:\code\ailive\model\V2ProPlus\runtime\GPT-SoVITS-V2ProPlus-Windows-main"
$python = "C:\code\ailive\model\V2ProPlus\venv\Scripts\python.exe"
$script = Join-Path $runtime "run_api_custom.py"
$pidFile = Join-Path $PSScriptRoot "..\data\tts.pid"

if (-not (Test-Path $python)) { throw "TTS Python not found: $python" }
if (-not (Test-Path $script)) { throw "TTS script not found: $script" }

$env:MPLCONFIGDIR = "C:\code\ailive\model\V2ProPlus\mpl-cache"
$env:PYTHONIOENCODING = "utf-8"
$env:TTS_HALF_PRECISION = "0"

Write-Host "Starting GPT-SoVITS TTS on http://127.0.0.1:9880"
$proc = Start-Process `
    -FilePath $python `
    -ArgumentList $script `
    -WorkingDirectory $runtime `
    -PassThru `
    -NoNewWindow
New-Item -ItemType Directory -Force (Split-Path $pidFile) | Out-Null
Set-Content -Path $pidFile -Value $proc.Id -Encoding ascii
Wait-Process -Id $proc.Id
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
