param(
    [string]$PackageDirectory = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$WebUi = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $WebUi
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if ([string]::IsNullOrWhiteSpace($PackageDirectory)) {
    $PackageDirectory = Join-Path $ProjectRoot `
        "artifacts\VoiceGateway-G10C-Test"
}
$PackageDirectory = [IO.Path]::GetFullPath($PackageDirectory)
$ResolvedProjectRoot = [IO.Path]::GetFullPath(
    $ProjectRoot
).TrimEnd("\") + "\"
if (-not $PackageDirectory.StartsWith(
    $ResolvedProjectRoot,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Package directory must stay inside the project: $PackageDirectory"
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Development Python was not found: $Python"
}
if (-not (Test-Path -LiteralPath $Csc -PathType Leaf)) {
    throw ".NET Framework C# compiler was not found: $Csc"
}

$BuildRoot = Join-Path $ProjectRoot "build\webui"
$HelperBuildDirectory = Join-Path $BuildRoot "bin"
$HelperBuildPath = Join-Path $HelperBuildDirectory `
    "VoiceGateway.AudioControl.exe"
$PyInstallerBuild = Join-Path $BuildRoot "pyinstaller"
$Spec = Join-Path $BuildRoot "spec"
$PackageWebUi = Join-Path $PackageDirectory "webui"
$PackageBin = Join-Path $PackageWebUi "bin"
$PackageStatic = Join-Path $PackageWebUi "static"

New-Item -ItemType Directory -Force -Path @(
    $PackageDirectory,
    $HelperBuildDirectory,
    $PyInstallerBuild,
    $Spec,
    $PackageBin
) | Out-Null

& $Csc /nologo /target:exe /platform:x64 /optimize+ `
    "/out:$HelperBuildPath" `
    (Join-Path $WebUi "audio_helper\Program.cs")
if ($LASTEXITCODE -ne 0) {
    throw "Audio control helper build failed with exit code $LASTEXITCODE"
}
Copy-Item -LiteralPath $HelperBuildPath `
    -Destination (
        Join-Path $PackageBin "VoiceGateway.AudioControl.exe"
    ) `
    -Force

if (Test-Path -LiteralPath $PackageStatic) {
    Remove-Item -LiteralPath $PackageStatic -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $WebUi "static") `
    -Destination $PackageStatic `
    -Recurse

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name "VoiceGateway-WebUI" `
        --distpath $PackageDirectory `
        --workpath $PyInstallerBuild `
        --specpath $Spec `
        --collect-all sounddevice `
        --collect-all pypinyin `
        "webui\server.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Web manager build failed with exit code $LASTEXITCODE"
    }

    & $Python -B -m unittest discover -s "webui\tests" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Web manager tests failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host (
    "Web manager: " +
    (Join-Path $PackageDirectory "VoiceGateway-WebUI.exe")
)
