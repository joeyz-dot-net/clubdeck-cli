import os
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chcli_tui
import clubdeck_config
from chcli_tui import AudioBridge, resolve_agora_sdk_dir, resolve_bridge_runtime


class BridgeConfigDefaultsTests(unittest.TestCase):
    def test_defaults_include_new_keys(self):
        self.assertIn("bridge_runtime", clubdeck_config.DEFAULTS["tui"])
        self.assertIn("agora_sdk_dir", clubdeck_config.DEFAULTS["tui"])
        # clubdeck_exe 已完全移除：不再出现在默认配置，也不参与运行时解析
        self.assertNotIn("clubdeck_exe", clubdeck_config.DEFAULTS["tui"])
        self.assertEqual(clubdeck_config.DEFAULTS["tui"]["bridge_runtime"], "")
        self.assertEqual(clubdeck_config.DEFAULTS["tui"]["agora_sdk_dir"], "")


class BridgeRuntimeResolutionTests(unittest.TestCase):
    @staticmethod
    def _touch(path):
        with open(path, "w", encoding="utf-8"):
            pass
        return path

    def test_prefers_explicit_bridge_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._touch(os.path.join(tmp, "node14.exe"))
            with mock.patch.dict(
                os.environ,
                {"CLUBDECK_BRIDGE_RUNTIME": runtime},
                clear=False,
            ):
                self.assertEqual(resolve_bridge_runtime(), runtime)

    def test_first_candidate_returned_when_missing(self):
        with mock.patch.dict(
            os.environ,
            {"CLUBDECK_BRIDGE_RUNTIME": ""},
            clear=False,
        ), mock.patch.object(
            chcli_tui,
            "_project_electron_runtime",
            return_value=r"C:\missing\electron12\electron.exe",
        ):
            self.assertEqual(resolve_bridge_runtime(), r"C:\missing\electron12\electron.exe")

    def test_defaults_to_project_electron12(self):
        with mock.patch.dict(
            os.environ,
            {"CLUBDECK_BRIDGE_RUNTIME": ""},
            clear=False,
        ):
            self.assertEqual(resolve_bridge_runtime(), chcli_tui._project_electron_runtime())

    def test_never_resolves_clubdeck_exe(self):
        """clubdeck_exe 配置/环境变量不再影响桥运行时解析。"""
        with tempfile.TemporaryDirectory() as tmp:
            clubdeck = self._touch(os.path.join(tmp, "Clubdeck.exe"))
            with mock.patch.dict(
                os.environ,
                {"CLUBDECK_BRIDGE_RUNTIME": "", "CLUBDECK_EXE": clubdeck},
                clear=False,
            ):
                self.assertEqual(
                    resolve_bridge_runtime(),
                    chcli_tui._project_electron_runtime(),
                )

    def test_bare_node_name_resolved_via_path(self):
        with mock.patch("shutil.which", return_value=r"C:\Program Files\nodejs\node.exe"):
            self.assertEqual(
                chcli_tui._as_absolute_or_which("node"),
                r"C:\Program Files\nodejs\node.exe",
            )

    def test_sdk_dir_absolutized(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CLUBDECK_SDK_DIR": tmp}, clear=False):
                self.assertEqual(resolve_agora_sdk_dir(), tmp)

    def test_sdk_dir_defaults_to_project_sdk(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch(
            "chcli_tui.clubdeck_config.env_or", return_value=""
        ), mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(resolve_agora_sdk_dir(), chcli_tui._project_sdk_dir())

    def test_sdk_dir_empty_when_project_sdk_missing(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch(
            "chcli_tui.clubdeck_config.env_or", return_value=""
        ), mock.patch.object(
            chcli_tui,
            "_project_sdk_dir",
            return_value=r"C:\missing\agora-electron-sdk",
        ):
            self.assertEqual(resolve_agora_sdk_dir(), "")


class AudioBridgeRuntimeValidationTests(unittest.TestCase):
    def test_runtime_without_sdk_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "electron.exe")
            self._touch(runtime)
            old_runtime, old_sdk = AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR
            try:
                AudioBridge.BRIDGE_RUNTIME = runtime
                AudioBridge.AGORA_SDK_DIR = ""
                with self.assertRaisesRegex(ValueError, "agora_sdk_dir"):
                    AudioBridge.validate_runtime()
            finally:
                AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR = old_runtime, old_sdk

    def test_standalone_runtime_requires_sdk_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "node.exe")
            self._touch(runtime)
            old_runtime, old_sdk = AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR
            try:
                AudioBridge.BRIDGE_RUNTIME = runtime
                AudioBridge.AGORA_SDK_DIR = ""
                with self.assertRaisesRegex(ValueError, "agora_sdk_dir"):
                    AudioBridge.validate_runtime()
            finally:
                AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR = old_runtime, old_sdk

    def test_standalone_runtime_with_sdk_dir_and_addon_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "node.exe")
            self._touch(runtime)
            sdk_dir = os.path.join(tmp, "sdk")
            os.makedirs(os.path.join(sdk_dir, "build", "Release"))
            self._touch(os.path.join(sdk_dir, "build", "Release", "agora_node_ext.node"))
            old_runtime, old_sdk = AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR
            try:
                AudioBridge.BRIDGE_RUNTIME = runtime
                AudioBridge.AGORA_SDK_DIR = sdk_dir
                AudioBridge.validate_runtime()  # should not raise
            finally:
                AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR = old_runtime, old_sdk

    def test_sdk_dir_without_addon_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "node.exe")
            self._touch(runtime)
            sdk_dir = os.path.join(tmp, "sdk")
            os.makedirs(sdk_dir)
            old_runtime, old_sdk = AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR
            try:
                AudioBridge.BRIDGE_RUNTIME = runtime
                AudioBridge.AGORA_SDK_DIR = sdk_dir
                with self.assertRaisesRegex(FileNotFoundError, "agora_node_ext.node"):
                    AudioBridge.validate_runtime()
            finally:
                AudioBridge.BRIDGE_RUNTIME, AudioBridge.AGORA_SDK_DIR = old_runtime, old_sdk

    def test_missing_runtime_raises_file_not_found(self):
        old_runtime = AudioBridge.BRIDGE_RUNTIME
        try:
            AudioBridge.BRIDGE_RUNTIME = r"C:\missing\node.exe"
            with self.assertRaises(FileNotFoundError):
                AudioBridge.validate_runtime()
        finally:
            AudioBridge.BRIDGE_RUNTIME = old_runtime

    def test_bridge_env_sets_sdk_dir_and_electron_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_sdk = AudioBridge.AGORA_SDK_DIR
            try:
                AudioBridge.AGORA_SDK_DIR = tmp
                env = AudioBridge.bridge_env()
                self.assertEqual(env["CLUBDECK_SDK_DIR"], tmp)
                self.assertEqual(env["ELECTRON_RUN_AS_NODE"], "1")
            finally:
                AudioBridge.AGORA_SDK_DIR = old_sdk


    def test_bridge_env_omits_sdk_dir_when_empty(self):
        old_sdk = AudioBridge.AGORA_SDK_DIR
        try:
            AudioBridge.AGORA_SDK_DIR = ""
            env = AudioBridge.bridge_env()
            self.assertNotIn("CLUBDECK_SDK_DIR", env)
        finally:
            AudioBridge.AGORA_SDK_DIR = old_sdk

    @staticmethod
    def _touch(path):
        with open(path, "w", encoding="utf-8"):
            pass
        return path
