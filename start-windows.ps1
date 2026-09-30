<#
.SYNOPSIS
  NOVA — Windows startup script. Installs and runs the frontend (Bun + Vite)
  and the backend (Python + FastAPI + LangGraph + Hugging Face).

.EXAMPLE
  .\start-windows.ps1                 # install if needed, then run BOTH services
  .\start-windows.ps1 -InstallOnly    # install dependencies only, run nothing
  .\start-windows.ps1 -BackendOnly    # backend only
  .\start-windows.ps1 -FrontendOnly   # frontend only
  .\start-windows.ps1 -BackendPort 8100
#>
[CmdletBinding()]
param(
  [switch]$InstallOnly,
  [switch]$BackendOnly,
  [switch]$FrontendOnly,
  [int]$FrontendPort = 8443,
  [int]$BackendPort = 8000
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$VenvDir = Join-Path $BackendDir '.venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $BackendDir 'requirements.txt'

function Write-Step([string]$Text) { Write-Host "» $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "  ✓ $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  ! $Text" -ForegroundColor Yellow }

function Test-Command([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "  NOVA — Personal Computer AI Agent" -ForegroundColor Magenta
Write-Host "  ──────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# ── 1. Frontend prerequisites ────────────────────────────────────────────────
Write-Step "Frontend (Bun + React + Vite)"
if (-not (Test-Command 'bun')) {
  Write-Warn 'bun was not found on PATH. Install it: powershell -c "irm bun.sh/install.ps1 | iex"'
  exit 1
}
if (-not (Test-Path (Join-Path $Root 'node_modules'))) {
  Push-Location $Root
  bun install
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw "bun install failed" }
  Pop-Location
  Write-Ok "node_modules installed"
} else {
  Write-Ok "node_modules present"
}

# ── 2. Backend prerequisites ─────────────────────────────────────────────────
Write-Step "Backend (Python + LangGraph + Hugging Face)"
$HasUv = Test-Command 'uv'

if (-not (Test-Path $VenvPy)) {
  if ($HasUv) {
    Write-Ok "creating virtualenv with uv (python 3.13)"
    & uv venv $VenvDir --python 3.13
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
  } else {
    $launcher = (Get-Command 'py' -ErrorAction SilentlyContinue).Source
    if (-not $launcher) { throw "Neither 'uv' nor the 'py' launcher was found. Install uv: https://docs.astral.sh/uv/" }
    Write-Ok "creating virtualenv with the py launcher"
    & $launcher -3.13 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
  }
} else {
  Write-Ok "virtualenv present"
}

$needsInstall = $true
if (Test-Path $VenvPy) {
  & $VenvPy -c "import langgraph" 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $needsInstall = $false }
}
if ($needsInstall) {
  Write-Ok "installing backend dependencies (first run takes a minute)"
  if ($HasUv) {
    & uv pip install --python $VenvPy -r $Requirements
  } else {
    & $VenvPy -m pip install --upgrade pip | Out-Null
    & $VenvPy -m pip install -r $Requirements
  }
  if ($LASTEXITCODE -ne 0) { throw "backend dependency installation failed" }
  Write-Ok "backend dependencies installed"
} else {
  Write-Ok "backend dependencies present"
}

if ($InstallOnly) {
  Write-Host ""
  Write-Ok "Install-only run finished. Start NOVA with: .\start-windows.ps1"
  Write-Host ""
  exit 0
}

# ── 3. Environment ───────────────────────────────────────────────────────────
if (-not $env:VITE_API_URL) { $env:VITE_API_URL = "http://127.0.0.1:$BackendPort" }

# Seed the config files from their documented templates on first run.
foreach ($pair in @(@('.env.example', '.env'), @('backend\.env.example', 'backend\.env'))) {
  $src = Join-Path $Root $pair[0]
  $dst = Join-Path $Root $pair[1]
  if ((Test-Path $src) -and -not (Test-Path $dst)) {
    Copy-Item $src $dst
    Write-Ok "created $($pair[1]) from $($pair[0])"
  }
}

if (Test-Path (Join-Path $Root '.env')) {
  Get-Content (Join-Path $Root '.env') | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
      if (-not (Get-Item "env:$($Matches[1])" -ErrorAction SilentlyContinue)) {
        Set-Item "env:$($Matches[1])" $Matches[2].Trim('"').Trim("'")
      }
    }
  }
}

# ── 4. Start the backend ─────────────────────────────────────────────────────
if (-not $FrontendOnly) {
  Write-Step "Starting backend on http://127.0.0.1:$BackendPort"
  $RunPs1 = Join-Path $BackendDir 'run.ps1'
  if (Test-Path $RunPs1) {
    Start-Process powershell -WorkingDirectory $Root -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $RunPs1, '-Port', "$BackendPort"
    ) | Out-Null
  } else {
    Start-Process $VenvPy -WorkingDirectory $BackendDir -ArgumentList @(
      '-m', 'uvicorn', 'nova_backend.main:app',
      '--host', '127.0.0.1', '--port', "$BackendPort"
    ) | Out-Null
  }
  Start-Sleep -Seconds 3
  Write-Ok "backend launched (new window) → http://127.0.0.1:$BackendPort/api/health"
}

if ($BackendOnly) {
  Write-Host ""
  Write-Ok "Backend-only run finished."
  Write-Host ""
  exit 0
}

# ── 5. Start the frontend (blocks this window) ───────────────────────────────
Write-Step "Starting frontend on http://localhost:$FrontendPort"
$env:PORT = "$FrontendPort"
Push-Location $Root
try { bun run dev } finally { Pop-Location }
