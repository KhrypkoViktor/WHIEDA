param(
  [Parameter(Mandatory = $true)]
  [string]$Password,

  [string]$Host = "185.252.232.93",
  [string]$User = "root",
  [string]$HostKey = "ssh-ed25519 255 SHA256:VayaKMuCqC0/a32Sj3vXLvxfdw/8P/PeM8L7RWYtxMM"
)

$ErrorActionPreference = "Stop"

$workspace = "C:\Users\srs\Documents\WHIEDA"
$patchName = "advisor-whieda-phase1_review_trusted_queue_active_version_patch_2026-07-11.sql"
$localPatch = Join-Path $workspace $patchName
$remotePatch = "/root/$patchName"
$pscp = "C:\Users\srs\scoop\shims\pscp.exe"
$plink = "C:\Users\srs\scoop\shims\plink.exe"

if (-not (Test-Path -LiteralPath $localPatch)) {
  throw "Local patch not found: $localPatch"
}

Write-Host "Uploading patch to $Host ..."
& $pscp -batch -pw $Password -hostkey $HostKey $localPatch "${User}@${Host}:${remotePatch}"
if ($LASTEXITCODE -ne 0) {
  throw "Upload failed with exit code $LASTEXITCODE"
}

Write-Host "Applying patch inside ~/n8n ..."
& $plink -batch -ssh "${User}@${Host}" -pw $Password -hostkey $HostKey "cd ~/n8n && cat $remotePatch | docker compose exec -T postgres psql -U n8n -d n8n"
if ($LASTEXITCODE -ne 0) {
  throw "Patch apply failed with exit code $LASTEXITCODE"
}

Write-Host "Done."
