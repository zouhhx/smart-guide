$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$script = Join-Path $root "src\knowledge\chromadb_vector_server.py"

Push-Location $root
try {
  & $python $script
} finally {
  Pop-Location
}
