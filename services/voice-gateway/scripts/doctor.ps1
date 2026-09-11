$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& ".venv\Scripts\voice-gateway.exe" --config "config\default.json" doctor
