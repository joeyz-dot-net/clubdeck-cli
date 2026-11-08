import io
import os
import queue
import subprocess
import sys
import types
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chcli_tui import AudioBridge


class AudioBridgeVolumeTests(unittest.TestCase):
    def test_volume_line_records_snapshot_and_history(self):
        bridge = AudioBridge()
        event = bridge._parse_line("[EVT] VOLUME 1:80 2:20")
        self.assertEqual(event, ("volume", {1: 80, 2: 20}))
        self.assertEqual(bridge.volume, {1: 80, 2: 20})
        self.assertEqual(len(bridge.volume_history), 1)
        self.assertEqual(bridge.volume_history[0][1], {1: 80, 2: 20})

    def test_volume_line_updates_snapshot(self):
        bridge = AudioBridge()
        bridge._parse_line("[EVT] VOLUME 1:80")
        bridge._parse_line("[EVT] VOLUME 1:5 3:60")
        self.assertEqual(bridge.volume, {1: 5, 3: 60})
        self.assertEqual(len(bridge.volume_history), 2)

    def test_volume_line_maps_me_to_self_uid(self):
        bridge = AudioBridge()
        bridge.self_uid = "42"
        event = bridge._parse_line("[EVT] VOLUME ME:55 3:10")
        self.assertEqual(event, ("volume", {42: 55, 3: 10}))
        self.assertEqual(bridge.volume, {42: 55, 3: 10})

    def test_volume_line_skips_non_numeric_uids(self):
        bridge = AudioBridge()
        bridge.self_uid = "42"
        event = bridge._parse_line("[EVT] VOLUME ME:55 xyz:9 3:10")
        self.assertEqual(event, ("volume", {42: 55, 3: 10}))

    def test_volume_history_trimmed_by_age(self):
        bridge = AudioBridge()
        now = 1000.0
        bridge.volume_history = [
            (now - 2000.0 + i, {1: 10}) for i in range(5)
        ] + [
            (now - 10.0 + i, {2: 20}) for i in range(3)
        ]
        bridge._trim_volume_history(now)
        self.assertEqual(len(bridge.volume_history), 3)
        self.assertTrue(
            all(t >= now - bridge.VOLUME_HISTORY_KEEP_SECONDS for t, _ in bridge.volume_history)
        )

    def test_volume_history_trimmed_by_count(self):
        bridge = AudioBridge()
        now = 1000.0
        # 全部落在保留窗口内，但数量超上限 -> 走条数裁剪
        bridge.volume_history = [
            (now - 0.01 * i, {1: 10}) for i in range(bridge.VOLUME_HISTORY_MAX + 100)
        ]
        bridge._trim_volume_history(now)
        self.assertLessEqual(len(bridge.volume_history), bridge.VOLUME_HISTORY_MAX)

class AudioBridgeCommandTests(unittest.TestCase):
    def test_command_uses_listen_mode_and_optional_playback_device(self):
        old_exe, old_pb = AudioBridge.BRIDGE_RUNTIME, AudioBridge.PLAYBACK_DEVICE
        try:
            AudioBridge.BRIDGE_RUNTIME = "electron.exe"
            AudioBridge.PLAYBACK_DEVICE = "Speakers"
            command = AudioBridge()._command("ch1", "tok", 123)
            self.assertIn("--listen", command)
            self.assertIn("--device", command)
            self.assertEqual(command[command.index("--device") + 1], "Speakers")
            self.assertNotIn("--mic-device", command)
            self.assertNotIn("--talk", command)
        finally:
            AudioBridge.BRIDGE_RUNTIME, AudioBridge.PLAYBACK_DEVICE = old_exe, old_pb

    def test_command_omits_default_playback_device(self):
        old_exe, old_pb = AudioBridge.BRIDGE_RUNTIME, AudioBridge.PLAYBACK_DEVICE
        try:
            AudioBridge.BRIDGE_RUNTIME = "electron.exe"
            AudioBridge.PLAYBACK_DEVICE = "default"
            command = AudioBridge()._command("ch1", "tok", 123)
            self.assertIn("--listen", command)
            self.assertNotIn("--device", command)
        finally:
            AudioBridge.BRIDGE_RUNTIME, AudioBridge.PLAYBACK_DEVICE = old_exe, old_pb


class AudioBridgeExitCodeTests(unittest.TestCase):
    def test_active_records_bridge_exit_code(self):
        bridge = AudioBridge()
        bridge.proc = types.SimpleNamespace(poll=lambda: 3)
        self.assertFalse(bridge.active)
        self.assertEqual(bridge.last_exit_code, 3)

    def test_exit_code_recorded_only_once(self):
        bridge = AudioBridge()
        bridge.proc = types.SimpleNamespace(poll=lambda: 7)
        self.assertFalse(bridge.active)
        bridge.proc = types.SimpleNamespace(poll=lambda: 7)
        self.assertFalse(bridge.active)
        self.assertEqual(bridge.last_exit_code, 7)


