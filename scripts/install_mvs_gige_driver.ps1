param(
    [string]$DriverRoot = $(Join-Path (Split-Path -Parent $PSScriptRoot) "drivers\mvs_gige")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $DriverRoot)) {
    throw "未找到 GigE 驱动目录：$DriverRoot"
}

$installerExe = Join-Path $DriverRoot "GigEInst.exe"
$installerBat = Join-Path $DriverRoot "install.bat"

if (Test-Path -LiteralPath $installerExe) {
    Write-Host "正在以管理员权限启动 GigE 驱动安装程序..."
    Start-Process -FilePath $installerExe -Verb RunAs -Wait
}
elseif (Test-Path -LiteralPath $installerBat) {
    Write-Host "正在以管理员权限启动 GigE 驱动安装脚本..."
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ('"' + $installerBat + '"') -Verb RunAs -Wait
}
else {
    throw "驱动目录中未找到 GigEInst.exe 或 install.bat。"
}

Write-Host "GigE 驱动安装流程已结束。"
