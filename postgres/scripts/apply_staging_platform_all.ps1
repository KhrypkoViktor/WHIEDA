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

# Order matters: tenancy + RLS helpers before tenant-scoped tables; binding after registry.
$Files = @(
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    # Existing staging must already contain lead_actors and referral_profiles.
    "platform_partner_subscriptions_v1.sql",
    "platform_partner_subscription_currency_v2.sql",
    "platform_referral_bonuses_v1.sql",
    "platform_referral_bonus_redemptions_v2.sql",
    "platform_referral_admin_intents_v3.sql",
    "platform_partner_library_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql"
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
    Write-Host "  -> staging_seed_whieda_journey_v1.sql"
    & psql -h $DbHost -p $Port -U $User -d $Db -f $Seed
    if ($LASTEXITCODE -ne 0) { throw "psql failed on staging seed" }
}

Write-Host "OK: staging platform SQL applied."
