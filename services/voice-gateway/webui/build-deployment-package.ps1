param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$webUiDirectory = $PSScriptRoot
$projectRoot = Split-Path -Parent $webUiDirectory
$runtimePackageRoot = Join-Path $projectRoot `
    "artifacts\VoiceGateway-G10C-Test"

if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionLine = Select-String `
        -LiteralPath (Join-Path $projectRoot "pyproject.toml") `
        -Pattern '^version\s*=\s*"([^"]+)"$' |
        Select-Object -First 1
    if (-not $versionLine) {
        throw "Unable to determine the project version from pyproject.toml."
    }
    $Version = $versionLine.Matches[0].Groups[1].Value
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Release version must use numeric x.y.z format: $Version"
}

$packageName = "VoiceGateway-G10C-WebUI-v$Version-win64"
$deployDirectory = Join-Path $projectRoot "artifacts\releases"
$buildDirectory = Join-Path $projectRoot "build\release"
$stageDirectory = Join-Path $buildDirectory "stage"
$packageDirectory = Join-Path $stageDirectory $packageName
$zipPath = Join-Path $deployDirectory "$packageName.zip"
$externalManifestPath = Join-Path $deployDirectory "VoiceGateway-G10C-WebUI-v$Version-manifest.json"
$temporaryToken = [Guid]::NewGuid().ToString("N")
$temporaryZipPath = Join-Path $deployDirectory ".$packageName.$temporaryToken.zip.tmp"
$temporaryManifestPath = Join-Path $deployDirectory ".VoiceGateway-G10C-WebUI-v$Version-manifest.$temporaryToken.json.tmp"
$internalManifestPath = Join-Path $packageDirectory "package-manifest-v$Version.json"
$cleanConfigPath = Join-Path $projectRoot "config\default.json"

$resolvedProjectRoot = [IO.Path]::GetFullPath($projectRoot).TrimEnd("\")
$resolvedBuildDirectory = [IO.Path]::GetFullPath($buildDirectory).TrimEnd("\")
$resolvedStageDirectory = [IO.Path]::GetFullPath($stageDirectory)
$resolvedPackageDirectory = [IO.Path]::GetFullPath($packageDirectory)
$resolvedDeployDirectory = [IO.Path]::GetFullPath(
    $deployDirectory
).TrimEnd("\") + "\"

if (-not $resolvedStageDirectory.StartsWith($resolvedBuildDirectory + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a staging directory outside webui\build."
}
if (-not $resolvedPackageDirectory.StartsWith($resolvedStageDirectory.TrimEnd("\") + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a package directory outside the staging directory."
}
if (-not (Test-Path -LiteralPath $cleanConfigPath -PathType Leaf)) {
    throw "Clean deployment config not found: $cleanConfigPath"
}
if (-not (Test-Path -LiteralPath $runtimePackageRoot -PathType Container)) {
    throw "Runtime package not found: $runtimePackageRoot"
}
foreach ($releasePath in @(
    $zipPath,
    $externalManifestPath,
    $temporaryZipPath,
    $temporaryManifestPath
)) {
    $resolvedReleasePath = [IO.Path]::GetFullPath($releasePath)
    if (-not $resolvedReleasePath.StartsWith(
        $resolvedDeployDirectory,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to publish outside the releases directory."
    }
}

function Publish-AtomicFile(
    [string]$TemporaryPath,
    [string]$FinalPath
) {
    if (Test-Path -LiteralPath $FinalPath -PathType Leaf) {
        $backupPath = (
            $FinalPath +
            "." +
            [Guid]::NewGuid().ToString("N") +
            ".replace-backup.tmp"
        )
        try {
            [IO.File]::Replace(
                $TemporaryPath,
                $FinalPath,
                $backupPath
            )
        }
        finally {
            if (Test-Path -LiteralPath $backupPath) {
                Remove-Item `
                    -LiteralPath $backupPath `
                    -Force `
                    -ErrorAction SilentlyContinue
            }
        }
    }
    else {
        [IO.File]::Move($TemporaryPath, $FinalPath)
    }
}

New-Item -ItemType Directory -Force -Path $deployDirectory | Out-Null

