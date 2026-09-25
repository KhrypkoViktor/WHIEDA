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
    # Partner platform chain needs lead_actors and referral_profiles from above.
    "platform_partner_subscriptions_v1.sql",
    "platform_partner_subscription_currency_v2.sql",
    "platform_referral_bonuses_v1.sql",
    "platform_referral_bonus_redemptions_v2.sql",
    "platform_referral_admin_intents_v3.sql",
    "platform_partner_site_requests_v4.sql",
    "platform_partner_renewal_requests_v5.sql",
    "platform_partner_subscription_reminders_v6.sql",
    "platform_partner_products_v7.sql",
    "platform_support_tickets_v8.sql",
    "platform_support_forum_v9.sql",
    "platform_telegram_consent_v1.sql",
    "platform_partner_site_request_plans_v10.sql",
    "platform_lead_actor_channels_v11.sql",
    "platform_partner_site_request_contacts_v12.sql",
    "platform_renewal_services_v13.sql",
    "platform_crm_v14.sql",
    "platform_academy_v1.sql",
    "platform_academy_shelf_v15.sql",
    "platform_partner_library_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_advisor_structured_base_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql",
    "platform_bot_binding_context_v1.sql",
    "platform_telegram_durable_inbox_v1.sql",
    "platform_tenant_advisor_data_plane_v1.sql",
    "platform_tenant_release_package_v1.sql",
    "platform_tenant_release_price_plane_v1.sql",
    "platform_telegram_durable_outbox_v1.sql"
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
