param(
    [string]$MvsRuntimeDir = "C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    [string]$MvsGigEDriverDir = "C:\Program Files (x86)\Common Files\MVS\Drivers\GigE",
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
$runtimeDest = Join-Path $root "runtime\mvs\Win64_x64"
$driverDest = Join-Path $root "drivers\mvs_gige"

if (-not (Test-Path -LiteralPath $MvsRuntimeDir)) {
    throw "未找到 MVS Runtime 目录：$MvsRuntimeDir"
}

Write-Host "[1/2] Staging MVS runtime into Software/runtime..."
Invoke-RobocopySafe -Source $MvsRuntimeDir -Destination $runtimeDest

Write-Host "[2/2] Staging optional GigE driver package..."
if (-not $SkipGigEDriverBundle -and (Test-Path -LiteralPath $MvsGigEDriverDir)) {
    Invoke-RobocopySafe -Source $MvsGigEDriverDir -Destination $driverDest
}
elseif (-not $SkipGigEDriverBundle) {
    Write-Warning "未找到 GigE 驱动目录，跳过复制：$MvsGigEDriverDir"
}

Write-Host ""
Write-Host "Portable runtime staging complete."
Write-Host "You can now copy the entire Software folder to another machine."
Write-Host "If the target machine still needs the vendor GigE network driver, run scripts\\install_mvs_gige_driver.ps1 as administrator."
