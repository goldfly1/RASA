# build.ps1 — Build all Go control-plane binaries
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
param(
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$RasaRoot = Split-Path -Parent $PSScriptRoot
$RasaRoot = (Resolve-Path $RasaRoot).Path

function Check-Command {
    param([string]$cmd)
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    return ($c -ne $null)
}

if (-not (Check-Command "go")) {
    Write-Host "ERROR: Go is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Install: winget install GoLang.Go" -ForegroundColor DarkCyan
    exit 1
}

$goVersion = (go version)
Write-Host "Found $goVersion" -ForegroundColor Green

$services = @(
    "orchestrator",
    "pool-controller",
    "memory-controller",
    "recovery-controller",
    "eval-aggregator",
    "policy-engine"
)

Push-Location $RasaRoot
foreach ($svc in $services) {
    $src = Join-Path "cmd" $svc
    $out = "$svc.exe"
    if ($Verbose) {
        Write-Host "Building $svc -> $out ..." -ForegroundColor Yellow
        go build -o $out "./$src"
    } else {
        go build -o $out "./$src" | Out-Null
    }
    Write-Host "OK     : $out" -ForegroundColor Green
}
Pop-Location

Write-Host ""
Write-Host "All Go binaries built." -ForegroundColor Cyan
