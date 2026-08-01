param(
  [string]$RemoteHost = '185.252.232.93',
  [string]$RemoteUser = 'root',
  [string]$SshPassword = $env:NORDMAN_LIVE_SSH_PASSWORD,
  [string]$N8nComposeDir = '~/n8n',
  [string]$CredentialName = 'advisor-dev-postgres'
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
OUT=/tmp/n8n-credentials-export.json
docker compose exec -T n8n n8n export:credentials --all --decrypted --output="$OUT" >/dev/null
docker compose exec -T n8n cat "$OUT"
'@

$remoteScript = $remoteScript.Replace('__N8N_COMPOSE_DIR__', $N8nComposeDir)
$local = Join-Path $env:TEMP ('inspect-advisor-dev-postgres-' + [guid]::NewGuid().ToString() + '.sh')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($local, ($remoteScript -replace "`r`n", "`n"), $utf8NoBom)

try {
  Invoke-RemoteProcess -FilePath 'scp.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    $local,
    "${RemoteUser}@${RemoteHost}:/tmp/inspect_advisor_dev_postgres_target.sh"
  ) | Out-Null

  $raw = Invoke-RemoteProcess -FilePath 'ssh.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    "$RemoteUser@$RemoteHost",
    'bash /tmp/inspect_advisor_dev_postgres_target.sh'
  )

  $parsed = $raw | ConvertFrom-Json
  $target = @($parsed) | Where-Object { $_.name -eq $CredentialName } | Select-Object -First 1
  if (-not $target) {
    throw "Credential not found: $CredentialName"
  }

  $data = $target.data
  [pscustomobject]@{
    id = $target.id
    name = $target.name
    type = $target.type
    host = $data.host
    port = $data.port
    database = $data.database
    user = $data.user
    schema = $data.schema
    ssl = $data.ssl
    ignoreSSL = $data.ignoreSSL
    sshTunnel = $data.sshTunnel
  } | ConvertTo-Json -Depth 4
}
finally {
  Remove-Item -LiteralPath $local -Force -ErrorAction SilentlyContinue
}
