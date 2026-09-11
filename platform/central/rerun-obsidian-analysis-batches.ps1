$ErrorActionPreference = 'Continue'

$platformRoot = 'D:\exhibition-hall-new\runtime\exhibition-hall-platform'
$logPath = Join-Path $platformRoot 'obsidian-analysis-rerun.log'
$vaultPath = Join-Path $platformRoot 'data\obsidian-vault'
$batchSize = 5
$maxBatches = 80

Add-Content -Path $logPath -Value "BATCH_START $(Get-Date -Format o) batchSize=$batchSize"

for ($i = 1; $i -le $maxBatches; $i += 1) {
  $body = @{
    vaultPath = $vaultPath
    limit = $batchSize
    force = $true
    missingAnalysisOnly = $true
  } | ConvertTo-Json -Compress

  try {
    $result = Invoke-RestMethod 'http://127.0.0.1:3001/api/knowledge/obsidian-analyze' `
      -Method Post `
      -ContentType 'application/json' `
      -Body $body `
      -TimeoutSec 1800

    $analysis = $result.status.analysis
    $message = "BATCH $i $(Get-Date -Format o) analyzed=$($analysis.analyzed) success=$($analysis.success) failed=$($analysis.failed) candidates=$($analysis.candidateCount)"
    Add-Content -Path $logPath -Value $message

    if ([int]$analysis.analyzed -le 0 -or [int]$analysis.candidateCount -le 0) {
      Add-Content -Path $logPath -Value "BATCH_DONE $(Get-Date -Format o) reason=no-candidates"
      break
    }
  } catch {
    Add-Content -Path $logPath -Value "BATCH_ERROR $i $(Get-Date -Format o) $($_.Exception.Message)"
    Start-Sleep -Seconds 5
  }
}

try {
  $r = Invoke-RestMethod 'http://127.0.0.1:3001/api/knowledge/obsidian-analyses?limit=300'
  $total = $r.items.Count
  $filled = ($r.items | Where-Object { $_.analysisResult -and $_.analysisResult.Length -gt 0 }).Count
  Add-Content -Path $logPath -Value "BATCH_SUMMARY $(Get-Date -Format o) filled=$filled total=$total"
} catch {
  Add-Content -Path $logPath -Value "BATCH_SUMMARY_ERROR $(Get-Date -Format o) $($_.Exception.Message)"
}
