param(
  [Parameter(Mandatory = $true)]
  [string]$MessageText,
  [string]$WebhookUrl = 'https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0',
  [string]$ChatId = '688931415',
  [string]$Username = 'SunRaySword',
  [string]$FirstName = 'Viktor',
  [int]$MessageId = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($MessageId -le 0) {
  $MessageId = [int][double]::Parse((Get-Date -UFormat %s)) % 1000000
}

$payload = @{
  message = @{
    text = $MessageText
    date = [int][double]::Parse((Get-Date -UFormat %s))
    from = @{
      id = [int64]$ChatId
      is_bot = $false
      username = $Username
      first_name = $FirstName
    }
    message_id = $MessageId
    chat = @{
      type = 'private'
      id = [int64]$ChatId
    }
  }
}

$body = $payload | ConvertTo-Json -Depth 6 -Compress
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$bytes = $utf8NoBom.GetBytes($body)

Invoke-RestMethod -Method Post -Uri $WebhookUrl -ContentType 'application/json; charset=utf-8' -Body $bytes