class AudioBridgeEventTests(unittest.TestCase):
    def _bridge_with_queue(self, lines):
        bridge = AudioBridge()
        bridge.proc = types.SimpleNamespace(poll=lambda: None, returncode=0)
        bridge._queue = queue.Queue()
        for line in lines:
            bridge._queue.put(line)
        return bridge

    def test_parse_line_joined_sets_flag(self):
        bridge = AudioBridge()
        self.assertFalse(bridge.joined)
        event = bridge._parse_line("[EVT] JOINED ch=c1 uid=7")
        self.assertEqual(event[0], "joined")
        self.assertTrue(bridge.joined)

    def test_read_events_drains_all_lines(self):
        bridge = self._bridge_with_queue([
            "[EVT] VOLUME 1:80 2:20",
            "[EVT] JOINED ch=c1 uid=7",
        ])
        events = bridge.read_events()
        self.assertEqual([ev[0] for ev in events], ["volume", "joined"])
        self.assertEqual(events[0][1], {1: 80, 2: 20})
        self.assertTrue(bridge._queue.empty())

    def test_read_events_empty_when_inactive(self):
        self.assertEqual(AudioBridge().read_events(), [])

    def test_wait_joined_success_requeues_lines(self):
        bridge = self._bridge_with_queue([
            "[CTRL] mic= MUTED",
            "[EVT] VOLUME 1:10",
            "[EVT] JOINED ch=c1 uid=7",
        ])
        bridge._wait_joined(timeout=2.0)
        self.assertTrue(bridge.joined)
        kinds = [ev[0] for ev in bridge.read_events()]
        self.assertEqual(kinds, ["volume", "joined"])

    def test_wait_joined_timeout_raises(self):
        bridge = self._bridge_with_queue(["[EVT] VOLUME 1:10"])
        with self.assertRaisesRegex(RuntimeError, "did not confirm join"):
            bridge._wait_joined(timeout=0.3)
        self.assertFalse(bridge.joined)

    def test_wait_joined_process_exit_raises(self):
        bridge = AudioBridge()
        bridge.proc = types.SimpleNamespace(poll=lambda: 3, returncode=3)
        bridge._queue = queue.Queue()
        with self.assertRaisesRegex(RuntimeError, "exited before joining"):
            bridge._wait_joined(timeout=1.0)

    def test_wait_joined_succeeds_if_joined_line_already_queued(self):
        bridge = AudioBridge()
        bridge.proc = types.SimpleNamespace(poll=lambda: 3, returncode=3)
        bridge._queue = queue.Queue()
        bridge._queue.put("[EVT] JOINED ch=c1 uid=7")
        bridge._wait_joined(timeout=1.0)
        self.assertTrue(bridge.joined)


class AudioBridgeStartTests(unittest.TestCase):
    @mock.patch("chcli_tui.AudioBridge._wait_joined", side_effect=RuntimeError("join wait failed"))
    @mock.patch.object(AudioBridge, "validate_runtime", return_value=None)
    def test_start_cleans_up_process_when_join_wait_fails(self, _validate_mock, _wait_mock):
        bridge = AudioBridge()
        fake_proc = mock.Mock()
        fake_proc.stdout = io.StringIO()
        fake_proc.poll.return_value = None
        with mock.patch("os.path.isfile", return_value=True), \
             mock.patch("subprocess.Popen", return_value=fake_proc):
            with self.assertRaisesRegex(RuntimeError, "join wait failed"):
                bridge.start("c1", "tok", 1)
        self.assertIsNone(bridge.proc)
        self.assertIsNone(bridge._queue)


class AudioBridgeStopTests(unittest.TestCase):
    def test_stop_waits_for_forced_kill_before_forgetting_process(self):
        bridge = AudioBridge()
        fake_proc = mock.Mock()
        fake_proc.pid = 1234
        fake_proc.stdin = io.StringIO()
        fake_proc.stdout = io.StringIO()
        fake_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="electron", timeout=3),
            9,
        ]
        bridge.proc = fake_proc

        bridge.stop(reason="test-cleanup")

        fake_proc.kill.assert_called_once_with()
        self.assertEqual(fake_proc.wait.call_count, 2)
        self.assertIsNone(bridge.proc)
        self.assertEqual(bridge.last_exit_code, 9)
        self.assertEqual(bridge.last_stop_reason, "test-cleanup")

    def test_stop_keeps_process_handle_when_forced_kill_is_unconfirmed(self):
        bridge = AudioBridge()
        fake_proc = mock.Mock()
        fake_proc.pid = 5678
        fake_proc.stdin = io.StringIO()
        fake_proc.stdout = io.StringIO()
        fake_proc.returncode = None
        fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="electron", timeout=3)
        bridge.proc = fake_proc

        with self.assertRaisesRegex(RuntimeError, "did not exit after forced kill"):
            bridge.stop()

        self.assertIs(bridge.proc, fake_proc)


if __name__ == "__main__":
    unittest.main()
