# Release Core (or staging) from a committed revision, with mandatory gates.
#
#   pwsh backend/deploy/core/release_core.ps1 -Target staging            # HEAD
#   pwsh backend/deploy/core/release_core.ps1 -Target core -Revision 6aaa96c
#
# Gates, in order — any failure stops before anything reaches the server:
#   1. the revision exists and (for HEAD) the tree is clean;
#   2. the focused unit suite (referral, subscriptions, telegram, schema map) passes;
#   3. the live-PostgreSQL integration tests pass on a throwaway local cluster
#      (scripts/run_postgres_integration_tests.ps1) — never skipped;
# then the archive is copied and release_core.sh runs the server-side schema gate
# and the swap. Production is never touched by hand any more.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('core', 'staging')][string]$Target,
    [string]$Revision = 'HEAD',
    [string]$SshKey = "$env:USERPROFILE\.ssh\wwc_deploy_ed25519",
    [string]$Host_ = 'root@185.252.232.93',
    [switch]$SkipUnitTests
)

$ErrorActionPreference = 'Stop'
$repoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
$apiRoot = Join-Path $repoRoot 'backend/platform-api'

$sha = (git -C $repoRoot rev-parse --short $Revision).Trim()
if (-not $sha) { throw "unknown revision $Revision" }
if ($Revision -eq 'HEAD') {
    $dirty = git -C $repoRoot status --porcelain -- backend/platform-api
    if ($dirty) { throw "backend/platform-api has uncommitted changes; commit or pass -Revision" }
}
Write-Host "== releasing $Target <- $sha"

Push-Location $apiRoot
try {
    if (-not $SkipUnitTests) {
        Write-Host '== gate: focused unit tests'
        & python -m pytest -q -p no:cacheprovider `
            tests/test_referral_bonus_service.py tests/test_referral_bonus_schema.py `
            tests/test_partner_subscriptions.py tests/test_telegram_processor.py `
            tests/test_telegram_referral_admin.py tests/test_lead_actor_telegram_link.py `
            tests/test_schema_requirements.py tests/test_identity_exchange_memory_best_effort.py
        if ($LASTEXITCODE -ne 0) { throw 'unit gate failed' }
    }
    Write-Host '== gate: PostgreSQL integration tests'
    & pwsh -NoProfile -File (Join-Path $apiRoot 'scripts/run_postgres_integration_tests.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL integration gate failed' }
} finally {
    Pop-Location
}

$archive = Join-Path ([IO.Path]::GetTempPath()) "platform-api-$sha.tar.gz"
git -C $repoRoot archive --format=tar.gz --output=$archive $sha backend/platform-api
if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }

$remoteRoot = if ($Target -eq 'core') { '/opt/whieda-platform-core' } else { '/opt/whieda-platform-staging' }
$ssh = @('-i', $SshKey, '-o', 'IdentitiesOnly=yes')
Write-Host "== uploading $archive"
& scp -q @ssh $archive "${Host_}:$remoteRoot/platform-api-$sha.tar.gz"
if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
& scp -q @ssh (Join-Path $PSScriptRoot 'release_core.sh') "${Host_}:$remoteRoot/release_core.sh"
if ($LASTEXITCODE -ne 0) { throw 'scp release_core.sh failed' }

Write-Host '== server-side release (schema gate, swap, readiness)'
& ssh @ssh $Host_ "sh $remoteRoot/release_core.sh $Target $sha $remoteRoot/platform-api-$sha.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "release_core.sh failed on $Target" }
Remove-Item $archive -ErrorAction SilentlyContinue
Write-Host "== done: $Target=$sha"
