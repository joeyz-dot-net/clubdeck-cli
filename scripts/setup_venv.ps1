param()

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $repo ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "[setup] 创建仓库虚拟环境: $venv"
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -m venv "$venv"
    } else {
        & python -m venv "$venv"
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Write-Error "[FAIL] 创建虚拟环境失败"
    }
} else {
    Write-Host "[setup] 复用现有虚拟环境: $venv"
}

Write-Host "[setup] 升级 pip ..."
& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Error "[FAIL] pip 升级失败" }

Write-Host "[setup] 安装本机实时转写依赖 ..."
& $venvPy -m pip install -r (Join-Path $repo "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Error "[FAIL] 依赖安装失败" }

$checkCode = @'
import importlib.util
mods = ["requests", "numpy", "faster_whisper"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
print("missing:", missing or "none")
raise SystemExit(1 if missing else 0)
'@
Write-Host "[setup] 校验关键依赖 ..."
$checkPy = Join-Path $env:TEMP "clubdeck_venv_check.py"
[System.IO.File]::WriteAllText($checkPy, $checkCode, [System.Text.UTF8Encoding]::new($false))
try {
    & $venvPy $checkPy
    if ($LASTEXITCODE -ne 0) { Write-Error "[FAIL] 依赖校验失败" }
} finally {
    Remove-Item -LiteralPath $checkPy -ErrorAction SilentlyContinue
}

Write-Host "[PASS] 仓库虚拟环境就绪: $venvPy"
Write-Host "[PASS] 启动入口: start_clubdeck.cmd"
