# full_pipeline.ps1 — Full data reset + backfill + features + train + forecast
# Run from repo root: .\scripts\full_pipeline.ps1
#
# Steps:
#   1. Truncate all BQ tables
#   2. Backfill eco2mix 2024 (consolidated/definitive dataset)
#   3. Backfill eco2mix 2025+ (real-time dataset)
#   4. Compute features from 2024-01-01
#   5. Train model
#   6. Forecast

$ErrorActionPreference = "Stop"

function Step($n, $msg) {
    Write-Host ""
    Write-Host "=== Step ${n}: $msg ===" -ForegroundColor Cyan
}

# ── 1. Truncate ───────────────────────────────────────────────────────────────
Step 1 "Truncate all BQ tables"
python scripts/truncate_tables.py
if ($LASTEXITCODE -ne 0) { throw "Step 1 failed" }

# ── 2. Backfill 2024 (consolidated dataset) ───────────────────────────────────
Step 2 "Backfill eco2mix 2024-01-01 → 2024-12-31 (eco2mix-regional-cons-def)"
$env:BACKFILL_START_DATE = "2024-01-01"
$env:BACKFILL_END_DATE   = "2024-12-31"
$env:BACKFILL_DATASET    = "eco2mix-regional-cons-def"
python scripts/backfill.py
if ($LASTEXITCODE -ne 0) { throw "Step 2 failed" }

# ── 3. Backfill 2025+ (real-time dataset) ─────────────────────────────────────
Step 3 "Backfill eco2mix 2025-01-01 → today (eco2mix-regional-tr)"
$env:BACKFILL_START_DATE = "2025-01-01"
$env:BACKFILL_END_DATE   = (Get-Date -Format "yyyy-MM-dd")
$env:BACKFILL_DATASET    = "eco2mix-regional-tr"
python scripts/backfill.py
if ($LASTEXITCODE -ne 0) { throw "Step 3 failed" }

# ── 4. Compute features ───────────────────────────────────────────────────────
Step 4 "Compute features from 2024-01-01"
$env:JOB_MODULE     = "features"
$env:FEATURES_SINCE = "2024-01-01"
Remove-Item Env:BACKFILL_START_DATE -ErrorAction SilentlyContinue
Remove-Item Env:BACKFILL_END_DATE   -ErrorAction SilentlyContinue
Remove-Item Env:BACKFILL_DATASET    -ErrorAction SilentlyContinue
python -m elec_jobs
if ($LASTEXITCODE -ne 0) { throw "Step 4 failed" }

# ── 5. Train ──────────────────────────────────────────────────────────────────
Step 5 "Train model"
$env:JOB_MODULE          = "train"
$env:MLFLOW_TRACKING_URI = "file:./mlruns"
Remove-Item Env:FEATURES_SINCE -ErrorAction SilentlyContinue
python -m elec_jobs
if ($LASTEXITCODE -ne 0) { throw "Step 5 failed" }

# ── 6. Forecast ───────────────────────────────────────────────────────────────
Step 6 "Forecast"
$env:JOB_MODULE = "forecast"
Remove-Item Env:MLFLOW_TRACKING_URI -ErrorAction SilentlyContinue
python -m elec_jobs
if ($LASTEXITCODE -ne 0) { throw "Step 6 failed" }

Write-Host ""
Write-Host "=== Pipeline complete ===" -ForegroundColor Green
