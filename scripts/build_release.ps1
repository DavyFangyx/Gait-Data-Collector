param(
    [string]$PythonExe = "python",
    [string]$MvsRuntimeDir = "C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    [string]$MvsGigEDriverDir = "C:\Program Files (x86)\Common Files\MVS\Drivers\GigE",
    [switch]$SkipMvsRuntimeBundle,
    [switch]$SkipGigEDriverBundle
)

$ErrorActionPreference = "Stop"

function Invoke-RobocopySafe {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    robocopy $Source $Destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "拷贝失败：$Source -> $Destination"
    }
}

$root = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $root "dist"
$appName = "ISCamRecorder-Python"
$entry = Join-Path $root "run_python_app.py"
$releaseDir = Join-Path $distDir $appName
$zipName = "{0}-{1}.zip" -f $appName, (Get-Date -Format "yyyyMMdd-HHmmss")
$zipPath = Join-Path $distDir $zipName
$runtimeDest = Join-Path $releaseDir "runtime\mvs\Win64_x64"
$driverDest = Join-Path $releaseDir "drivers\mvs_gige"
$driverHelper = Join-Path $root "scripts\install_mvs_gige_driver.ps1"
$localRuntimeDir = Join-Path $root "runtime\mvs\Win64_x64"
$localGigEDriverDir = Join-Path $root "drivers\mvs_gige"
$settingsFile = Join-Path $root "settings.json"
$subjectListFile = Join-Path $root "SubjectList.txt"
if (-not (Test-Path -LiteralPath $settingsFile)) {
    $settingsFile = Join-Path $root "settings.example.json"
}
if (-not (Test-Path -LiteralPath $subjectListFile)) {
    $subjectListFile = Join-Path $root "SubjectList.example.txt"
}

Write-Host "[1/5] Installing packaging tools..."
& $PythonExe -m pip install pyinstaller

Write-Host "[2/5] Building executable..."
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name $appName `
    --paths $root `
    --collect-all PyQt5 `
    --collect-all av `
    --collect-submodules cv2 `
    --hidden-import numpy `
    --collect-all scipy `
    --collect-all matplotlib `
    --hidden-import psutil `
    --hidden-import serial `
    --add-data "$root\python_app\assets;python_app\assets" `
    --add-data "$root\foot_sensor_layout;foot_sensor_layout" `
    --add-data "$settingsFile;." `
    --add-data "$subjectListFile;." `
    $entry

if (-not (Test-Path -LiteralPath (Join-Path $releaseDir "settings.json")) -and (Test-Path -LiteralPath (Join-Path $releaseDir "settings.example.json"))) {
    Copy-Item -LiteralPath (Join-Path $releaseDir "settings.example.json") -Destination (Join-Path $releaseDir "settings.json") -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $releaseDir "SubjectList.txt")) -and (Test-Path -LiteralPath (Join-Path $releaseDir "SubjectList.example.txt"))) {
    Copy-Item -LiteralPath (Join-Path $releaseDir "SubjectList.example.txt") -Destination (Join-Path $releaseDir "SubjectList.txt") -Force
}

Write-Host "[3/5] Copying documentation and optional helper scripts..."
Copy-Item -LiteralPath `
    (Join-Path $root "README.md"), `
    (Join-Path $root "README.zh-CN.md"), `
    (Join-Path $root "requirements.txt"), `
    $driverHelper `
    -Destination $releaseDir -Force

Write-Host "[4/5] Bundling MVS runtime and optional GigE driver package..."
if (-not $SkipMvsRuntimeBundle) {
    $runtimeSource = if (Test-Path -LiteralPath $localRuntimeDir) { $localRuntimeDir } else { $MvsRuntimeDir }
    if (-not (Test-Path -LiteralPath $runtimeSource)) {
        throw "未找到可用的 MVS Runtime 目录：$runtimeSource"
    }
    Invoke-RobocopySafe -Source $runtimeSource -Destination $runtimeDest
}

if (-not $SkipGigEDriverBundle) {
    $driverSource = if (Test-Path -LiteralPath $localGigEDriverDir) { $localGigEDriverDir } else { $MvsGigEDriverDir }
    if (Test-Path -LiteralPath $driverSource) {
        Invoke-RobocopySafe -Source $driverSource -Destination $driverDest
    }
    else {
        Write-Warning "未找到 GigE 驱动目录，跳过驱动安装包复制：$driverSource"
    }
}

Write-Host "[5/5] Creating zip package..."
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Build complete."
Write-Host "Release folder: $releaseDir"
Write-Host "Zip package:    $zipPath"
Write-Host ""
if ($SkipMvsRuntimeBundle) {
    Write-Host "MVS runtime bundle: skipped"
}
else {
    Write-Host "MVS runtime bundle: included"
}
if ($SkipGigEDriverBundle) {
    Write-Host "GigE driver bundle: skipped"
}
elseif (Test-Path -LiteralPath $driverDest) {
    Write-Host "GigE driver bundle: included"
}
else {
    Write-Host "GigE driver bundle: not found on build machine"
}
Write-Host ""
Write-Host "Target machine note:"
Write-Host "- Full Hikrobot MVS Client is no longer required for this packaged release."
Write-Host "- If GigE discovery/performance still needs the vendor network driver, run install_mvs_gige_driver.ps1 as administrator on the target machine."
