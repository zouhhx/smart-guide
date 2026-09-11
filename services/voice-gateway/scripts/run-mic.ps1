param(
    [string]$Device = "",
    [double]$Seconds = 0,
    [switch]$BypassKws
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Arguments = @("--config", "config\default.json", "mic")
if ($Device) { $Arguments += @("--device", $Device) }
if ($Seconds -gt 0) { $Arguments += @("--seconds", $Seconds) }
if ($BypassKws) { $Arguments += "--bypass-kws" }
& ".venv\Scripts\voice-gateway.exe" @Arguments
exit $LASTEXITCODE
