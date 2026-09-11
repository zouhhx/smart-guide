param(
    [switch]$CreateZip,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $Root "src"
$Artifacts = Join-Path $Root "artifacts"
$Package = Join-Path $Artifacts "VoiceGateway-G10C-Test"
$PreviousPackage = Join-Path $Artifacts `
    ".VoiceGateway-G10C-Test.previous"
$StageRoot = Join-Path $Root "build\package-stage"
$StagedPackage = Join-Path $StageRoot "VoiceGateway-G10C-Test"
$CoreBuild = Join-Path $Root "build\core\pyinstaller"
$Spec = Join-Path $Root "build\core\spec"

function Read-RequiredVersion(
    [string]$Path,
    [string]$Pattern,
    [string]$Label
) {
    $VersionMatch = Select-String `
        -LiteralPath $Path `
        -Pattern $Pattern |
        Select-Object -First 1
    if (-not $VersionMatch) {
        throw "Unable to determine $Label version from: $Path"
    }
    return $VersionMatch.Matches[0].Groups[1].Value
}

function Remove-SafeDirectory(
    [string]$Path,
    [string]$AllowedRoot
) {
    $ResolvedAllowed = [IO.Path]::GetFullPath(
        $AllowedRoot
    ).TrimEnd("\") + "\"
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $ResolvedPath.StartsWith(
        $ResolvedAllowed,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove path outside build root: $ResolvedPath"
    }
    if (Test-Path -LiteralPath $ResolvedPath) {
        Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
    }
}

$ProjectVersion = Read-RequiredVersion `
    (Join-Path $Root "pyproject.toml") `
    '^version\s*=\s*"([^"]+)"\s*$' `
    "project"
$CoreVersion = Read-RequiredVersion `
    (Join-Path $Root "src\voice_gateway\__init__.py") `
    '^__version__\s*=\s*"([^"]+)"\s*$' `
    "voice core"
$WebVersion = Read-RequiredVersion `
    (Join-Path $Root "webui\server.py") `
    '^APP_VERSION\s*=\s*"([^"]+)"\s*$' `
    "Web manager"
if ($ProjectVersion -ne $CoreVersion -or $ProjectVersion -ne $WebVersion) {
    throw (
        "Version mismatch: pyproject=$ProjectVersion, " +
        "core=$CoreVersion, web=$WebVersion"
    )
}
if (
    -not [string]::IsNullOrWhiteSpace($Version) -and
    $Version -ne $ProjectVersion
) {
    throw (
        "Requested release version $Version does not match " +
        "the source version $ProjectVersion."
    )
}
$Version = $ProjectVersion

$RequiredFiles = @(
    $Python,
    (Join-Path $Root "models\models.lock.json"),
    (Join-Path $Root "config\default.json"),
    (Join-Path $Root "docs\WEB-MANAGER.md"),
    (Join-Path $Root "docs\KWS-WAKE-NOTIFY-API.md"),
    (Join-Path $Root "docs\G10-C-FIELD-TEST.md"),
    (Join-Path $Root "webui\server.py"),
    (Join-Path $Root "webui\audio_helper\Program.cs")
)
foreach ($RequiredFile in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required build input is missing: $RequiredFile"
    }
}
$DeployScripts = @(
    Get-ChildItem -LiteralPath (Join-Path $Root "deploy") `
        -File `
        -Filter "*.cmd"
)
if ($DeployScripts.Count -ne 6) {
    throw "Expected six deployment CMD files, found $($DeployScripts.Count)."
}

