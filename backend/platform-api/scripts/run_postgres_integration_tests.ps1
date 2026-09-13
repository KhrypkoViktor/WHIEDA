# Run the live-PostgreSQL integration tests on a throwaway local cluster.
#
# The *_postgres.py tests are the only ones that execute the real SQL against the
# real migrations. Without PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN they silently
# skip — which is how an ON CONFLICT that did not match the production index
# reached production on 2026-09-12. This script removes the excuse: it needs
# only a PostgreSQL install (scoop, EDB or PG_BIN), no Docker, no shared DB.
#
#   pwsh backend/platform-api/scripts/run_postgres_integration_tests.ps1
#   pwsh backend/platform-api/scripts/run_postgres_integration_tests.ps1 -Port 55433 -PytestArgs @('-k','referral')
#
# The port is chosen free at run time (or verified free when given), so the tests
# can never connect to somebody else's already-running PostgreSQL and report a
# result that has nothing to do with the cluster we just created.

[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$PgBin = $env:PG_BIN,
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$apiRoot = Split-Path -Parent $PSScriptRoot

if (-not $PgBin) {
    # Outer @(): a single surviving path must stay an array, or [0] yields the letter 'C'.
    $candidates = @(
        @(
            "$env:USERPROFILE\scoop\apps\postgresql\current\bin",
            (Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName)
        ) | Where-Object { $_ -and (Test-Path (Join-Path $_ 'pg_ctl.exe')) }
    )
    if (-not $candidates) {
        throw 'PostgreSQL binaries not found. Install postgresql (scoop install postgresql) or set PG_BIN.'
    }
    $PgBin = $candidates[0]
}

function Test-PortFree([int]$candidate) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
        $listener.Start(); $listener.Stop(); return $true
    } catch { return $false }
}

if ($Port -eq 0) {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $Port = $probe.LocalEndpoint.Port; $probe.Stop()
} elseif (-not (Test-PortFree $Port)) {
    throw "port $Port is already in use on 127.0.0.1; refusing to run against a foreign PostgreSQL"
}

$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$cluster = Join-Path ([IO.Path]::GetTempPath()) "whieda-pgtest-$stamp"
$dataDir = Join-Path $cluster 'data'
$pwFile = Join-Path $cluster 'pw.txt'
$logFile = Join-Path $cluster 'postgres.log'
$password = 'pgtest_' + [guid]::NewGuid().ToString('N').Substring(0, 12)

New-Item -ItemType Directory -Force $cluster | Out-Null
Set-Content -Path $pwFile -Value $password -NoNewline -Encoding ascii

$started = $false
try {
    & (Join-Path $PgBin 'initdb.exe') -D $dataDir -U postgres --pwfile=$pwFile -E UTF8 -A scram-sha-256 *> (Join-Path $cluster 'initdb.log')
    if ($LASTEXITCODE -ne 0) { throw "initdb failed, see $cluster\initdb.log" }

    # pg_ctl must not be run through a PowerShell pipeline: the postmaster inherits the
    # stdout handle and pwsh then waits on it forever. Detach and poll pg_isready instead.
    Start-Process -FilePath (Join-Path $PgBin 'pg_ctl.exe') `
        -ArgumentList @('-D', "`"$dataDir`"", '-o', "`"-p $Port -c listen_addresses=127.0.0.1`"", '-l', "`"$logFile`"", 'start') `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $cluster 'pg_ctl.out') -RedirectStandardError (Join-Path $cluster 'pg_ctl.err')
    $started = $true
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        & (Join-Path $PgBin 'pg_isready.exe') -h 127.0.0.1 -p $Port -q
        $ready = ($LASTEXITCODE -eq 0)
    } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw "PostgreSQL did not become ready on port $Port, see $logFile" }

    $env:PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN = "postgresql://postgres:$password@127.0.0.1:$Port/postgres"
    Push-Location $apiRoot
    try {
        $tests = Get-ChildItem (Join-Path $apiRoot 'tests') -Filter '*_postgres.py' | ForEach-Object { "tests/$($_.Name)" }
        Write-Host "PostgreSQL $Port up; running: $($tests -join ' ')"
        & python -m pytest @tests -q -m integration @PytestArgs
        $exit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    exit $exit
} finally {
    if ($started) {
        & (Join-Path $PgBin 'pg_ctl.exe') -D $dataDir -m fast -w stop *> $null
    }
    Remove-Item -Recurse -Force $cluster -ErrorAction SilentlyContinue
}
