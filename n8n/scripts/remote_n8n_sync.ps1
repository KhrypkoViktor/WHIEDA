param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('export', 'import')]
  [string]$Mode,

  [Parameter(Mandatory = $true)]
  [string]$LocalPath,

  [string]$WorkflowId = 'tCuwyLflr0ukorER',
  [string]$RemoteHost = '185.252.232.93',
  [string]$RemoteUser = 'root',
  [string]$Password = '***REMOVED***'
)

$ErrorActionPreference = 'Stop'

function Invoke-RemoteProcess {
  param(
    [string]$FilePath,
    [string[]]$Arguments
  )

  $ask = Join-Path $env:TEMP ('codex-ssh-askpass-' + [guid]::NewGuid().ToString() + '.bat')
  Set-Content -LiteralPath $ask -Value "@echo $Password" -Encoding ASCII

  try {
    $attempt = 0
    do {
      $attempt++
      $psi = New-Object System.Diagnostics.ProcessStartInfo
      $psi.FileName = $FilePath
      $quotedArgs = foreach ($arg in $Arguments) {
        if ($null -eq $arg) {
          '""'
          continue
        }

        $text = [string]$arg
        if ($text -match '[\s"]') {
          '"' + ($text -replace '(\\*)"', '$1$1\"') + '"'
        }
        else {
          $text
        }
      }
      $psi.Arguments = [string]::Join(' ', $quotedArgs)
      $psi.UseShellExecute = $false
      $psi.RedirectStandardOutput = $true
      $psi.RedirectStandardError = $true
      $psi.CreateNoWindow = $true
      $psi.Environment['SSH_ASKPASS'] = $ask
      $psi.Environment['DISPLAY'] = '1'
      $psi.Environment['SSH_ASKPASS_REQUIRE'] = 'force'
      $psi.Environment['PATH'] = $env:PATH

      $proc = New-Object System.Diagnostics.Process
      $proc.StartInfo = $psi
      [void]$proc.Start()
      $stdout = $proc.StandardOutput.ReadToEnd()
      $stderr = $proc.StandardError.ReadToEnd()
      $proc.WaitForExit()

      if ($proc.ExitCode -eq 0) {
        return [pscustomobject]@{
          ExitCode = 0
          StdOut = $stdout
          StdErr = $stderr
        }
      }

      if ($attempt -lt 4) {
        Start-Sleep -Seconds 3
      }
    } while ($attempt -lt 4)
    throw ($stderr.Trim())
  }
  finally {
    Remove-Item -LiteralPath $ask -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-Ssh {
  param([string]$RemoteCommand)

  $result = Invoke-RemoteProcess -FilePath 'ssh.exe' -Arguments @(
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'PreferredAuthentications=password',
    '-o', 'PubkeyAuthentication=no',
    '-o', 'BatchMode=no',
    "$RemoteUser@$RemoteHost",
    $RemoteCommand
  )

  return $result.StdOut
}

function Invoke-Scp {
  param(
    [string]$SourcePath,
    [string]$DestinationPath
  )

  [void](Invoke-RemoteProcess -FilePath 'scp.exe' -Arguments @(
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'PreferredAuthentications=password',
    '-o', 'PubkeyAuthentication=no',
    '-o', 'BatchMode=no',
    $SourcePath,
    "${RemoteUser}@${RemoteHost}:$DestinationPath"
  ))
}

if ($Mode -eq 'export') {
  $remote = @"
docker exec n8n-n8n-1 n8n export:workflow --id $WorkflowId --output /tmp/live-wf.json >/dev/null 2>&1
docker exec n8n-n8n-1 cat /tmp/live-wf.json
"@
  $content = Invoke-Ssh -RemoteCommand $remote
  if (-not $content) {
    throw 'No workflow content received from remote host.'
  }
  $joined = [string]$content
  $joined = $joined.Trim()
  if (-not ($joined.StartsWith('{') -or $joined.StartsWith('['))) {
    throw "Remote export did not return JSON.`n$joined"
  }
  Set-Content -LiteralPath $LocalPath -Value $joined -Encoding UTF8
  exit 0
}

if (-not (Test-Path -LiteralPath $LocalPath)) {
  throw "Local file not found: $LocalPath"
}

Invoke-Scp -SourcePath $LocalPath -DestinationPath '/tmp/live-wf.json'

$remoteImport = @"
python3 - <<'PY'
import json
from pathlib import Path

workflow = json.loads(Path('/tmp/live-wf.json').read_text(encoding='utf-8'))
nodes = json.dumps(workflow['nodes'], ensure_ascii=False)
connections = json.dumps(workflow['connections'], ensure_ascii=False)
settings = json.dumps(workflow.get('settings') or {}, ensure_ascii=False)
meta = json.dumps(workflow.get('meta') or {}, ensure_ascii=False)
name = workflow['name'].replace("'", "''")

sql = f"""
update workflow_entity
set name = '{name}',
    nodes = '{nodes.replace("'", "''")}'::json,
    connections = '{connections.replace("'", "''")}'::json,
    settings = '{settings.replace("'", "''")}'::json,
    meta = '{meta.replace("'", "''")}'::json,
    active = true,
    \"updatedAt\" = now()
where id = '{workflow['id']}';

update workflow_history
set nodes = '{nodes.replace("'", "''")}'::json,
    connections = '{connections.replace("'", "''")}'::json
where \"workflowId\" = '{workflow['id']}';
"""

Path('/tmp/live-wf-update.sql').write_text(sql, encoding='utf-8')
PY
docker exec n8n-postgres-1 psql -U n8n -d n8n -f /tmp/live-wf-update.sql
"@

Invoke-Ssh -RemoteCommand $remoteImport | Write-Output
