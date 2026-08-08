#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== WHIEDA Data Quality Control Plane ==="

python qa\data_quality\run_data_quality.py --manifest qa\data_quality\fixtures\manifest.test.json --validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python qa\data_quality\run_data_quality.py --manifest qa\data_quality\fixtures\manifest.test.json --report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location backend\platform-api
python -m pytest tests\data_quality\ -q
$pytestExit = $LASTEXITCODE
Pop-Location

if ($pytestExit -ne 0) { exit $pytestExit }

Write-Host "=== DATA QUALITY: PASS ==="
