<#
.SYNOPSIS
    Start Many-Lives locally with durable state and room visuals.

.DESCRIPTION
    Most settings come from .env. STUB_STATE_FILE does NOT: it is read with
    os.getenv() in app/services/stubs.py, not through pydantic Settings, so
    putting it in .env has no effect. That matters, because without it the
    engine is in-memory and `resume` returns 404 after a restart -- which looks
    exactly like a bug but is correct behaviour for a non-durable engine.

    This script sets it as a real environment variable, which is why it exists.

.EXAMPLE
    .\scripts\run_local.ps1
    .\scripts\run_local.ps1 -Port 8001 -NoVisuals
#>
param(
    [int]$Port = 8000,
    [string]$StateFile = ".state/demo.json",
    [string]$VisualsDir = ".visuals",
    [switch]$NoVisuals,
    [switch]$FakeModels,
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot\..

if (-not (Test-Path ".env")) {
    Write-Error ".env is missing. Copy .env.example to .env and fill it in."
}

if ($Fresh) {
    Write-Host "Clearing previous state and images..." -ForegroundColor Yellow
    if (Test-Path $StateFile) { Remove-Item $StateFile -Force }
    if (Test-Path $VisualsDir) { Remove-Item $VisualsDir -Recurse -Force }
}

# Free the port if something is still listening on it.
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Port $Port was in use; stopping pid $($listener.OwningProcess)." -ForegroundColor Yellow
    Stop-Process -Id $listener.OwningProcess -Force
    Start-Sleep -Seconds 2
}

# The one setting that must be a real environment variable.
$env:STUB_STATE_FILE = $StateFile
$env:VISUALS_DIR = $VisualsDir
$env:DEBUG_ENDPOINTS = "true"

if ($NoVisuals)  { $env:ENABLE_ROOM_VISUALS = "false" }
else             { $env:ENABLE_ROOM_VISUALS = "true" }

if ($FakeModels) {
    # No provider calls at all: canned narration, no images billed.
    $env:USE_FAKE_MODELS = "true"
    $env:IMAGE_CLIENT = "fake"
}

New-Item -ItemType Directory -Force -Path (Split-Path $StateFile) | Out-Null

Write-Host ""
Write-Host "Many-Lives" -ForegroundColor Cyan
Write-Host "  url          http://127.0.0.1:$Port"
Write-Host "  state file   $StateFile   (durable; survives a restart)"
Write-Host "  images       $VisualsDir\<campaign_id>\<asset_id>.img"
Write-Host "  visuals      $($env:ENABLE_ROOM_VISUALS)"
Write-Host "  fake models  $(if ($FakeModels) { 'true (no provider calls)' } else { 'false (real OpenRouter calls cost money)' })"
Write-Host "  inspector    enabled (DEBUG_ENDPOINTS=true)"
Write-Host ""
Write-Host "  Bound to 127.0.0.1 only. Do not add --host 0.0.0.0: there is no" -ForegroundColor DarkGray
Write-Host "  authentication anywhere in this app." -ForegroundColor DarkGray
Write-Host ""

python -m uvicorn app.main:app --port $Port
