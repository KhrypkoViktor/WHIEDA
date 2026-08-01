$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$workflowPath = Join-Path $workspaceRoot 'live-workflow-split.json'
$sqlPath = Join-Path $workspaceRoot 'live-workflow-update.sql'

$raw = Get-Content -LiteralPath $workflowPath -Raw -Encoding UTF8
$jsonStart = $raw.IndexOf('[')
if ($jsonStart -gt 0) {
  $raw = $raw.Substring($jsonStart)
}
$parsed = $raw | ConvertFrom-Json
if ($parsed -is [System.Array]) {
  $workflow = $parsed[0]
} else {
  $workflow = $parsed
}

function Escape-SqlText([string]$value) {
  if ($null -eq $value) { return '' }
  return $value.Replace("'", "''")
}

$name = Escape-SqlText $workflow.name
$nodes = Escape-SqlText (($workflow.nodes | ConvertTo-Json -Depth 100 -Compress))
$connections = Escape-SqlText (($workflow.connections | ConvertTo-Json -Depth 100 -Compress))
$settings = Escape-SqlText (($workflow.settings | ConvertTo-Json -Depth 100 -Compress))
$meta = Escape-SqlText (($workflow.meta | ConvertTo-Json -Depth 100 -Compress))
$workflowId = Escape-SqlText $workflow.id

$sql = @"
update workflow_entity
set name = '$name',
    nodes = '$nodes'::json,
    connections = '$connections'::json,
    settings = '$settings'::json,
    meta = '$meta'::json,
    active = true,
    "updatedAt" = now()
where id = '$workflowId';

update workflow_history
set nodes = '$nodes'::json,
    connections = '$connections'::json
where "workflowId" = '$workflowId';
"@

Set-Content -LiteralPath $sqlPath -Value $sql -Encoding UTF8
