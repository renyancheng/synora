param(
    [string]$LanBaseUrl = "http://10.74.201.232:8000",
    [string]$ServerBaseUrl = "http://101.43.52.61:8000",
    [switch]$NoSplitPerAbi
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $repoRoot "app\synora"
$outputDir = Join-Path $appDir "build\dual-apk"

function Invoke-Build([string]$baseUrl, [string]$label) {
    Write-Host "==> Building $label APKs with base URL: $baseUrl"

    $buildArgs = @(
        "build", "apk", "--release",
        "--dart-define=SYNORA_API_BASE_URL=$baseUrl"
    )

    if (-not $NoSplitPerAbi) {
        $buildArgs += "--split-per-abi"
    }

    Push-Location $appDir
    try {
        & flutter @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "flutter build failed for $label"
        }
    }
    finally {
        Pop-Location
    }

    $apkSourceDir = Join-Path $appDir "build\app\outputs\flutter-apk"
    $targetDir = Join-Path $outputDir $label
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    if ($NoSplitPerAbi) {
        $sourceFile = Join-Path $apkSourceDir "app-release.apk"
        $targetFile = Join-Path $targetDir "synora-$label-release.apk"
        Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
        return
    }

    $abiFiles = @(
        "app-armeabi-v7a-release.apk",
        "app-arm64-v8a-release.apk",
        "app-x86_64-release.apk"
    )

    foreach ($fileName in $abiFiles) {
        $sourceFile = Join-Path $apkSourceDir $fileName
        if (-not (Test-Path $sourceFile)) {
            throw "Missing APK output: $sourceFile"
        }
        $targetFile = Join-Path $targetDir ($fileName -replace "^app-", "synora-$label-")
        Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
    }
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

Invoke-Build -baseUrl $LanBaseUrl -label "lan"
Invoke-Build -baseUrl $ServerBaseUrl -label "server"

Write-Host ""
Write-Host "Build finished."
Write-Host "LAN APKs:    $($outputDir)\lan"
Write-Host "Server APKs: $($outputDir)\server"
