# NOVA backend — Windows startup script.
#
#   .\run.ps1            # start on NOVA_HOST/NOVA_PORT (default 127.0.0.1:8000)
#   .\run.ps1 -Port 9000 # start on a custom port
#
# Creates .venv with uv (CPython 3.13) on first run, installs
# requirements.txt when `langgraph` is not importable, then serves
# nova_backend.main:app with the virtualenv interpreter.

[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Requirements = Join-Path $Root "requirements.txt"

if ($RecreateVenv -and (Test-Path $Venv)) {
    Write-Host "[NOVA] removing existing venv..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force $Venv
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "[NOVA] creating venv with uv (python 3.13)..." -ForegroundColor Cyan
    & uv venv $Venv --python 3.13
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}

$needsInstall = $true
& $VenvPython -c "import langgraph" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $needsInstall = $false }

if ($needsInstall) {
    Write-Host "[NOVA] installing requirements..." -ForegroundColor Cyan
    & uv pip install --python $VenvPython -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }
}

if ($Port -le 0) {
    if ($env:NOVA_PORT) { $Port = [int]$env:NOVA_PORT } else { $Port = 8000 }
}
$BindHost = if ($env:NOVA_HOST -and $env:NOVA_HOST.Trim()) { $env:NOVA_HOST.Trim() } else { "127.0.0.1" }

# uvicorn resolves `nova_backend` from the backend folder regardless of cwd.
$env:PYTHONPATH = $Root
Push-Location $Root
try {
    Write-Host "[NOVA] serving on http://${BindHost}:${Port}" -ForegroundColor Green
    & $VenvPython -m uvicorn nova_backend.main:app --host $BindHost --port $Port
}
finally {
    Pop-Location
}
