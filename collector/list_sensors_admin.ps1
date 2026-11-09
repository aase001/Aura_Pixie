<#
.SYNOPSIS
    以管理员权限导出 hwcollector 的完整传感器清单，用于标定 monitor_hub.py 的指标映射表。

.DESCRIPTION
    非管理员下，LibreHardwareMonitor 不会枚举存储设备，且 CPU 温度 / CPU 功耗 / CPU 频率
    全部为空。映射表里这几组指标只能靠管理员视角的清单标定，因此需要本脚本。

    首次运行会自动申请一次 UAC 提权，并由提权后的子进程写出结果；父进程写完即退出，
    绝不再用自己（非管理员）的输出覆盖结果文件。
#>
param([switch]$Elevated)

$ErrorActionPreference = 'Stop'

$exe  = Join-Path $PSScriptRoot 'pub\hwcollector.exe'
$list = Join-Path $env:TEMP 'lhm_list_admin.txt'
$meta = Join-Path $env:TEMP 'lhm_meta_admin.json'

if (-not (Test-Path -LiteralPath $exe)) {
    Write-Host "未找到 $exe ，请先在 collector 目录执行：dotnet publish -c Release -o pub" -ForegroundColor Red
    exit 1
}

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host '申请管理员权限（会弹一次 UAC）…' -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Elevated'
    )
    exit 0   # 关键：结果由提权子进程写出，父进程必须立即退出，不能覆盖
}

# ---- 到这里已经是管理员上下文 ----
& $exe --list | Set-Content -LiteralPath $list -Encoding UTF8
& $exe --meta | Set-Content -LiteralPath $meta -Encoding UTF8

Write-Host "已以管理员身份导出：" -ForegroundColor Green
Write-Host "  $list"
Write-Host "  $meta"
