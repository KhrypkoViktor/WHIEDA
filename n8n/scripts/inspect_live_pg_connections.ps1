param(
  [string]$RemoteHost = '185.252.232.93',
  [string]$RemoteUser = 'root',
  [string]$SshPassword = $env:NORDMAN_LIVE_SSH_PASSWORD,
  [int]$DurationSeconds = 12
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
END=$((SECONDS + __DURATION__))
while [ $SECONDS -lt $END ]; do
  date -Iseconds
  ss -tpn | grep ':5432' || true
  echo "---"
  sleep 1
done
'@

$remoteScript = $remoteScript.Replace('__DURATION__', [string]$DurationSeconds)
$local = Join-Path $env:TEMP ('inspect-live-pg-connections-' + [guid]::NewGuid().ToString() + '.sh')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($local, ($remoteScript -replace "`r`n", "`n"), $utf8NoBom)

try {
  Invoke-RemoteProcess -FilePath 'scp.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    $local,
    "${RemoteUser}@${RemoteHost}:/tmp/inspect_live_pg_connections.sh"
  ) | Out-Null

  Invoke-RemoteProcess -FilePath 'ssh.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    "$RemoteUser@$RemoteHost",
    'bash /tmp/inspect_live_pg_connections.sh'
  ) | Write-Output
}
finally {
  Remove-Item -LiteralPath $local -Force -ErrorAction SilentlyContinue
}