Push-Location $Root
try {
    & $Python -B -m unittest discover -s "tests" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Core tests failed with exit code $LASTEXITCODE"
    }

    Remove-SafeDirectory $StageRoot (Join-Path $Root "build")
    Remove-SafeDirectory $CoreBuild (Join-Path $Root "build")
    Remove-SafeDirectory $Spec (Join-Path $Root "build")
    New-Item -ItemType Directory -Force -Path @(
        $StageRoot,
        $CoreBuild,
        $Spec
    ) | Out-Null

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --console `
        --name "VoiceGateway-G10C-Test" `
        --distpath $StageRoot `
        --workpath $CoreBuild `
        --specpath $Spec `
        --collect-all sherpa_onnx `
        --collect-all sounddevice `
        "tools\voice_gateway_entry.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $PackagedCore = Join-Path $StagedPackage `
        "VoiceGateway-G10C-Test.exe"
    $PackagedCoreVersion = (
        & $PackagedCore --version | Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Packaged core version check failed with exit code " +
            $LASTEXITCODE
        )
    }
    if ($PackagedCoreVersion -ne $Version) {
        throw (
            "Packaged core version mismatch: expected $Version, " +
            "got $PackagedCoreVersion"
        )
    }

    New-Item -ItemType Directory -Force -Path @(
        (Join-Path $StagedPackage "config"),
        (Join-Path $StagedPackage "models"),
        (Join-Path $StagedPackage "testdata")
    ) | Out-Null
    Copy-Item -LiteralPath "config\default.json" `
        -Destination (Join-Path $StagedPackage "config\default.json")
    Copy-Item -LiteralPath "models\models.lock.json" `
        -Destination (Join-Path $StagedPackage "models\models.lock.json")
    foreach ($ModelDirectory in @("kws", "vad", "asr")) {
        Copy-Item -LiteralPath (Join-Path "models" $ModelDirectory) `
            -Destination (
                Join-Path $StagedPackage "models\$ModelDirectory"
            ) `
            -Recurse
    }
    Copy-Item -Path "deploy\*.cmd" -Destination $StagedPackage
    Copy-Item -LiteralPath "README.md" `
        -Destination (Join-Path $StagedPackage "README.md")
    Copy-Item -LiteralPath "docs\WEB-MANAGER.md" `
        -Destination (
            Join-Path $StagedPackage "WEB-MANAGER-README.md"
        )
    Copy-Item -LiteralPath "docs\KWS-WAKE-NOTIFY-API.md" `
        -Destination (
            Join-Path $StagedPackage "KWS-WAKE-NOTIFY-API.md"
        )
    Copy-Item -LiteralPath "docs\G10-C-FIELD-TEST.md" `
        -Destination (
            Join-Path $StagedPackage "G10-C-FIELD-TEST.md"
        )
    Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" `
        -Destination (
            Join-Path $StagedPackage "THIRD_PARTY_NOTICES.md"
        )

    & "scripts\run-verified-demo.ps1"
    if ($LASTEXITCODE -ne 0) {
        throw "Demo generation failed with exit code $LASTEXITCODE"
    }
    $DemoSource = Join-Path $env:TEMP `
        "voice_gateway_p1_demo\demo.wav"
    Copy-Item -LiteralPath $DemoSource `
        -Destination (
            Join-Path $StagedPackage "testdata\demo-nihao-side-ai.wav"
        )

    & (Join-Path $StagedPackage "VoiceGateway-G10C-Test.exe") `
        --config (Join-Path $StagedPackage "config\default.json") `
        doctor
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged doctor failed with exit code $LASTEXITCODE"
    }
    & (Join-Path $StagedPackage "VoiceGateway-G10C-Test.exe") `
        --config (Join-Path $StagedPackage "config\default.json") `
        file (
            Join-Path $StagedPackage `
                "testdata\demo-nihao-side-ai.wav"
        )
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged demo failed with exit code $LASTEXITCODE"
    }

    & "webui\build-web-manager.ps1" `
        -PackageDirectory $StagedPackage
    if ($LASTEXITCODE -ne 0) {
        throw "Web manager build failed with exit code $LASTEXITCODE"
    }
    & (Join-Path $StagedPackage "VoiceGateway-WebUI.exe") `
        --help |
        Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Packaged Web manager smoke test failed with exit code " +
            $LASTEXITCODE
        )
    }

    foreach ($RuntimeDirectory in @(
        "logs",
        "results",
        "runtime",
        "webui-runtime"
    )) {
        Remove-SafeDirectory `
            (Join-Path $StagedPackage $RuntimeDirectory) `
            $StagedPackage
    }

    $Manifest = @(
        Get-ChildItem -LiteralPath $StagedPackage -File -Recurse |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = $_.FullName.Substring(
                        $StagedPackage.Length + 1
                    ).Replace("\", "/")
                    bytes = $_.Length
                    sha256 = (
                        Get-FileHash `
                            -LiteralPath $_.FullName `
                            -Algorithm SHA256
                    ).Hash.ToLowerInvariant()
                }
            }
    )
    $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        (Join-Path $StagedPackage "package-manifest.json"),
        ($Manifest | ConvertTo-Json -Depth 4) +
            [Environment]::NewLine,
        $Utf8WithoutBom
    )

    New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
    Remove-SafeDirectory $PreviousPackage $Artifacts
    $UseInPlaceReplacement = $false
    if (Test-Path -LiteralPath $Package) {
        try {
            Move-Item -LiteralPath $Package `
                -Destination $PreviousPackage
        }
        catch [IO.IOException] {
            # A shell may have the package as its current directory. Windows
            # then refuses to rename the directory even when no EXE is open.
            # The fully validated staging package remains available while its
            # contents are synchronized into the existing directory.
            $UseInPlaceReplacement = $true
        }
    }

    if ($UseInPlaceReplacement) {
        foreach (
            $ExistingItem in Get-ChildItem `
                -LiteralPath $Package `
                -Force
        ) {
            Remove-Item -LiteralPath $ExistingItem.FullName `
                -Recurse `
                -Force
        }
        foreach (
            $StagedItem in Get-ChildItem `
                -LiteralPath $StagedPackage `
                -Force
        ) {
            Copy-Item -LiteralPath $StagedItem.FullName `
                -Destination $Package `
                -Recurse `
                -Force
        }
        Remove-SafeDirectory $StagedPackage $StageRoot
    }
    else {
        try {
            Move-Item -LiteralPath $StagedPackage `
                -Destination $Package
        }
        catch {
            if (
                -not (Test-Path -LiteralPath $Package) -and
                (Test-Path -LiteralPath $PreviousPackage)
            ) {
                Move-Item -LiteralPath $PreviousPackage `
                    -Destination $Package
            }
            throw
        }
        Remove-SafeDirectory $PreviousPackage $Artifacts
    }
    Remove-SafeDirectory $StageRoot (Join-Path $Root "build")

    if ($CreateZip) {
        & "webui\build-deployment-package.ps1" `
            -Version $Version
        if ($LASTEXITCODE -ne 0) {
            throw (
                "Release package build failed with exit code " +
                $LASTEXITCODE
            )
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Complete package: $Package"
