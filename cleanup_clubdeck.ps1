# cleanup_clubdeck.ps1 - Stop stale processes of this project before startup.
#
# Only touches processes whose command line lives under this project directory
# (so the Clubdeck desktop app and other projects are never touched).
#
# Scopes (per-tool single instance; tools can run side by side):
#   all       - stop every session/bridge of this project (stop_clubdeck.cmd)
#   tui       - stop the text client (chcli_tui.py) + its audio bridge
#   orphans   - stop ONLY bridges/mpv whose launcher process is gone;
#               never touches live sessions (safe to run on demand)
#
# Stale audio bridges whose parent process is gone are always cleaned,
# regardless of scope, because no live session depends on them.
#
# Usage:
#   cleanup_clubdeck.ps1 [-Scope all|tui|orphans] [-DryRun]
param(
    [ValidateSet('all', 'tui', 'orphans')]
    [string]$Scope = 'all',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot.TrimEnd('\')

# entry script -> tool group
$GroupEntries = [ordered]@{
    'tui'        = @('chcli_tui.py')
}

function Get-EntryGroup {
    param([string]$CommandLine)
    foreach ($group in $GroupEntries.Keys) {
        foreach ($entry in $GroupEntries[$group]) {
            if ($CommandLine -like "*$entry*") {
                return $group
            }
        }
    }
    return $null
}

# CIM 枚举在本机偶发失败：最多重试 3 次
$all = @()
for ($attempt = 0; $attempt -lt 3 -and $all.Count -eq 0; $attempt++) {
    try {
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        $all = @()
    }
    if ($all.Count -eq 0 -and $attempt -lt 2) {
        Start-Sleep -Milliseconds 400
    }
}
$byId = @{}
foreach ($p in $all) {
    $byId[[int]$p.ProcessId] = $p
}

$procs = $all | Where-Object {
    $_.Name -in @('python.exe', 'node.exe', 'electron.exe', 'mpv.exe', 'mpv.com') -and
    $_.CommandLine -and
    $_.CommandLine -like "*$ProjectDir\*"
}

$targets = @{}  # ProcessId -> process
foreach ($p in $procs) {
    if ($p.Name -eq 'python.exe') {
        if ($Scope -eq 'orphans') {
            continue
        }
        $group = Get-EntryGroup ([string]$p.CommandLine)
        if ($Scope -eq 'all') {
            $targets[[int]$p.ProcessId] = $p
        }
        elseif ($group -and $group -eq $Scope) {
            $targets[[int]$p.ProcessId] = $p
        }
    }
    elseif ($p.Name -in @('node.exe', 'electron.exe') -and $p.CommandLine -like '*agora_bridge.js*') {
        $parent = $null
        if ($byId.ContainsKey([int]$p.ParentProcessId)) {
            $parent = $byId[[int]$p.ParentProcessId]
        }
        if (-not $parent) {
            # orphan bridge (its launcher is gone): stale, always clean
            $targets[[int]$p.ProcessId] = $p
            continue
        }
        if ($parent.Name -eq 'python.exe') {
            $group = Get-EntryGroup ([string]$parent.CommandLine)
            if ($Scope -eq 'all' -or ($group -and $group -eq $Scope)) {
                $targets[[int]$p.ProcessId] = $p
            }
        }
    }
    elseif ($p.Name -in @('mpv.exe', 'mpv.com')) {
        $parent = $null
        if ($byId.ContainsKey([int]$p.ParentProcessId)) {
            $parent = $byId[[int]$p.ParentProcessId]
        }
        if (-not $parent) {
            # orphan mpv (its launcher is gone): stale, always clean
            $targets[[int]$p.ProcessId] = $p
            continue
        }
        if ($parent.Name -eq 'python.exe') {
            $group = Get-EntryGroup ([string]$parent.CommandLine)
            if ($Scope -eq 'all' -or ($group -and $group -eq $Scope)) {
                $targets[[int]$p.ProcessId] = $p
            }
        }
    }
}

# also stop child processes of the python instances being stopped
# (audio bridge and any descendants of the TUI process).
$childMap = @{}
foreach ($p in $all) {
    if ($p.ParentProcessId -and $p.ProcessId -ne $p.ParentProcessId) {
        $ppid = [int]$p.ParentProcessId
        if (-not $childMap.ContainsKey($ppid)) {
            $childMap[$ppid] = @()
        }
        $childMap[$ppid] += $p
    }
}

function Get-Descendants {
    param([int]$RootId)
    $found = @()
    if ($childMap.ContainsKey($RootId)) {
        foreach ($child in $childMap[$RootId]) {
            $found += $child
            $found += Get-Descendants ([int]$child.ProcessId)
        }
    }
    return $found
}

foreach ($p in $procs) {
    if ($p.Name -eq 'python.exe' -and $targets.ContainsKey([int]$p.ProcessId)) {
        foreach ($descendant in (Get-Descendants ([int]$p.ProcessId))) {
            $targets[[int]$descendant.ProcessId] = $descendant
        }
    }
}

$targetList = @($targets.Values)
if ($targetList.Count -eq 0) {
    if (-not $DryRun) {
        # 音频桥只可能是本项目 electron.exe/node.exe 子进程，绝不全杀 Clubdeck.exe
        Write-Host 'No stale processes found for this project.'
    }
    exit 0
}

Write-Host "Found $($targetList.Count) stale process(es) for this project (scope: $Scope):"
foreach ($p in ($targetList | Sort-Object Name, ProcessId)) {
    Write-Host ("  [{0}] PID {1}  {2}" -f $p.Name, $p.ProcessId, $p.CommandLine)
}

if ($DryRun) {
    Write-Host '(DryRun: listed above only, nothing was stopped.)'
    exit 0
}

foreach ($p in ($targetList | Sort-Object { $_.Name -ne 'python.exe' })) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "  Stopped PID $($p.ProcessId)"
    }
    catch {
        Write-Warning "  Stop-Process failed for PID $($p.ProcessId): $($_.Exception.Message)"
        try {
            taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Host
            if (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue) {
                Write-Warning "  PID $($p.ProcessId) still alive after taskkill (may need an elevated shell)"
            }
            else {
                Write-Host "  Stopped PID $($p.ProcessId) via taskkill"
            }
        }
        catch {
            Write-Warning "  taskkill also failed for PID $($p.ProcessId): $($_.Exception.Message)"
        }
    }
}
Start-Sleep -Milliseconds 500
Write-Host 'Cleanup finished, you can start a new instance.'
