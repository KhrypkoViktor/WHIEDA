# Release Core (or staging) from a committed revision, with mandatory gates.
#
#   pwsh backend/deploy/core/release_core.ps1 -Target staging            # HEAD
#   pwsh backend/deploy/core/release_core.ps1 -Target core -Revision 6aaa96c
#   pwsh backend/deploy/core/release_core.ps1 -Target core -Revision <old> -AllowRollback
#
# Gates, in order — any failure stops before anything reaches the server:
#   1. the revision exists and (for HEAD) the tree is clean;
#   2. the candidate contains the revision now live on the target (read from
#      <root>/RELEASE_REVISION over ssh; git merge-base --is-ancestor). A slice
#      released from a branch that is not merged into the common line would be
#      silently reverted by the next release (17.10.2026, 6b2ca06). Unreadable
#      RELEASE_REVISION only warns (first release on a clean target). An
#      intentional rollback needs -AllowRollback, which skips only this check;
#   3. the focused unit suite (referral, subscriptions, telegram, schema map) passes;
#   4. the live-PostgreSQL integration tests pass on a throwaway local cluster
#      (scripts/run_postgres_integration_tests.ps1) — never skipped;
# then the archive is copied and release_core.sh runs the server-side schema gate
# and the swap. Production is never touched by hand any more.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('core', 'staging')][string]$Target,
    [string]$Revision = 'HEAD',
    [string]$SshKey = "$env:USERPROFILE\.ssh\wwc_deploy_ed25519",
    [string]$Host_ = 'root@185.252.232.93',
    [switch]$SkipUnitTests,
    [switch]$AllowRollback
)

$ErrorActionPreference = 'Stop'

# Must match root= in release_core.sh (it writes RELEASE_REVISION there).
function Get-RemoteRoot([string]$Target) {
    if ($Target -eq 'core') { '/opt/whieda-platform-core' } else { '/opt/whieda-platform-staging' }
}

function Test-GitCommit([string]$RepoRoot, [string]$Rev) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        git -C $RepoRoot cat-file -e "${Rev}^{commit}" 2>$null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $old
    }
}

# Gate 2: $Sha must contain the revision currently live on $Target.
function Assert-ContainsLiveRevision {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Sha,
        [Parameter(Mandatory)][ValidateSet('core', 'staging')][string]$Target,
        [Parameter(Mandatory)][string]$SshKey,
        [Parameter(Mandatory)][string]$Host_,
        [switch]$AllowRollback
    )

    $remoteFile = "$(Get-RemoteRoot $Target)/RELEASE_REVISION"
    $liveName = if ($Target -eq 'core') { 'бой' } else { 'staging' }
    Write-Host "== gate: $Sha contains the revision live on $Target"

    # stderr is captured (not thrown) so an unreachable host only warns.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & ssh -i $SshKey -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 $Host_ "cat $remoteFile" 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    $live = (($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join '').Trim()
    $why = (($raw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }) -join ' ').Trim()
    if ($exit -ne 0 -or -not $live) {
        Write-Warning "ancestor gate SKIPPED: cannot read $remoteFile on $Host_ (ssh exit $exit$(if ($why) { ": $why" })). Fine for the first release on a clean target, otherwise check the target by hand."
        return
    }
    if ($live -notmatch '^[0-9a-fA-F]{7,40}$') {
        Write-Warning "ancestor gate SKIPPED: $remoteFile holds '$live', not a commit id."
        return
    }

    if (-not (Test-GitCommit $RepoRoot $live)) {
        Write-Host "   $live is unknown locally; git fetch origin"
        git -C $RepoRoot fetch origin --quiet
        if (-not (Test-GitCommit $RepoRoot $live)) {
            throw "$liveName стоит на $live, этого коммита нет в репозитории — выясни, чей срез, и влей его"
        }
    }

    git -C $RepoRoot merge-base --is-ancestor $live $Sha
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "   ok: $Sha contains $live"
        return
    }
    if ($code -ne 1) { throw "git merge-base --is-ancestor $live $Sha failed (exit $code)" }

    # $live is not an ancestor of $Sha: show what the release would drop.
    Write-Host "   commits of $live missing from ${Sha}:"
    git -C $RepoRoot log --oneline --no-decorate -n 20 "$Sha..$live" | Out-Host
    if ($AllowRollback) {
        Write-Warning "-AllowRollback: $Sha does NOT contain $liveName $live; this release REVERTS the commits above. Ancestor gate skipped on request."
        return
    }
    throw "$Sha не содержит текущий $liveName ${live}: релиз откатил бы чужой срез. Влей $live (или общую линию origin/core/academy-threads-20261013) и повтори; намеренный откат — ключ -AllowRollback"
}
$repoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
$apiRoot = Join-Path $repoRoot 'backend/platform-api'

$sha = (git -C $repoRoot rev-parse --short $Revision).Trim()
if (-not $sha) { throw "unknown revision $Revision" }
if ($Revision -eq 'HEAD') {
    # git archive ships the commit, so only modified tracked files can make the
    # release differ from what was tested; untracked noise (pycache) is ignored.
    $dirty = git -C $repoRoot status --porcelain --untracked-files=no -- backend/platform-api
    if ($dirty) { throw "backend/platform-api has uncommitted changes; commit or pass -Revision" }
}
Write-Host "== releasing $Target <- $sha"
Assert-ContainsLiveRevision -RepoRoot $repoRoot -Sha $sha -Target $Target -SshKey $SshKey -Host_ $Host_ -AllowRollback:$AllowRollback

Push-Location $apiRoot
try {
    if (-not $SkipUnitTests) {
        Write-Host '== gate: focused unit tests'
        & python -m pytest -q -p no:cacheprovider `
            tests/test_referral_bonus_service.py tests/test_referral_bonus_schema.py `
            tests/test_partner_subscriptions.py tests/test_telegram_processor.py `
            tests/test_telegram_referral_admin.py tests/test_lead_actor_telegram_link.py `
            tests/test_schema_requirements.py tests/test_identity_exchange_memory_best_effort.py `
            tests/test_site_account.py tests/test_pro_start.py tests/test_telegram_support_tunnel.py `
            tests/test_bot_copy_audit.py tests/test_telegram_renewal_requests.py tests/test_partner_payment_reminder.py `
            tests/test_pricing.py tests/test_telegram_billing_multiline.py tests/test_partner_subscription_reminders.py `
            tests/test_telegram_service_sales.py tests/test_telegram_route_truth_table.py tests/test_advisor_bundle_products.py
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

$remoteRoot = Get-RemoteRoot $Target
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
