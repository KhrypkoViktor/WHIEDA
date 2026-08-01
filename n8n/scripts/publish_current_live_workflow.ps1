param(
  [string]$RemoteHost = '185.252.232.93',
  [string]$RemoteUser = 'root',
  [string]$SshPassword = $env:NORDMAN_LIVE_SSH_PASSWORD,
  [string]$N8nComposeDir = '~/n8n',
  [string]$WorkflowId = 'advisor-whieda-phase1'
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

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$remoteScript = @'
set -e
cd __N8N_COMPOSE_DIR__
WF_ID="__WORKFLOW_ID__"
STAMP="__STAMP__"
REMOTE_PUBLISHED="/tmp/${WF_ID}-published-before-${STAMP}.json"
REMOTE_PUBLISHED_HOST="/tmp/${WF_ID}-published-before-host-${STAMP}.json"
REMOTE_AFTER="/tmp/${WF_ID}-published-after-${STAMP}.json"
REMOTE_AFTER_HOST="/tmp/${WF_ID}-published-after-host-${STAMP}.json"

docker compose exec -T n8n n8n export:workflow --id="$WF_ID" --published --output="$REMOTE_PUBLISHED" >/dev/null
docker compose exec -T n8n cat "$REMOTE_PUBLISHED" > "$REMOTE_PUBLISHED_HOST"
docker compose exec -T n8n n8n publish:workflow --id="$WF_ID" >/dev/null
docker compose restart n8n >/dev/null
sleep 8
docker compose exec -T n8n n8n export:workflow --id="$WF_ID" --published --output="$REMOTE_AFTER" >/dev/null
docker compose exec -T n8n cat "$REMOTE_AFTER" > "$REMOTE_AFTER_HOST"
docker compose exec -T postgres psql -U n8n -d n8n -c "select id, \"versionId\", \"activeVersionId\", active, \"updatedAt\" from workflow_entity where id='${WF_ID}';"
docker compose exec -T postgres psql -U n8n -d n8n -c "select \"workflowId\", \"publishedVersionId\", \"updatedAt\" from workflow_published_version where \"workflowId\"='${WF_ID}';"
echo "PUBLISHED_BEFORE_HOST=$REMOTE_PUBLISHED_HOST"
echo "PUBLISHED_AFTER_HOST=$REMOTE_AFTER_HOST"
'@

$remoteScript = $remoteScript.Replace('__N8N_COMPOSE_DIR__', $N8nComposeDir)
$remoteScript = $remoteScript.Replace('__WORKFLOW_ID__', $WorkflowId)
$remoteScript = $remoteScript.Replace('__STAMP__', $stamp)

$local = Join-Path $env:TEMP ('publish-current-live-workflow-' + [guid]::NewGuid().ToString() + '.sh')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($local, ($remoteScript -replace "`r`n", "`n"), $utf8NoBom)

try {
  Invoke-RemoteProcess -FilePath 'scp.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    $local,
    "${RemoteUser}@${RemoteHost}:/tmp/publish_current_live_workflow.sh"
  ) | Out-Null

  Invoke-RemoteProcess -FilePath 'ssh.exe' -Password $SshPassword -Arguments @(
    '-o','StrictHostKeyChecking=no',
    '-o','PreferredAuthentications=password',
    '-o','PubkeyAuthentication=no',
    '-o','BatchMode=no',
    "$RemoteUser@$RemoteHost",
    'bash /tmp/publish_current_live_workflow.sh'
  ) | Write-Output
}
finally {
  Remove-Item -LiteralPath $local -Force -ErrorAction SilentlyContinue
}
