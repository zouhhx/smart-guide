$ErrorActionPreference = 'Continue'

$platformRoot = 'D:\exhibition-hall-new\runtime\exhibition-hall-platform'
$logPath = Join-Path $platformRoot 'obsidian-analysis-rerun.log'
$body = @{
  vaultPath = Join-Path $platformRoot 'data\obsidian-vault'
  limit = 'all'
  force = $true
  missingAnalysisOnly = $true
} | ConvertTo-Json -Compress

Add-Content -Path $logPath -Value "START $(Get-Date -Format o)"
try {
  $result = Invoke-RestMethod 'http://127.0.0.1:3001/api/knowledge/obsidian-analyze' `
    -Method Post `
    -ContentType 'application/json' `
    -Body $body `
    -TimeoutSec 21600
  $json = $result | ConvertTo-Json -Depth 8 -Compress
  Add-Content -Path $logPath -Value "DONE $(Get-Date -Format o) $json"
} catch {
  Add-Content -Path $logPath -Value "ERROR $(Get-Date -Format o) $($_.Exception.Message)"
}
