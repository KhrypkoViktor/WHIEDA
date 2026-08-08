#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "=== WHIEDA Advisor Acceptance Lab ==="

if (-not (Test-Path "qa\acceptance\acceptance_target.local.json")) {
  Copy-Item "qa\acceptance\acceptance_target.example.json" "qa\acceptance\acceptance_target.local.json"
}

python qa\acceptance\run_acceptance.py --offline --target qa\acceptance\acceptance_target.local.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python qa\acceptance\run_acceptance.py --run --dry-run --target qa\acceptance\acceptance_target.local.json --limit 5
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location backend\platform-api
python -m pytest tests\acceptance\ -q
$pytestExit = $LASTEXITCODE
Pop-Location

if ($pytestExit -ne 0) { exit $pytestExit }

Write-Host "=== ACCEPTANCE LAB: OFFLINE PASS ==="
Write-Host "Live E2E requires Core up: python qa\acceptance\run_acceptance.py --check-target --target qa\acceptance\acceptance_target.local.json"
