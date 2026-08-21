# Apply all platform SQL on staging/local — full dependency order, NO prod
param(
    [string]$DbHost = "127.0.0.1",
    [int]$Port = 5432,
    [string]$Db = "whieda_platform",
    [string]$User = "postgres",
    [switch]$CreateDb
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$SqlDir = Join-Path $Root "postgres\sql"

# Order: registry/RLS helpers, existing leads schema + RLS, session/journey
# stack, WHIEDA telegram binding row, binding-context columns/backfill of
# WHIEDA rows, then durable Telegram inbox/outbox. Each file listed once.
# Do not apply production.
$Files = @(
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    "whieda_website_leads_p0_v1.sql",
    "wwc_leads_p01_runtime_migration.sql",
    "platform_tenant_rls_legacy_leads_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql",
    "platform_bot_binding_context_v1.sql",
    "platform_telegram_durable_inbox_v1.sql",
    "platform_tenant_advisor_data_plane_v1.sql",
    "platform_tenant_release_package_v1.sql"
)

if ($CreateDb) {
    Write-Host "Creating database $Db (if missing)..."
    & psql -h $DbHost -p $Port -U $User -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$Db'" | Out-Null
    $exists = & psql -h $DbHost -p $Port -U $User -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$Db'"
    if (-not ($exists -match "1")) {
        & psql -h $DbHost -p $Port -U $User -d postgres -c "CREATE DATABASE $Db"
    }
}

Write-Host "Applying $($Files.Count) platform SQL files to ${Db}@${DbHost}:${Port} ..."

foreach ($f in $Files) {
    $path = Join-Path $SqlDir $f
    if (-not (Test-Path $path)) {
        throw "Missing $path"
    }
    Write-Host "  -> $f"
    & psql -h $DbHost -p $Port -U $User -d $Db -f $path
    if ($LASTEXITCODE -ne 0) { throw "psql failed on $f" }
}

$Seed = Join-Path $Root "postgres\scripts\staging_seed_whieda_journey_v1.sql"
if (Test-Path $Seed) {
    Write-Host "  skip staging_seed_whieda_journey_v1.sql (stale vs onboarding schema; not in APPLY_ORDER)"
}

Write-Host "OK: staging platform SQL applied."
