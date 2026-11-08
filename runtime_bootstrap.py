#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local single-instance launcher for ClubDeck real-time transcription."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from single_instance import LaunchAlreadyRunning, launch_guard


PROJECT_DIR = Path(__file__).resolve().parent
LAUNCH_LOCK = PROJECT_DIR / "data" / "runtime-launch.lock"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动本机 ClubDeck 实时转写")
    parser.add_argument("--no-self-check", action="store_true", help="跳过启动检查")
    parser.add_argument("--check", action="store_true", help="检查本机转写依赖和音频桥")
    return parser


def _run(args: argparse.Namespace, tui_args: list[str]) -> int:
    import self_check

    if args.check:
        self_check.ensure_requirements()
        report = self_check.run_self_check()
        return 0 if all(item.ok for item in report.values()) else 2

    self_check.ensure_requirements()
    child_env = os.environ.copy()
    if args.no_self_check:
        child_env["CH_STARTUP_SELF_CHECK"] = "0"
    try:
        import chcli_tui
        chcli_tui.AudioBridge.validate_runtime()
    except Exception as exc:
        print(f"[失败] 本机音频桥不可用: {exc}", flush=True)
        return 2

    command = [sys.executable, str(PROJECT_DIR / "chcli_tui.py"), *tui_args]
    try:
        return subprocess.call(command, cwd=PROJECT_DIR, env=child_env)
    except KeyboardInterrupt:
        return 130


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, tui_args = parser.parse_known_args(argv)
    try:
        with launch_guard(LAUNCH_LOCK):
            return _run(args, tui_args)
    except LaunchAlreadyRunning:
        print("[信息] ClubDeck 已在运行，请使用现有窗口。", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
