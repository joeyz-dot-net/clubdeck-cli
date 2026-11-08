import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clubdeck_config


class ConfigLoadTests(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = clubdeck_config.load(os.path.join(tmp, "nope.json"), refresh=True)
            self.assertTrue(cfg["tui"]["auto_transcribe"])
            self.assertEqual(cfg["tui"]["transcribe_seconds"], "auto")
            self.assertNotIn("assistant", cfg)
            self.assertNotIn("chat_refresh_seconds", cfg["tui"])

    def test_file_overrides_defaults_partially(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"tui": {"transcribe_seconds": 30, "whisper_model": "small"}}, fh)
            cfg = clubdeck_config.load(path, refresh=True)
            self.assertEqual(cfg["tui"]["transcribe_seconds"], 30)
            self.assertEqual(cfg["tui"]["whisper_model"], "small")
            self.assertTrue(cfg["tui"]["auto_transcribe"])

    def test_invalid_json_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            cfg = clubdeck_config.load(path, refresh=True)
            self.assertEqual(cfg["tui"]["transcribe_seconds"], "auto")

    def test_sensitive_keys_never_read_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sneaky.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({
                    "tui": {"whisper_model": "small"},
                    "token": "abc",
                    "tui_secrets": {"api_key": "secret"},
                }, fh)
            cfg = clubdeck_config.load(path, refresh=True)
            self.assertNotIn("token", cfg)
            self.assertNotIn("tui_secrets", cfg)


class EnvOrTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = dict(os.environ)
        self._saved_config = clubdeck_config.CONFIG_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        clubdeck_config.CONFIG_FILE = os.path.join(self._tmp.name, "clubdeck.config.json")
        clubdeck_config.load(refresh=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        clubdeck_config.CONFIG_FILE = self._saved_config

    def _write_config(self, data):
        with open(clubdeck_config.CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        clubdeck_config.load(refresh=True)

    def test_config_value_wins_over_default(self):
        self._write_config({"tui": {"transcribe_seconds": 30}})
        self.assertEqual(clubdeck_config.env_or("tui", "transcribe_seconds", 15), 30)

    def test_env_overrides_config(self):
        self._write_config({"tui": {"transcribe_seconds": 30}})
        os.environ["CH_TRANSCRIBE_SECONDS"] = "45"
        self.assertEqual(
            clubdeck_config.env_or("tui", "transcribe_seconds", 15, "CH_TRANSCRIBE_SECONDS", cast=int),
            45,
        )

    def test_bool_cast_from_env(self):
        os.environ["CH_AUTO_TRANSCRIBE"] = "0"
        self.assertFalse(
            clubdeck_config.env_or("tui", "auto_transcribe", True, "CH_AUTO_TRANSCRIBE", cast=bool)
        )
        os.environ["CH_AUTO_TRANSCRIBE"] = "1"
        self.assertTrue(
            clubdeck_config.env_or("tui", "auto_transcribe", True, "CH_AUTO_TRANSCRIBE", cast=bool)
        )


class EnsureFileTests(unittest.TestCase):
    def test_creates_template_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            result = clubdeck_config.ensure_file(path)
            self.assertEqual(result, Path(path))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), clubdeck_config.DEFAULTS)

    def test_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"tui": {"whisper_model": "small"}}, fh)
            clubdeck_config.ensure_file(path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"tui": {"whisper_model": "small"}})


class SetOptionTests(unittest.TestCase):
    def test_set_option_writes_and_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            clubdeck_config.ensure_file(path)
            clubdeck_config.set_option("tui", "whisper_model", "medium", path=path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["tui"]["whisper_model"], "medium")
            self.assertEqual(clubdeck_config.load(path, refresh=True)["tui"]["whisper_model"], "medium")

    def test_set_option_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            clubdeck_config.ensure_file(path)
            clubdeck_config.set_option("tui", "whisper_model", "tiny", path=path)
            clubdeck_config.set_option("tui", "transcribe_seconds", 20, path=path)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["tui"]["whisper_model"], "tiny")
            self.assertEqual(data["tui"]["transcribe_seconds"], 20)


