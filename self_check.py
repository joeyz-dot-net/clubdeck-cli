#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Startup checks for local real-time transcription."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import clubdeck_config

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None:
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


_IMPORT_NAMES = {
    "requests": "requests",
    "numpy": "numpy",
    "faster-whisper": "faster_whisper",
}


@dataclass
class CheckResult:
    ok: bool
    detail: str = ""


def ensure_requirements(log: Any = None) -> tuple[bool, list[str]]:
    """Install missing Python packages listed in the core requirements file."""
    req = clubdeck_config.PROJECT_DIR / "requirements.txt"
    missing: list[str] = []
    if req.is_file():
        for raw in req.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~]", line.split(";")[0].strip())[0].strip()
            module = _IMPORT_NAMES.get(name)
            if module and importlib.util.find_spec(module) is None:
                missing.append(name)
    if not missing:
        return True, []
    print(f"[依赖] 缺少 {', '.join(missing)}，正在安装…", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req)],
            capture_output=True, text=True, timeout=600,
        )
        ok = result.returncode == 0
        if not ok:
            tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
            print(f"[失败] 依赖安装失败: {tail[-1][:200]}", flush=True)
        else:
            print(f"[完成] 已安装 {', '.join(missing)}", flush=True)
    except Exception as exc:
        ok = False
        print(f"[失败] 依赖安装失败: {exc}", flush=True)
    if log is not None:
        try:
            log(f"[deps] ensure_requirements ok={ok} missing={','.join(missing)}")
        except Exception:
            pass
    return ok, missing


def check_dependencies() -> dict[str, CheckResult]:
    results: dict[str, CheckResult] = {}
    for package, module in _IMPORT_NAMES.items():
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            installed = False
        results[module] = CheckResult(installed, "已安装" if installed else "未安装")
    try:
        import chcli_tui
        chcli_tui.AudioBridge.validate_runtime()
        results["audio_bridge"] = CheckResult(True, "Agora 音频桥可用")
    except Exception as exc:
        results["audio_bridge"] = CheckResult(False, str(exc))
    return results


def run_self_check(config: Any = None, *, full: bool = True, print_progress: bool = True) -> dict[str, CheckResult]:
    del config, full
    results = check_dependencies()
    if print_progress:
        print("本机实时转写检查", flush=True)
        for name, result in results.items():
            label = "通过" if result.ok else "失败"
            print(f"  {name}: {label} — {result.detail}", flush=True)
        if all(result.ok for result in results.values()):
            print("[通过] 实时转写环境就绪", flush=True)
        else:
            print("[失败] 请先修复上述依赖或音频桥配置", flush=True)
    return results


if __name__ == "__main__":
    ensure_requirements()
    report = run_self_check()
    raise SystemExit(0 if all(item.ok for item in report.values()) else 1)
