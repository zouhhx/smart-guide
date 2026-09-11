$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"

if (Test-Path $envPath) {
  Get-Content -Encoding UTF8 $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") {
      return
    }

    $idx = $line.IndexOf("=")
    $name = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim().Trim("'`"")

    if ($name) {
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

$env:PYTHONUNBUFFERED = "1"
Set-Location $root

$python = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

& $python (Join-Path $root "src\tts\iflytek_tts_http_server.py") --host 127.0.0.1 --port 3011
