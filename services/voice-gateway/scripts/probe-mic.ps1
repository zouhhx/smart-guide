param(
    [string]$Device = "",
    [double]$Seconds = 8
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Arguments = @("--config", "config\default.json", "probe", "--seconds", $Seconds)
if ($Device) { $Arguments += @("--device", $Device) }
& ".venv\Scripts\voice-gateway.exe" @Arguments
exit $LASTEXITCODE
