<#
.SYNOPSIS
    监控 chcli_tui.py 长跑健康（屏幕不刷新问题回归看护）。

.DESCRIPTION
    周期性采样 TUI 进程的内存 / CPU / 线程数和音频桥子进程，
    追加写入日志。TUI 进程重启后会自动重新跟踪最新实例，适合挂后台跑几小时。

.EXAMPLE
    .\scripts\monitor_tui_health.ps1 -IntervalSeconds 300 -LogFile logs\tui-health.log
    每 5 分钟采样一次，直到 Ctrl+C 或进程结束。

.EXAMPLE
    .\scripts\monitor_tui_health.ps1 -ProcessId 15476 -Count 3
    只采样指定 PID 3 次。
#>
param(
    [int]$ProcessId = 0,
    [int]$IntervalSeconds = 300,
    [string]$LogFile = "",
    [int]$Count = 0
)

$ErrorActionPreference = "SilentlyContinue"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $LogFile) {
    $LogFile = Join-Path $repo "logs\tui-health.log"
}
$logDir = Split-Path -Parent $LogFile
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Resolve-TuiPid {
    $procs = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*chcli_tui.py*' } |
        Sort-Object CreationDate -Descending
    return @($procs | Select-Object -First 1 -ExpandProperty ProcessId)
}

function New-SampleLine([int]$targetPid) {
    $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if (-not $proc) {
        return "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),$targetPid,gone,-,-,-,-,-"
    }
    $bridges = @(Get-CimInstance Win32_Process |
        Where-Object { $_.ParentProcessId -eq $targetPid -and $_.Name -in @('node.exe', 'electron.exe') -and $_.CommandLine -like '*agora_bridge.js*' })
    $room = "?"
    if ($bridges.Count -gt 0 -and $bridges[0].CommandLine -match 'agora_bridge\.js \S+ (\S+)') {
        $room = $matches[1]
    }
    return ("{0},{1},{2},{3},{4},{5},{6},{7}" -f (
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),
        $targetPid,
        [math]::Round($proc.WorkingSet64 / 1MB, 1),
        [math]::Round($proc.PrivateMemorySize64 / 1MB, 1),
        [math]::Round($proc.CPU, 1),
        $proc.Threads.Count,
        $bridges.Count,
        $room
    ))
}

if (-not (Test-Path $LogFile)) {
    "time,pid,state,ws_mb,priv_mb,cpu_s,threads,bridges,room" | Out-File $LogFile -Encoding utf8
}

$tracked = $ProcessId
$samples = 0
Write-Host "monitor started: log=$LogFile interval=${IntervalSeconds}s count=$Count"
try {
    while ($true) {
        if (-not $tracked -or -not (Get-Process -Id $tracked -ErrorAction SilentlyContinue)) {
            $tracked = Resolve-TuiPid
            if (-not $tracked) {
                "monitor: no chcli_tui.py process found" | Out-File $LogFile -Append -Encoding utf8
                Write-Host "no chcli_tui.py running, waiting..."
            } else {
                "monitor: re-tracked pid $tracked" | Out-File $LogFile -Append -Encoding utf8
                Write-Host "tracking pid $tracked"
            }
        }
        if ($tracked) {
            $line = New-SampleLine $tracked
            $line | Out-File $LogFile -Append -Encoding utf8
            Write-Host $line
        }
        $samples++
        if ($Count -gt 0 -and $samples -ge $Count) { break }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    "monitor: stopped at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $LogFile -Append -Encoding utf8
}
