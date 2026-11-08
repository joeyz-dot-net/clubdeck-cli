# -*- coding: utf-8 -*-
"""本机 TUI 的跨进程单实例锁测试。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import single_instance  # noqa: E402


class LaunchGuardTests(unittest.TestCase):
    def test_rejects_overlapping_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "tui-run.lock"
            with single_instance.launch_guard(lock_path):
                with self.assertRaises(single_instance.LaunchAlreadyRunning):
                    with single_instance.launch_guard(lock_path):
                        self.fail("second holder unexpectedly acquired the lock")

    def test_released_after_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "tui-run.lock"
            with single_instance.launch_guard(lock_path):
                pass
            with single_instance.launch_guard(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
