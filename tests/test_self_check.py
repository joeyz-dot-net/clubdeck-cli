import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import self_check
from self_check import CheckResult


class EnsureRequirementsTests(unittest.TestCase):
    def test_all_installed_skips_pip(self):
        with patch.object(self_check, "_IMPORT_NAMES", {"requests": "requests"}), \
             patch("self_check.importlib.util.find_spec", return_value=object()), \
             patch("self_check.subprocess.run") as run:
            self.assertEqual(self_check.ensure_requirements(), (True, []))
        run.assert_not_called()

    def test_missing_package_installs_core_requirements(self):
        with patch.object(self_check, "_IMPORT_NAMES", {"requests": "requests"}), \
             patch("self_check.importlib.util.find_spec", return_value=None), \
             patch("self_check.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertEqual(self_check.ensure_requirements(), (True, ["requests"]))
        self.assertIn("requirements.txt", run.call_args.args[0][-1])


class RuntimeCheckTests(unittest.TestCase):
    def test_check_dependencies_reports_python_and_audio_bridge(self):
        with patch("self_check.importlib.util.find_spec", return_value=object()), \
             patch("chcli_tui.AudioBridge.validate_runtime"):
            results = self_check.check_dependencies()
        self.assertEqual(set(results), {"requests", "numpy", "faster_whisper", "audio_bridge"})
        self.assertTrue(all(isinstance(item, CheckResult) and item.ok for item in results.values()))

    def test_audio_bridge_failure_is_reported(self):
        with patch("self_check.importlib.util.find_spec", return_value=object()), \
             patch("chcli_tui.AudioBridge.validate_runtime", side_effect=FileNotFoundError("missing")):
            results = self_check.check_dependencies()
        self.assertFalse(results["audio_bridge"].ok)
        self.assertIn("missing", results["audio_bridge"].detail)

    def test_report_returns_check_results(self):
        expected = {"core": CheckResult(True, "ready")}
        with patch("self_check.check_dependencies", return_value=expected):
            self.assertIs(self_check.run_self_check(print_progress=False), expected)


if __name__ == "__main__":
    unittest.main()
