param(
  [string]$RemoteHost = '185.252.232.93',
  [string]$RemoteUser = 'root',
  [string]$SshPassword = $env:NORDMAN_LIVE_SSH_PASSWORD,
  [string]$N8nComposeDir = '~/n8n'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $SshPassword) {
  throw 'Set SshPassword or NORDMAN_LIVE_SSH_PASSWORD first.'
}

function Invoke-RemoteProcess {
  param(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$Password
  )

  $ask = Join-Path $env:TEMP ('codex-ssh-askpass-' + [guid]::NewGuid().ToString() + '.bat')
  Set-Content -LiteralPath $ask -Value "@echo $Password" -Encoding ASCII

  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $quotedArgs = foreach ($arg in $Arguments) {
      $text = [string]$arg
      if ($text -match '[\s"]') { '"' + ($text -replace '(\\*)"', '$1$1\"') + '"' } else { $text }
    }
    $psi.Arguments = [string]::Join(' ', $quotedArgs)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.Environment['SSH_ASKPASS'] = $ask
    $psi.Environment['DISPLAY'] = '1'
    $psi.Environment['SSH_ASKPASS_REQUIRE'] = 'force'
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) {
      throw ($stderr.Trim())
    }
    return $stdout
  }
  finally {
    Remove-Item -LiteralPath $ask -Force -ErrorAction SilentlyContinue
  }
}

$remoteScript = @'
set -e
cd __N8N_COMPOSE_DIR__
docker compose exec -T postgres psql -U n8n -d n8n <<SQL
select 'advisor_events' as table_name, count(*)::bigint as rows from advisor_events
union all
select 'advisor_review_queue', count(*)::bigint from advisor_review_queue
union all
select 'advisor_structured_products', count(*)::bigint from advisor_structured_products
union all
select 'advisor_structured_aliases', count(*)::bigint from advisor_structured_aliases
union all
select 'advisor_structured_resources', count(*)::bigint from advisor_structured_resources
union all
select 'advisor_users', count(*)::bigint from advisor_users
union all
select 'advisor_conversations', count(*)::bigint from advisor_conversations
order by table_name;

select source_type, status, count(*)::bigint
from advisor_review_queue
group by source_type, status
order by source_type, status;

select client_id, source_type, source_ref, source_title, status, created_at
from advisor_review_queue
order by created_at desc
limit 10;
SQL
'@

$remoteScript = $remoteScript.Replace('__N8N_COMPOSE_DIR__', $N8nComposeDir)
$local = Join-Path $env:TEMP ('inspect-runtime-db-summary-' + [guid]::NewGuid().ToString() + '.sh')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($local, ($remoteScript -replace "`r`n", "`n"), $utf8NoBom)

try {
  Invoke-RemoteProcess -FilePath 'scp.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    $local,
    "${RemoteUser}@${RemoteHost}:/tmp/inspect_runtime_db_summary.sh"
  ) | Out-Null

  Invoke-RemoteProcess -FilePath 'ssh.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    "$RemoteUser@$RemoteHost",
    'bash /tmp/inspect_runtime_db_summary.sh'
  ) | Write-Output
}
finally {
  Remove-Item -LiteralPath $local -Force -ErrorAction SilentlyContinue
}
