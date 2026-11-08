<#
.SYNOPSIS
    落地「脱离 Clubdeck 应用」的独立音频桥运行时(Windows)。

.DESCRIPTION
    1. 从已安装的 Clubdeck 复制 agora-electron-sdk 到 third_party\agora-electron-sdk;
    2. 下载裸 Electron 12(ABI 87,与 Clubdeck 内置 Node ABI 一致)到 third_party\electron12;
    3. 把 bridge_runtime + agora_sdk_dir 写入 clubdeck.config.json(BOM-free UTF-8,保留其它键);
    4. 校验 electron 的 process.versions.modules == 87 且 addon 存在。

    为什么不是 Node 14:Electron 12 内置 Node 14.16 但 ABI 是 87;独立 Node 14 是 ABI 83,
    加载不了 agora_node_ext.node。独立运行时必须是 ABI 87 的 Electron 12。

.PARAMETER DryRun
    只打印将要执行的动作,不下载/复制/写配置。

.PARAMETER Force
    已存在 third_party 目标时重新复制/下载覆盖。

.EXAMPLE
    .\scripts\setup_standalone_bridge.ps1 -DryRun
    .\scripts\setup_standalone_bridge.ps1
#>
param(
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $Repo 'clubdeck.config.json'
$ThirdParty = Join-Path $Repo 'third_party'
$SdkTarget = Join-Path $ThirdParty 'agora-electron-sdk'
$ElectronDir = Join-Path $ThirdParty 'electron12'
$ElectronExe = Join-Path $ElectronDir 'electron.exe'

$ElectronVersion = 'v12.2.3'
$ElectronUrl = "https://github.com/electron/electron/releases/download/$ElectronVersion/electron-$ElectronVersion-win32-x64.zip"
$ElectronZip = Join-Path $env:TEMP "electron-$ElectronVersion-win32-x64.zip"

function Write-Step {
    param([string]$Message)
    Write-Host "[setup] $Message"
}

# ---- 1. SDK：优先用项目内置 third_party/agora-electron-sdk ----
if ((Test-Path -LiteralPath (Join-Path $SdkTarget 'build\Release\agora_node_ext.node')) -and -not $Force) {
    Write-Step "SDK 已内置: $SdkTarget（无需 Clubdeck.exe）"
    $skipSdkCopy = $true
}
else {
    $skipSdkCopy = $false
}

# ---- 1b. 仅当内置 SDK 缺失时，才从旧 Clubdeck 应用抽取（legacy 兜底） ----
$candidates = @()
if ($env:CLUBDECK_EXE -and (Test-Path -LiteralPath $env:CLUBDECK_EXE)) {
    $candidates += $env:CLUBDECK_EXE
}
$default = Join-Path $env:LOCALAPPDATA 'Programs\Clubdeck\Clubdeck.exe'
if (Test-Path -LiteralPath $default) {
    $candidates += $default
}
$clubdeck = $candidates | Select-Object -First 1
if (-not $clubdeck) {
    if ($skipSdkCopy) {
        Write-Step "内置 SDK 已存在，跳过 Clubdeck 抽取。"
    }
    else {
        throw "内置 SDK 缺失且未找到 Clubdeck.exe；请复制 agora-electron-sdk 到 third_party\agora-electron-sdk 或设置 CLUBDECK_EXE。"
    }
}
else {
    $sdkSource = Join-Path (Split-Path -Parent $clubdeck) 'resources\app.asar.unpacked\node_modules\agora-electron-sdk'
    if (-not (Test-Path -LiteralPath (Join-Path $sdkSource 'build\Release\agora_node_ext.node'))) {
        throw "Clubdeck 的 SDK 目录缺失: $sdkSource"
    }
    Write-Step "Clubdeck(legacy 抽取): $clubdeck"
    Write-Step "SDK 源 : $sdkSource"
}

# ---- 2. copy SDK ----
if ($skipSdkCopy) {
    # SDK 已内置，无需复制。
}
elseif ((Test-Path -LiteralPath $SdkTarget) -and -not $Force) {
    Write-Step "SDK 已存在: $SdkTarget(用 -Force 重新复制)"
}
else {
    if ($DryRun) {
        Write-Step "[DryRun] 将复制 SDK 到 $SdkTarget"
    }
    else {
        if (Test-Path -LiteralPath $SdkTarget) {
            Remove-Item -LiteralPath $SdkTarget -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $ThirdParty | Out-Null
        Copy-Item -LiteralPath $sdkSource -Destination $SdkTarget -Recurse -Force
        Write-Step "SDK 已复制: $SdkTarget"
    }
}

# ---- 3. download / extract bare Electron 12 ----
if ((Test-Path -LiteralPath $ElectronExe) -and -not $Force) {
    Write-Step "Electron 12 已存在: $ElectronExe(用 -Force 重新下载)"
}
else {
    if ($DryRun) {
        Write-Step "[DryRun] 将下载 $ElectronUrl"
        Write-Step "[DryRun] 将解压到 $ElectronDir"
    }
    else {
        Write-Step "下载 Electron 12: $ElectronUrl"
        Invoke-WebRequest -Uri $ElectronUrl -OutFile $ElectronZip -UseBasicParsing
        if (Test-Path -LiteralPath $ElectronDir) {
            Remove-Item -LiteralPath $ElectronDir -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $ElectronDir | Out-Null
        Expand-Archive -LiteralPath $ElectronZip -DestinationPath $ElectronDir -Force
        Remove-Item -LiteralPath $ElectronZip -Force -ErrorAction SilentlyContinue
        Write-Step "Electron 12 已解压: $ElectronDir"
    }
}

# ---- 4. verify ABI ----
$abi = $null
if (-not $DryRun -and (Test-Path -LiteralPath $ElectronExe)) {
    $env:ELECTRON_RUN_AS_NODE = '1'
    try {
        $abi = (& $ElectronExe -e "console.log(process.versions.modules)" 2>$null | Select-Object -First 1).Trim()
    }
    finally {
        Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    }
    Write-Step "Electron ABI: $abi(期望 87)"
    if ($abi -ne '87') {
        throw "Electron ABI 不是 87($abi),加载 addon 会失败。"
    }
    $addon = Join-Path $SdkTarget 'build\Release\agora_node_ext.node'
    if (-not (Test-Path -LiteralPath $addon)) {
        throw "addon 缺失: $addon"
    }
    Write-Step "addon 就绪: $addon"
}

# ---- 5. write config (BOM-free UTF-8, preserve other keys) ----
$cfg = $null
if (Test-Path -LiteralPath $ConfigPath) {
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
if (-not $cfg) {
    $cfg = [PSCustomObject]@{ tui = [PSCustomObject]@{} }
}
if (-not $cfg.PSObject.Properties['tui']) {
    $cfg | Add-Member -NotePropertyName tui -NotePropertyValue ([PSCustomObject]@{})
}
if ($DryRun) {
    Write-Step "[DryRun] 将写入配置: bridge_runtime=$ElectronExe, agora_sdk_dir=$SdkTarget"
}
else {
    $cfg.tui | Add-Member -NotePropertyName bridge_runtime -NotePropertyValue $ElectronExe -Force
    $cfg.tui | Add-Member -NotePropertyName agora_sdk_dir -NotePropertyValue $SdkTarget -Force
    $json = $cfg | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Step "配置已写入: $ConfigPath"
    Write-Step "完成。建议复核: python -c 打印 chcli_tui.AudioBridge.BRIDGE_RUNTIME 与 AGORA_SDK_DIR。"
}
