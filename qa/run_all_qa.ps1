#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== WHIEDA Quality Lab (offline) ==="

python qa\run_whieda_regression.py --validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python qa\run_whieda_regression.py --summary
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python qa\run_whieda_regression.py --report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location backend\platform-api
python -m pytest tests\qa\ -q
$pytestExit = $LASTEXITCODE
Pop-Location

if ($pytestExit -ne 0) { exit $pytestExit }

Write-Host "=== WHIEDA Quality Lab: PASS ==="
