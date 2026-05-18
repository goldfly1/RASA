# RASA Prerequisites Check & Bootstrap - Windows
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
param(
    [switch]$SkipRedis,
    [switch]$SkipGo,
    [switch]$SkipNats,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RasaRoot   = Split-Path -Parent $PSScriptRoot
$RasaRoot   = (Resolve-Path $RasaRoot).Path
$BinDir     = Join-Path $RasaRoot "bin"
$VenvDir    = Join-Path $RasaRoot ".venv"
$NatsVersion = "v2.11.2"

function Check-Command {
    param([string]$cmd)
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    return ($c -ne $null)
}

Write-Host ""
Write-Host "=== RASA Windows Pilot Bootstrap ===" -ForegroundColor Cyan
Write-Host "Root: $RasaRoot" -ForegroundColor Gray

# --- Python -------------------------------------------------------------
Write-Host ""
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
$py = $null
$pyv = ""
if (Check-Command "python") {
    $tmp = python --version 2>&1
    if ($tmp -match "Python\s+3\.(\d+)") {
        $minor = [int]$matches[1]
        if ($minor -ge 12) {
            $py  = (Get-Command "python").Source
            $pyv = $tmp.Trim()
        }
    }
}
if (-not $py) {
    Write-Host "MISSING: Python 3.12+ not found" -ForegroundColor Red
    Write-Host "Install: winget install Python.Python.3.13" -ForegroundColor DarkCyan
    exit 1
}
Write-Host "FOUND : $py ($pyv)" -ForegroundColor Green

# --- pip ----------------------------------------------------------------
Write-Host ""
Write-Host "[2/6] Checking pip..." -ForegroundColor Yellow
if (Check-Command "pip") {
    Write-Host "FOUND : pip OK" -ForegroundColor Green
} else {
    Write-Host "MISSING: run  python -m ensurepip" -ForegroundColor Red
    exit 1
}

# --- Go -----------------------------------------------------------------
if (-not $SkipGo) {
    Write-Host ""
    Write-Host "[3/6] Checking Go..." -ForegroundColor Yellow
    if (Check-Command "go") {
        Write-Host "FOUND : $(go version)" -ForegroundColor Green
    } else {
        Write-Host "MISSING: Go not found" -ForegroundColor Red
        if (Check-Command "winget") {
            Write-Host "Installing Go..." -ForegroundColor DarkYellow
            winget install GoLang.Go --silent --accept-package-agreements
            Write-Host "INSTALLED: restart PowerShell to refresh PATH" -ForegroundColor Green
        } else {
            Write-Host "winget missing - install manually from https://go.dev/dl/" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host ""
    Write-Host "[3/6] Skipped Go" -ForegroundColor DarkGray
}

# --- Go Control Plane Build ---------------------------------------------
if (-not $SkipGo) {
    Write-Host ""
    Write-Host "[3.5/6] Building Go control plane..." -ForegroundColor Yellow
    $services = @("orchestrator", "pool-controller", "memory-controller", "recovery-controller", "eval-aggregator", "policy-engine")
    foreach ($svc in $services) {
        $out = Join-Path $RasaRoot "$svc.exe"
        go build -o $out "./cmd/$svc" | Out-Null
        Write-Host "  OK : $svc.exe" -ForegroundColor Green
    }
    Write-Host "DONE   : Go binaries built" -ForegroundColor Green
}

# --- NATS Server --------------------------------------------------------
if (-not $SkipNats) {
    Write-Host ""
    Write-Host "[4/6] Checking NATS..." -ForegroundColor Yellow
    $haveNats = (Check-Command "nats-server") -or (Test-Path (Join-Path $BinDir "nats-server.exe"))
    if ($haveNats) {
        Write-Host "FOUND" -ForegroundColor Green
    } else {
        Write-Host "MISSING: downloading $NatsVersion..." -ForegroundColor Red
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        $arch = "amd64"
        if (-not [Environment]::Is64BitOperatingSystem) { $arch = "386" }
        $url  = "https://github.com/nats-io/nats-server/releases/download/$NatsVersion/nats-server-$NatsVersion-windows-$arch.zip"
        $zip  = Join-Path $env:TEMP "nats.zip"
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $BinDir -Force
            Write-Host "INSTALLED: $BinDir\nats-server.exe" -ForegroundColor Green
            Write-Host "HINT    : Add $BinDir to PATH" -ForegroundColor DarkCyan
        } catch {
            Write-Host "FAILED: $_" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host ""
    Write-Host "[4/6] Skipped NATS" -ForegroundColor DarkGray
}

# --- Redis --------------------------------------------------------------
if (-not $SkipRedis) {
    Write-Host ""
    Write-Host "[5/6] Checking Redis..." -ForegroundColor Yellow
    if (Check-Command "redis-server") {
        Write-Host "FOUND" -ForegroundColor Green
    } else {
        Write-Host "MISSING" -ForegroundColor Red
        Write-Host "Install: winget install Redis.Redis" -ForegroundColor DarkCyan
        Write-Host "        or WSL: sudo apt install redis-server" -ForegroundColor DarkCyan
    }
} else {
    Write-Host ""
    Write-Host "[5/6] Skipped Redis" -ForegroundColor DarkGray
}

# --- Python venv --------------------------------------------------------
Write-Host ""
Write-Host "[6/6] Python venv..." -ForegroundColor Yellow
$pyVenvExe = Join-Path $VenvDir "Scripts\python.exe"
$existing  = Test-Path $pyVenvExe

if ($existing -and (-not $Force)) {
    Write-Host "FOUND : $VenvDir" -ForegroundColor Green
} else {
    if ($existing -and $Force) {
        Write-Host "RECREATE: removing old venv" -ForegroundColor DarkYellow
        Remove-Item -Recurse -Force $VenvDir
    }
    Write-Host "CREATE: $VenvDir" -ForegroundColor DarkYellow
    & $py -m venv "$VenvDir"
}

$vpip = Join-Path $VenvDir "Scripts\pip.exe"
if (Test-Path $vpip) {
    Write-Host "UPGRADE: pip" -ForegroundColor DarkYellow
    & $pyVenvExe -m pip install --upgrade pip | Out-Null
    Write-Host "INSTALL: rasa dependencies..." -ForegroundColor DarkYellow
    try {
        Push-Location $RasaRoot
        & $vpip install -e . | Out-Null
        Pop-Location
    } catch {
        Pop-Location
        Write-Host "WARN  : editable install failed" -ForegroundColor Yellow
        Write-Host "        run: .venv\Scripts\pip install -e ." -ForegroundColor DarkCyan
    }
    Write-Host "DONE   : Python env ready" -ForegroundColor Green
} else {
    Write-Host "CRITICAL: venv creation failed" -ForegroundColor Red
    exit 1
}

# --- Summary ------------------------------------------------------------
Write-Host ""
Write-Host "=== Bootstrap Complete ===" -ForegroundColor Cyan
Write-Host "Python: $py ($pyv)"
Write-Host "Venv  : $VenvDir"
if (Test-Path (Join-Path $RasaRoot ".git")) {
    Write-Host "Git   : yes"
}
Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. copy .env.example .env  (edit keys and passwords)"
Write-Host "  2. .\scripts\create_databases.ps1"
Write-Host "  3. .\scripts\bootstrap_schema.ps1"
Write-Host "  4. redis-server"
Write-Host "  5. nats-server -c config\nats-server.conf"
Write-Host "  6. .\scripts\build.ps1          (rebuild Go binaries if needed)"
Write-Host "  7. .venv\Scripts\activate"
Write-Host "  8. python -m rasa.llm_gateway"
Write-Host ""
