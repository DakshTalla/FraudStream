# FraudStream one-command start (Windows PowerShell 5.1+).
#
#   .\start.ps1                  # set up everything and open the dashboard
#   .\start.ps1 -NoDocker        # skip Redpanda, use the in-process queue
#   .\start.ps1 -Tps 60          # simulate 60 transactions per second
#
# First run: creates the Python venv, installs packages, trains the model and
# installs the frontend packages (a few minutes). Later runs start in seconds.
param(
    [switch]$NoDocker,
    [double]$Tps = 20,
    [int]$ApiPort = 8000,
    [int]$WebPort = 3100
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"   # force utf-8 for all Python file I/O on Windows

function Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }

function Assert-Ok($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

# --- 1. Python environment ---------------------------------------------------
if (-not (Test-Path $python)) {
    Step "Creating Python virtual environment"
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv (Join-Path $root ".venv") }
    else { python -m venv (Join-Path $root ".venv") }
    Assert-Ok "venv creation"
    Step "Installing Python packages"
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r (Join-Path $root "backend\requirements.txt")
    Assert-Ok "pip install"
}

# --- 2. Model ----------------------------------------------------------------
if (-not (Test-Path (Join-Path $root "backend\models\model.json"))) {
    Step "Training the fraud model on simulated history (about 30 seconds)"
    Push-Location (Join-Path $root "backend")
    & $python -m fraudstream.train
    Pop-Location
    Assert-Ok "training"
}

# --- 3. Frontend packages ----------------------------------------------------
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Step "Installing frontend packages"
    Push-Location (Join-Path $root "frontend")
    npm install --no-fund --no-audit
    Pop-Location
    Assert-Ok "npm install"
}

# --- 4. Broker: Redpanda if Docker is running, else in-process queue -----------
$broker = "memory"
if (-not $NoDocker -and (Get-Command docker -ErrorAction SilentlyContinue)) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Step "Starting Redpanda (Kafka-compatible broker) in Docker"
        docker compose -f (Join-Path $root "docker-compose.yml") up -d
        if ($LASTEXITCODE -eq 0) { $broker = "auto" }
    }
}
if ($broker -eq "memory") { Write-Host "Using the in-process queue (no Docker broker)." }

# --- 5. Start backend and frontend, each in its own window ----------------------
Step "Starting backend on port $ApiPort and dashboard on port $WebPort"
$backendCommand = "`$env:PYTHONUTF8='1'; `$env:FRAUDSTREAM_BROKER='$broker'; `$env:FRAUDSTREAM_TPS='$Tps'; " +
    "Set-Location '$root\backend'; & '$python' -m uvicorn fraudstream.api:app --port $ApiPort"
$frontendCommand = "`$env:NEXT_PUBLIC_API_URL='http://localhost:$ApiPort'; " +
    "Set-Location '$root\frontend'; npm run dev -- --port $WebPort"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand

# --- 6. Wait until the API answers, then open the dashboard --------------------
Write-Host "Waiting for the API (it replays history to warm up its state)..."
$ready = $false
foreach ($attempt in 1..60) {
    try {
        Invoke-RestMethod "http://localhost:$ApiPort/api/health" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw "The API did not start. Check the backend window for the error." }

Start-Process "http://localhost:$WebPort"
Write-Host "`nFraudStream is running: http://localhost:$WebPort" -ForegroundColor Green
Write-Host "To stop: close the two new PowerShell windows. To stop Redpanda: docker compose down"
