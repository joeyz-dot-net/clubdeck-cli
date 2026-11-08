# -*- coding: utf-8 -*-
"""跨进程单实例锁（本机实时转写界面使用）。

启动入口（runtime_bootstrap.py）在完整会话期间持有文件锁，
保证同一项目同一时刻只运行一个转写界面。文件锁由操作系统在进程退出/崩溃时自动释放，不会残留脏锁。

用法:
    try:
        with launch_guard(Path("data/runtime-launch.lock")):
            ...  # 整个交互会话
    except LaunchAlreadyRunning:
        print("已在运行，本次不重复启动")
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


class LaunchAlreadyRunning(RuntimeError):
    """Raised when another process still holds the single-instance lock."""


@contextlib.contextmanager
def launch_guard(lock_path: Path):
    """Hold a cross-process lock for the complete interactive session."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise LaunchAlreadyRunning from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