class SecretsTests(unittest.TestCase):
    def test_set_and_get_secret_in_separate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.secrets.json")
            clubdeck_config.set_secret("token", "abc", path=path)
            self.assertEqual(clubdeck_config.get_secret("token", "", path=path), "abc")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"token": "abc"})

    def test_set_secret_preserves_other_keys_and_empty_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.secrets.json")
            clubdeck_config.set_secret("token", "abc", path=path)
            clubdeck_config.set_secret("user_id", "42", path=path)
            clubdeck_config.set_secret("token", "", path=path)
            self.assertEqual(clubdeck_config.load_secrets(path), {"user_id": "42"})
            self.assertEqual(clubdeck_config.get_secret("token", "", path=path), "")

    def test_missing_secrets_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nope.json")
            self.assertEqual(clubdeck_config.load_secrets(path), {})
            self.assertEqual(clubdeck_config.get_secret("token", "", path=path), "")

    def test_load_secrets_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clubdeck.secrets.json"
            path.write_text(
                json.dumps({"token": "clubdeck-test-token"}),
                encoding="utf-8-sig",
            )
            self.assertEqual(
                clubdeck_config.get_secret("token", path=path),
                "clubdeck-test-token",
            )

    def test_param_defs_cover_all_defaults(self):
        for section, values in clubdeck_config.DEFAULTS.items():
            for key in values:
                self.assertIn(key, {d["key"] for d in clubdeck_config.param_defs_for(section)})

    def test_audio_device_params_are_choice(self):
        for key in ("agora_playback_device",):
            d = next(x for x in clubdeck_config.PARAM_DEFS if x["section"] == "tui" and x["key"] == key)
            self.assertEqual(d["type"], "choice")
            self.assertIn("choices_fn", d)

    def test_resolve_choices_static_and_dynamic(self):
        self.assertEqual(
            clubdeck_config.resolve_choices({"type": "choice", "choices": ["a", "b"]}),
            ["a", "b"],
        )
        with patch("clubdeck_config.list_audio_input_devices", return_value=["default", "Mic A"]):
            self.assertEqual(
                clubdeck_config.resolve_choices({"type": "choice", "choices_fn": "list_audio_input_devices"}),
                ["default", "Mic A"],
            )
        self.assertEqual(clubdeck_config.resolve_choices({"type": "choice"}), [])

    def test_list_audio_devices_falls_back_without_soundcard(self):
        with patch.dict("sys.modules", {"soundcard": None}):
            self.assertEqual(clubdeck_config.list_audio_input_devices(), ["default"])
            self.assertEqual(clubdeck_config.list_audio_output_devices(), ["default"])

    def test_sensitive_params_include_only_clubdeck_credentials(self):
        keys = {d["key"] for d in clubdeck_config.SENSITIVE_PARAMS}
        self.assertEqual(keys, {"token", "user_id"})


class AutoWhisperModelTests(unittest.TestCase):
    def test_weak_host_tiny(self):
        with patch("clubdeck_config.host_specs", return_value=(4, 4.0)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "tiny")

    def test_low_ram_forces_tiny(self):
        with patch("clubdeck_config.host_specs", return_value=(12, 4.0)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "tiny")

    def test_six_threads_base(self):
        with patch("clubdeck_config.host_specs", return_value=(6, 16.0)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "base")

    def test_balanced_host_small(self):
        with patch("clubdeck_config.host_specs", return_value=(12, 32.0)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "small")

    def test_strong_host_medium(self):
        with patch("clubdeck_config.host_specs", return_value=(16, 64.0)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "medium")

    def test_undetectable_defaults_small(self):
        with patch("clubdeck_config.host_specs", return_value=(None, None)):
            self.assertEqual(clubdeck_config.auto_whisper_model(), "small")

    def test_resolve_auto_and_explicit(self):
        with patch("clubdeck_config.auto_whisper_model", return_value="small"):
            self.assertEqual(clubdeck_config.resolve_whisper_model("auto"), "small")
            self.assertEqual(clubdeck_config.resolve_whisper_model(""), "small")
            self.assertEqual(clubdeck_config.resolve_whisper_model("default"), "small")
        self.assertEqual(clubdeck_config.resolve_whisper_model("Tiny"), "tiny")
        self.assertEqual(clubdeck_config.resolve_whisper_model("small"), "small")


class AutoTranscribeSecondsTests(unittest.TestCase):
    def test_seconds_per_model(self):
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("tiny"), 25)
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("base"), 20)
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("small"), 15)
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("medium"), 10)
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("large-v3"), 8)

    def test_unknown_model_defaults_15(self):
        self.assertEqual(clubdeck_config.auto_transcribe_seconds("weird-model"), 15)

    def test_resolve_auto_uses_model(self):
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds("auto", "tiny"), 25)
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds("", "small"), 15)

    def test_resolve_explicit_number(self):
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds("20", "tiny"), 20)
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds(30, "tiny"), 30)
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds("0", "tiny"), 1)

    def test_resolve_invalid_falls_back_to_model(self):
        self.assertEqual(clubdeck_config.resolve_transcribe_seconds("abc", "tiny"), 25)


if __name__ == "__main__":
    unittest.main()