if (Test-Path -LiteralPath $stageDirectory) {
    Remove-Item -LiteralPath $stageDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stageDirectory | Out-Null

New-Item -ItemType Directory -Force -Path $packageDirectory | Out-Null

$topLevelFiles = @(
    "01-doctor.cmd",
    "02-probe-mic.cmd",
    "03-run-live.cmd",
    "04-offline-demo.cmd",
    "05-web-manager.cmd",
    "06-enable-lan-access.cmd",
    "README.md",
    "WEB-MANAGER-README.md",
    "KWS-WAKE-NOTIFY-API.md",
    "THIRD_PARTY_NOTICES.md",
    "VoiceGateway-G10C-Test.exe",
    "VoiceGateway-WebUI.exe"
)

foreach ($relativePath in $topLevelFiles) {
    $sourcePath = Join-Path $runtimePackageRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required package file is missing: $relativePath"
    }
    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $packageDirectory $relativePath)
}

$guideFiles = @(
    Get-ChildItem -LiteralPath $runtimePackageRoot `
        -File `
        -Filter "G10-C-*.md"
)
if ($guideFiles.Count -ne 1) {
    throw "Expected exactly one G10-C guide file, found $($guideFiles.Count)."
}
Copy-Item -LiteralPath $guideFiles[0].FullName -Destination (Join-Path $packageDirectory $guideFiles[0].Name)

$directories = @(
    "_internal",
    "models",
    "testdata",
    "webui\bin",
    "webui\static"
)

foreach ($relativePath in $directories) {
    $sourcePath = Join-Path $runtimePackageRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Required package directory is missing: $relativePath"
    }
    $destinationPath = Join-Path $packageDirectory $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinationPath) | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse
}

New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory "config") | Out-Null
Copy-Item -LiteralPath $cleanConfigPath -Destination (Join-Path $packageDirectory "config\default.json")

$manifestEntries = @(
    Get-ChildItem -LiteralPath $packageDirectory -File -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($resolvedPackageDirectory.TrimEnd("\").Length + 1).Replace("\", "/")
            [ordered]@{
                path = $relativePath
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)

$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
$manifestJson = $manifestEntries | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText($internalManifestPath, $manifestJson + [Environment]::NewLine, $utf8WithoutBom)

try {
    Copy-Item `
        -LiteralPath $internalManifestPath `
        -Destination $temporaryManifestPath
    Add-Type -AssemblyName System.IO.Compression
    $zipStream = [IO.File]::Open(
        $temporaryZipPath,
        [IO.FileMode]::CreateNew
    )
    $archive = $null
    try {
        $archive = New-Object IO.Compression.ZipArchive(
            $zipStream,
            [IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        foreach ($file in Get-ChildItem -LiteralPath $packageDirectory -File -Recurse | Sort-Object FullName) {
            $entryName = $file.FullName.Substring($resolvedPackageDirectory.TrimEnd("\").Length + 1).Replace("\", "/")
            $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = $file.LastWriteTime
            $sourceStream = $null
            $entryStream = $null
            try {
                $sourceStream = [IO.File]::OpenRead($file.FullName)
                $entryStream = $entry.Open()
                $sourceStream.CopyTo($entryStream)
            }
            finally {
                if ($null -ne $entryStream) {
                    $entryStream.Dispose()
                }
                if ($null -ne $sourceStream) {
                    $sourceStream.Dispose()
                }
            }
        }
    }
    finally {
        if ($null -ne $archive) {
            $archive.Dispose()
        }
        else {
            $zipStream.Dispose()
        }
    }

    Publish-AtomicFile $temporaryZipPath $zipPath
    Publish-AtomicFile $temporaryManifestPath $externalManifestPath
}
finally {
    foreach ($temporaryPath in @(
        $temporaryZipPath,
        $temporaryManifestPath
    )) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

$zipInfo = Get-Item -LiteralPath $zipPath
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()

$result = [pscustomobject]@{
    Package = $zipInfo.FullName
    Manifest = (Get-Item -LiteralPath $externalManifestPath).FullName
    FilesInManifest = $manifestEntries.Count
    Bytes = $zipInfo.Length
    MiB = [math]::Round($zipInfo.Length / 1MB, 2)
    SHA256 = $zipHash
}

Remove-Item -LiteralPath $stageDirectory -Recurse -Force

$result
