import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtime_bootstrap


class RuntimeBootstrapTests(unittest.TestCase):
    def test_launch_guard_rejects_overlapping_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "launch.lock"
            with runtime_bootstrap.launch_guard(lock_path):
                with self.assertRaises(runtime_bootstrap.LaunchAlreadyRunning):
                    with runtime_bootstrap.launch_guard(lock_path):
                        self.fail("second launcher unexpectedly acquired the lock")

    def test_existing_launcher_is_a_successful_noop(self):
        @contextlib.contextmanager
        def blocked_guard(*_args, **_kwargs):
            raise runtime_bootstrap.LaunchAlreadyRunning
            yield

        with mock.patch("runtime_bootstrap.launch_guard", blocked_guard), mock.patch(
            "runtime_bootstrap._run"
        ) as run:
            self.assertEqual(runtime_bootstrap.main([]), 0)
        run.assert_not_called()

    def test_local_launch_validates_runtime_and_forwards_child_arguments(self):
        args = runtime_bootstrap.build_parser().parse_args(["--no-self-check"])
        child_env = {"PATH": os.environ.get("PATH", "")}
        with mock.patch("self_check.ensure_requirements", return_value=(True, [])), \
             mock.patch("chcli_tui.AudioBridge.validate_runtime") as validate, \
             mock.patch("runtime_bootstrap.os.environ", child_env), \
             mock.patch("runtime_bootstrap.subprocess.call", return_value=0) as call:
            self.assertEqual(runtime_bootstrap._run(args, ["--account", "local"]), 0)
        validate.assert_called_once_with()
        self.assertIn("--account", call.call_args.args[0])
        self.assertEqual(call.call_args.kwargs["env"]["CH_STARTUP_SELF_CHECK"], "0")

    def test_check_mode_does_not_launch_tui(self):
        args = runtime_bootstrap.build_parser().parse_args(["--check"])
        report = {"core": mock.Mock(ok=True)}
        with mock.patch("self_check.ensure_requirements", return_value=(True, [])), \
             mock.patch("self_check.run_self_check", return_value=report), \
             mock.patch("runtime_bootstrap.subprocess.call") as call:
            self.assertEqual(runtime_bootstrap._run(args, []), 0)
        call.assert_not_called()

    def test_launcher_acquires_real_lock(self):
        with mock.patch("runtime_bootstrap._run", return_value=0):
            self.assertEqual(runtime_bootstrap.main([]), 0)
        self.assertTrue(runtime_bootstrap.LAUNCH_LOCK.exists())


if __name__ == "__main__":
    unittest.main()
