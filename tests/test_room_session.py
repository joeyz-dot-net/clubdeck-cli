# -*- coding: utf-8 -*-
import threading
import unittest

from room_session import RoomSessionManager, response_error


class FakeAudio:
    def __init__(self, active=True):
        self.active = active
        self.starts = []
        self.stops = []

    def start(self, channel, token, uid):
        self.starts.append((channel, token, uid))
        self.active = True

    def stop(self, reason="requested"):
        self.stops.append(reason)
        self.active = False


class RoomSessionManagerTests(unittest.TestCase):
    def _manager(self, api, audio=None, joined=None, logs=None, **kwargs):
        return RoomSessionManager(
            channel="room1",
            user_id="42",
            api_call=api,
            audio=audio or FakeAudio(),
            join_body=lambda channel: {"channel": channel, "ctx": 1},
            on_joined=(joined if joined is not None else (lambda _response: None)),
            log=(logs if logs is not None else []).append,
            active_ping_interval=20,
            active_ping_failure_limit=2,
            recovery_base_delay=5,
            **kwargs,
        )

    def test_response_error_accepts_empty_success_response(self):
        self.assertEqual(response_error({}), "")
        self.assertEqual(response_error({"success": True}), "")
        self.assertEqual(response_error(None), "no response")
        self.assertEqual(
            response_error({"success": False, "error_message": "denied"}),
            "denied",
        )

    def test_active_ping_waits_for_interval_and_records_success(self):
        calls = []
        logs = []
        manager = self._manager(
            lambda path, body: calls.append((path, body)) or {"success": True},
            logs=logs,
        )
        manager.mark_joined(100)

        self.assertEqual(manager.last_active_ping_success_at, 0)
        self.assertEqual(manager.active_ping_successes, 0)
        self.assertEqual(manager.status, "等待房间保活")
        manager.tick(119)
        self.assertEqual(calls, [])
        manager.tick(120)

        self.assertEqual(calls, [("/active_ping", {"channel": "room1"})])
        self.assertEqual(manager.last_active_ping_success_at, 120)
        self.assertEqual(manager.active_ping_successes, 1)
        self.assertTrue(any("active_ping 成功" in line for line in logs))
        self.assertTrue(manager.healthy)

    def test_two_ping_failures_trigger_one_serialized_rejoin(self):
        calls = []
        audio = FakeAudio()
        joined = []

        def api(path, body):
            calls.append((path, body))
            if path == "/active_ping":
                return {"success": False, "error_message": "temporary"}
            if path == "/join_channel":
                return {"success": True, "token": "new-token", "users": []}
            return {"success": True}

        manager = self._manager(api, audio=audio, joined=joined.append)
        manager.mark_joined(100)
        self.assertFalse(manager.tick(120))
        self.assertEqual(audio.starts, [])

        self.assertTrue(manager.tick(140))

        self.assertEqual(audio.starts, [("room1", "new-token", 42)])
        self.assertEqual(len(audio.stops), 1)
        self.assertEqual(len(joined), 1)
        self.assertEqual([path for path, _body in calls].count("/join_channel"), 1)
        self.assertEqual(manager.active_ping_failures, 0)
        self.assertEqual(manager.last_rejoin_at, 140)
        self.assertEqual(manager.rejoin_count, 1)

    def test_invalid_request_triggers_immediate_rejoin(self):
        audio = FakeAudio()

        def api(path, _body):
            if path == "/active_ping":
                return {"success": False, "error_message": "Invalid request."}
            if path == "/join_channel":
                return {"success": True, "token": "new-token"}
            return {"success": True}

        manager = self._manager(api, audio=audio)
        manager.mark_joined(100)

        self.assertTrue(manager.tick(120))
        self.assertTrue(audio.active)
        self.assertEqual(audio.starts, [("room1", "new-token", 42)])

    def test_dead_bridge_uses_delay_then_rejoins(self):
        audio = FakeAudio(active=False)

        def api(path, _body):
            if path == "/join_channel":
                return {"success": True, "token": "new-token"}
            return {"success": True}

        manager = self._manager(api, audio=audio)
        manager.mark_joined(100)

        self.assertFalse(manager.tick(102))
        self.assertIn("音频桥", manager.recovery_reason)
        self.assertFalse(manager.tick(106))
        self.assertTrue(manager.tick(107))
        self.assertEqual(audio.starts, [("room1", "new-token", 42)])

    def test_stop_prevents_ping_and_recovery(self):
        calls = []
        manager = self._manager(
            lambda path, body: calls.append((path, body)) or {"success": True}
        )
        manager.mark_joined(100)
        manager.stop()

        self.assertFalse(manager.tick(200))
        self.assertEqual(calls, [])
        self.assertFalse(manager.healthy)

    def test_stop_waits_for_inflight_recovery_and_cleans_new_bridge(self):
        class BlockingAudio(FakeAudio):
            def __init__(self):
                super().__init__(active=True)
                self.start_entered = threading.Event()
                self.release_start = threading.Event()

            def start(self, channel, token, uid):
                self.start_entered.set()
                self.release_start.wait(timeout=2)
                super().start(channel, token, uid)

        audio = BlockingAudio()

        def api(path, _body):
            if path == "/active_ping":
                return {"success": False, "error_message": "Invalid request."}
            if path == "/join_channel":
                return {"success": True, "token": "new-token"}
            return {"success": True}

        manager = self._manager(api, audio=audio, stop_wait_timeout=2)
        manager.mark_joined(100)
        recovery = threading.Thread(target=lambda: manager.tick(120))
        recovery.start()
        self.assertTrue(audio.start_entered.wait(timeout=1))

        stopped = threading.Event()
        stop_thread = threading.Thread(
            target=lambda: (manager.stop(), stopped.set())
        )
        stop_thread.start()
        self.assertFalse(stopped.wait(timeout=0.05))
        audio.release_start.set()
        recovery.join(timeout=2)
        stop_thread.join(timeout=2)

        self.assertTrue(stopped.is_set())
        self.assertFalse(audio.active)
        self.assertTrue(
            any("session-stop-during-recovery" in reason for reason in audio.stops)
        )
        self.assertEqual(manager.rejoin_count, 0)


if __name__ == "__main__":
    unittest.main()
