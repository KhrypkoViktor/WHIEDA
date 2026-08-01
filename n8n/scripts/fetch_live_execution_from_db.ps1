param(
  [Parameter(Mandatory = $true)]
  [string]$ExecutionId,
  [string]$OutputPath = '',
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

if (-not $OutputPath) {
  $OutputPath = Join-Path (Get-Location) ("execution_${ExecutionId}_live_db.json")
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

$query = @"
copy (
  select json_build_object(
    'data',
    json_build_object(
      'id', e.id,
      'finished', e.finished,
      'mode', e.mode,
      'retryOf', e."retryOf",
      'retrySuccessId', e."retrySuccessId",
      'status', e.status,
      'createdAt', e."createdAt",
      'startedAt', e."startedAt",
      'stoppedAt', e."stoppedAt",
      'deletedAt', e."deletedAt",
      'workflowId', e."workflowId",
      'waitTill', e."waitTill",
      'storedAt', e."storedAt",
      'data', d.data
    )
  )::text
  from execution_entity e
  join execution_data d on d."executionId" = e.id
  where e.id = '$ExecutionId'
) to stdout;
"@

$localSql = Join-Path $env:TEMP ('whieda-fetch-execution-' + [guid]::NewGuid().ToString() + '.sql')
$remoteSql = '/tmp/' + [IO.Path]::GetFileName($localSql)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($localSql, ($query -replace "`r`n", "`n"), $utf8NoBom)

try {
  Invoke-RemoteProcess -FilePath 'scp.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    $localSql,
    "${RemoteUser}@${RemoteHost}:${remoteSql}"
  ) | Out-Null

  Invoke-RemoteProcess -FilePath 'ssh.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    "$RemoteUser@$RemoteHost",
    "cd $N8nComposeDir && docker compose exec -T postgres psql -U n8n -d n8n < $remoteSql"
  ) | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}
finally {
  Remove-Item -LiteralPath $localSql -Force -ErrorAction SilentlyContinue
}

Write-Output $OutputPath
