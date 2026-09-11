param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $Python -3.12 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install setuptools==80.9.0 wheel==0.45.1
& ".venv\Scripts\python.exe" -m pip install -r requirements.lock
& ".venv\Scripts\python.exe" -m pip install -r requirements-build.lock
& ".venv\Scripts\python.exe" -m pip install --no-build-isolation -e .
Write-Host "Development environment is ready."
