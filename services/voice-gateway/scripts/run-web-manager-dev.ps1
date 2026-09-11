param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8765,
    [switch]$OpenBrowser,
    [switch]$NoAutoStart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Package = Join-Path $Root "artifacts\VoiceGateway-G10C-Test"
$Gateway = Join-Path $Package "VoiceGateway-G10C-Test.exe"
$Helper = Join-Path $Package `
    "webui\bin\VoiceGateway.AudioControl.exe"
$Config = Join-Path $Package "config\default.json"

foreach ($RequiredFile in @($Python, $Gateway, $Helper, $Config)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required development runtime file is missing: $RequiredFile"
    }
}

$Arguments = @(
    "-B",
    (Join-Path $Root "webui\server.py"),
    "--root",
    $Root,
    "--config",
    $Config,
    "--gateway",
    $Gateway,
    "--audio-helper",
    $Helper,
    "--host",
    $HostAddress,
    "--port",
    $Port
)
if ($OpenBrowser) {
    $Arguments += "--open-browser"
}
if ($NoAutoStart) {
    $Arguments += "--no-auto-start"
}

Push-Location $Root
try {
    & $Python @Arguments
}
finally {
    Pop-Location
}
