import os
import io
import json
import re
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chcli_tui
import clubdeck_config

def select_main_menu(app, title):
    """按标题定位主菜单项（上次房间记录存在时菜单项会前移，避免依赖固定下标）。"""
    rows = app.cur["rows"]
    idx = next(i for i, r in enumerate(rows) if r["title"] == title)
    app.move(idx)

class ClubhouseClientIdentityTests(unittest.TestCase):
    CURRENT_CONFIG = {
        "device_id": "",
        "app_version": "",
        "app_build": "",
        "user_agent": "",
        "languages": "",
        "locale": "",
        "accept_language": "",
        "device_mfr": "",
        "device_model": "",
        "keyboards": "",
        "storefront_country_code": "",
    }

    def setUp(self):
        self._device_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._device_tmp.cleanup)
        patcher = patch.object(
            chcli_tui,
            "CLIENT_DEVICE_ID_FILE",
            Path(self._device_tmp.name) / "device-id.txt",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        cache_patcher = patch.object(chcli_tui, "_FALLBACK_DEVICE_IDS", {})
        cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

    def test_old_profile_keeps_device_but_not_stale_app_tuple(self):
        profile = {
            "token": "must-not-leak",
            "deviceId": "11111111-2222-4333-8444-555555555555",
            "appVersion": "24.01.02",
            "appBuild": "3375",
            "userAgent": "clubhouse/android",
            "languages": ["zh-CN", "en-US"],
            "locale": "zh_CN",
        }
        identity = chcli_tui.resolve_client_identity(
            profile, env={}, config=self.CURRENT_CONFIG,
        )
        self.assertEqual(identity["device_id"], profile["deviceId"])
        self.assertEqual(identity["app_version"], "26.08.11")
        self.assertEqual(identity["app_build"], "1037961")
        self.assertEqual(identity["user_agent"], "clubhouse/android/1037961")
        self.assertEqual(identity["languages"], "zh-CN,en-US")
        self.assertEqual(identity["locale"], "zh_CN")
        self.assertEqual(identity["accept_language"], "zh-CN")
        self.assertEqual(identity["device_mfr"], "Google")
        self.assertEqual(identity["device_model"], "Pixel 8")
        self.assertEqual(identity["keyboards"], "Gboard")
        self.assertEqual(identity["storefront_country_code"], "")
        self.assertNotIn("token", identity)

    def test_strictly_newer_complete_profile_tuple_wins(self):
        identity = chcli_tui.resolve_client_identity({
            "deviceId": "11111111-2222-4333-8444-555555555555",
            "appVersion": "26.09.01",
            "appBuild": "1039001",
            "userAgent": "clubhouse/android",
        }, env={}, config=self.CURRENT_CONFIG)
        self.assertEqual(identity["app_version"], "26.09.01")
        self.assertEqual(identity["app_build"], "1039001")
        self.assertEqual(identity["user_agent"], "clubhouse/android/1039001")

    def test_same_version_higher_profile_build_wins(self):
        identity = chcli_tui.resolve_client_identity({
            "deviceId": "11111111-2222-4333-8444-555555555555",
            "appVersion": "26.08.11",
            "appBuild": "1038000",
            "userAgent": "clubhouse/android/1038000",
        }, env={}, config=self.CURRENT_CONFIG)
        self.assertEqual(identity["app_build"], "1038000")
        self.assertEqual(identity["user_agent"], "clubhouse/android/1038000")

    def test_environment_overrides_profile_and_config_as_one_tuple(self):
        identity = chcli_tui.resolve_client_identity({
            "deviceId": "11111111-2222-4333-8444-555555555555",
            "appVersion": "27.01.01",
            "appBuild": "1040000",
        }, env={
            "CH_DEVICE_ID": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "CH_APP_VERSION": "26.08.04",
            "CH_APP_BUILD": "1037900",
            "CH_USER_AGENT": "clubhouse/android/1037900",
            "CH_LANGUAGES": "en-CA",
            "CH_LOCALE": "en_CA",
            "CH_ACCEPT_LANGUAGE": "fr-CA",
            "CH_DEVICE_MFR": "Google",
            "CH_DEVICE_MODEL": "Pixel 8",
            "CH_KEYBOARDS": "Gboard 123!",
            "CH_STOREFRONT_COUNTRY_CODE": "CA",
        }, config=self.CURRENT_CONFIG)
        self.assertEqual(identity, {
            "device_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "app_version": "26.08.04",
            "app_build": "1037900",
            "user_agent": "clubhouse/android/1037900",
            "languages": "en-CA",
            "locale": "en_CA",
            "accept_language": "fr-CA",
            "device_mfr": "Google",
            "device_model": "Pixel 8",
            "keyboards": "Gboard",
            "storefront_country_code": "CA",
        })

    def test_incomplete_environment_tuple_fails_closed(self):
        for env in (
            {"CH_APP_VERSION": "26.08.04"},
            {"CH_APP_BUILD": "1037900"},
            {"CH_USER_AGENT": "clubhouse/android/1037900"},
            {"CH_APP_VERSION": "26.08.04", "CH_APP_BUILD": "1037900"},
        ):
            with self.subTest(env=env), self.assertRaisesRegex(ValueError, "incomplete"):
                chcli_tui.resolve_client_identity({}, env=env, config=self.CURRENT_CONFIG)

    def test_config_defaults_use_the_verified_built_in_tuple(self):
        default = clubdeck_config.DEFAULTS["clubhouse"]
        self.assertEqual(
            (default["app_version"], default["app_build"], default["user_agent"]),
            ("", "", ""),
        )
        identity = chcli_tui.resolve_client_identity({}, env={}, config=default)
        self.assertEqual(
            (identity["app_version"], identity["app_build"], identity["user_agent"]),
            (chcli_tui.APP_VERSION, chcli_tui.APP_BUILD, chcli_tui.UA),
        )

    def test_incomplete_config_tuple_fails_closed(self):
        config = dict(self.CURRENT_CONFIG, app_version="26.09.01")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            chcli_tui.resolve_client_identity({}, env={}, config=config)

    def test_complete_config_tuple_wins_over_profile(self):
        config = dict(
            self.CURRENT_CONFIG,
            app_version="26.08.04",
            app_build="1037900",
            user_agent="clubhouse/android/1037900",
        )
        identity = chcli_tui.resolve_client_identity({
            "deviceId": "11111111-2222-4333-8444-555555555555",
            "appVersion": "27.01.01",
            "appBuild": "1040000",
            "userAgent": "clubhouse/android/1040000",
        }, env={}, config=config)
        self.assertEqual(
            (identity["app_version"], identity["app_build"], identity["user_agent"]),
            ("26.08.04", "1037900", "clubhouse/android/1037900"),
        )

    def test_missing_or_invalid_profile_device_gets_one_persisted_uuid(self):
        first = chcli_tui.resolve_client_identity(
            {"deviceId": "not-a-uuid"}, env={}, config=self.CURRENT_CONFIG,
        )["device_id"]
        second = chcli_tui.resolve_client_identity(
            {}, env={}, config=self.CURRENT_CONFIG,
        )["device_id"]
        self.assertEqual(first, second)
        self.assertEqual(uuid.UUID(first).version, 4)
        self.assertEqual(
            Path(chcli_tui.CLIENT_DEVICE_ID_FILE).read_text(encoding="utf-8").strip(),
            first,
        )

    def test_unwritable_device_file_still_reuses_one_process_uuid(self):
        with patch.object(Path, "write_text", side_effect=OSError("read only")):
            first = chcli_tui.resolve_client_identity(
                {}, env={}, config=self.CURRENT_CONFIG,
            )["device_id"]
            second = chcli_tui.resolve_client_identity(
                {}, env={}, config=self.CURRENT_CONFIG,
            )["device_id"]
        self.assertEqual(first, second)
        self.assertEqual(uuid.UUID(first).version, 4)

    def test_invalid_explicit_device_id_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "UUIDv4"):
            chcli_tui.resolve_client_identity(
                {}, env={"CH_DEVICE_ID": "shared-device"}, config=self.CURRENT_CONFIG,
            )

    def test_credentials_use_profile_identity_only_for_the_same_account(self):
        profile = {
            "token": "profile-token",
            "userId": 42,
            "deviceId": "11111111-2222-4333-8444-555555555555",
        }
        token, uid = chcli_tui.configure_credentials("profile-token", "42", profile=profile)
        self.assertEqual((token, uid), ("profile-token", "42"))
        self.assertEqual(
            chcli_tui._CLUBDECK_PROFILE_IDENTITY["deviceId"], profile["deviceId"],
        )

        token, uid = chcli_tui.configure_credentials("other-token", "99", profile=profile)
        self.assertEqual((token, uid), ("other-token", "99"))
        self.assertEqual(chcli_tui._CLUBDECK_PROFILE_IDENTITY, {})

    def test_credentials_do_not_pair_profile_user_with_other_token(self):
        profile = {"token": "profile-token", "userId": 42, "deviceId": "ignored"}
        token, uid = chcli_tui.configure_credentials("other-token", "", profile=profile)
        self.assertEqual((token, uid), ("other-token", ""))
        self.assertEqual(chcli_tui._CLUBDECK_PROFILE_IDENTITY, {})

    def test_credentials_do_not_pair_profile_token_with_other_user(self):
        profile = {"token": "profile-token", "userId": 42, "deviceId": "ignored"}
        token, uid = chcli_tui.configure_credentials("", "99", profile=profile)
        self.assertEqual((token, uid), ("", "99"))
        self.assertEqual(chcli_tui._CLUBDECK_PROFILE_IDENTITY, {})

    def test_profile_loader_retains_only_non_secret_identity_globally(self):
        previous = chcli_tui._CLUBDECK_PROFILE_IDENTITY
        self.addCleanup(setattr, chcli_tui, "_CLUBDECK_PROFILE_IDENTITY", previous)
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "Clubdeck"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "token": "secret-token",
                "userId": 42,
                "deviceId": "11111111-2222-4333-8444-555555555555",
                "appVersion": "24.01.02",
                "appBuild": "3375",
            }), encoding="utf-8")
            profile = chcli_tui.load_clubdeck_profile([tmp])
            chcli_tui.set_clubdeck_profile_identity(profile)

        self.assertEqual(profile["token"], "secret-token")
        self.assertNotIn("token", chcli_tui._CLUBDECK_PROFILE_IDENTITY)
        self.assertNotIn("userId", chcli_tui._CLUBDECK_PROFILE_IDENTITY)
        self.assertEqual(
            chcli_tui._CLUBDECK_PROFILE_IDENTITY["deviceId"],
            "11111111-2222-4333-8444-555555555555",
        )

class ClubhouseApiTransportTests(unittest.TestCase):
    CURRENT_CONFIG = ClubhouseClientIdentityTests.CURRENT_CONFIG

    def setUp(self):
        env_patcher = patch.dict(os.environ, {}, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        config_patcher = patch.object(
            clubdeck_config,
            "load",
            return_value={"clubhouse": dict(self.CURRENT_CONFIG)},
        )
        config_patcher.start()
        self.addCleanup(config_patcher.stop)
        identity_patcher = patch.object(chcli_tui, "_CLUBDECK_PROFILE_IDENTITY", {})
        identity_patcher.start()
        self.addCleanup(identity_patcher.stop)
        self._device_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._device_tmp.cleanup)
        device_patcher = patch.object(
            chcli_tui,
            "CLIENT_DEVICE_ID_FILE",
            Path(self._device_tmp.name) / "device-id.txt",
        )
        device_patcher.start()
        self.addCleanup(device_patcher.stop)

    def test_http_426_non_json_preserves_status_and_body(self):
        response = Mock(status_code=426, text="please upgrade this client")
        response.json.side_effect = ValueError("not json")
        with patch("chcli_tui.requests.post", return_value=response) as post:
            result = chcli_tui.api("/audience_reply", {"channel": "room1"})
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 426)
        self.assertIn("please upgrade this client", result["error_message"])
        sent = post.call_args.kwargs["headers"]
        self.assertEqual(sent["CH-AppVersion"], "26.08.11")
        self.assertEqual(sent["CH-AppBuild"], "1037961")
        self.assertEqual(sent["User-Agent"], "clubhouse/android/1037961")
        self.assertEqual(sent["CH-Session-Id"], chcli_tui.CLIENT_SESSION_ID)
        self.assertEqual(uuid.UUID(sent["CH-Session-Id"]).version, 4)
        self.assertEqual(sent["CH-DeviceMfr"], "Google")
        self.assertEqual(sent["CH-DeviceModel"], "Pixel 8")
        self.assertEqual(sent["CH-Keyboards"], "Gboard")
        self.assertEqual(sent["Accept-Language"], "en-US")
        self.assertNotIn("CH-Languages", sent)

    def test_session_id_is_reused_for_every_request_in_one_process(self):
        first = chcli_tui.make_headers("token", "42")
        second = chcli_tui.make_headers("token", "42")
        self.assertEqual(first["CH-Session-Id"], second["CH-Session-Id"])
        self.assertEqual(uuid.UUID(first["CH-Session-Id"]).version, 4)

    def test_http_error_json_cannot_report_success(self):
        response = Mock(status_code=426, text="")
        response.json.return_value = {"success": True, "error_message": "upgrade"}
        with patch("chcli_tui.requests.post", return_value=response):
            result = chcli_tui.api("/audience_reply", {"channel": "room1"})
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 426)

    def test_http_error_empty_fields_get_actionable_defaults(self):
        response = Mock(status_code=426, text="")
        response.json.return_value = {
            "success": True, "status_code": None, "error_message": "",
        }
        with patch("chcli_tui.requests.post", return_value=response):
            result = chcli_tui.api("/audience_reply", {"channel": "room1"})
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 426)
        self.assertEqual(result["error_message"], "HTTP 426")

class LoadRoomsTests(unittest.TestCase):
    def test_parses_page_and_cursor(self):
        fake = {
            "items": [
                {"channel": {
                    "topic": "晚上好", "num_speakers": 2, "num_all": 5,
                    "is_private": False, "channel": "abc123",
                }},
                {"channel": {
                    "topic": "私聊房", "num_speakers": 0, "num_all": 1,
                    "is_private": True, "channel": "xyz789",
                }},
            ],
            "next_cursor": "cur123",
        }
        with patch("chcli_tui.api", return_value=fake) as mock:
            rows, cursor = chcli_tui.load_rooms("prev")
        mock.assert_called_once_with("/get_feed_v3", {"next_cursor": "prev"})
        self.assertEqual(cursor, "cur123")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "[公] 晚上好 · 2讲/5人")
        self.assertEqual(rows[1]["title"], "[私] 私聊房 · 0讲/1人")
        self.assertNotIn("sub", rows[0])

    def test_empty_page_returns_empty_cursor(self):
        with patch("chcli_tui.api", return_value={"items": []}):
            rows, cursor = chcli_tui.load_rooms("")
        self.assertEqual(rows, [])
        self.assertEqual(cursor, "")

    def test_skips_items_without_channel(self):
        fake = {"items": [
            {"channel": None},
            "junk",
            {"channel": {"topic": "T", "channel": "c1"}},
        ]}
        with patch("chcli_tui.api", return_value=fake):
            rows, _ = chcli_tui.load_rooms("")
        self.assertEqual(len(rows), 1)

    def test_rooms_sorted_by_people_desc(self):
        fake = {"items": [
            {"channel": {"topic": "小房", "num_speakers": 1, "num_all": 3, "channel": "c1"}},
            {"channel": {"topic": "大房", "num_speakers": 5, "num_all": 40, "channel": "c2"}},
            {"channel": {"topic": "中房", "num_speakers": 2, "num_all": 20, "channel": "c3"}},
        ]}
        with patch("chcli_tui.api", return_value=fake):
            rows, _ = chcli_tui.load_rooms("")
        self.assertEqual([r["id"] for r in rows], ["c2", "c3", "c1"])

class FriendlyRoomNameTests(unittest.TestCase):
    """友好房名以 feed 为准（音乐房 join 响应的 topic 是当前曲目）。"""

    def test_returns_topic_from_feed(self):
        rows = [{"id": "c2", "obj": {"channel": "c2", "topic": "🎵 音乐时间",
                                         "privacy_settings": {"display_text": "飞猪音乐"}}}]
        with patch("chcli_tui.load_rooms", return_value=(rows, "")):
            self.assertEqual(chcli_tui.friendly_room_name("c2"), "飞猪音乐")

    def test_returns_empty_when_not_found(self):
        with patch("chcli_tui.load_rooms", return_value=([], "")):
            self.assertEqual(chcli_tui.friendly_room_name("zzz"), "")

    def test_paginates_to_find_channel(self):
        page1 = ([{"id": "a", "obj": {"topic": "A"}}], "cur1")
        page2 = ([{"id": "b", "obj": {"topic": "B",
                                             "privacy_settings": {"display_text": "飞猪音乐"}}}], "")
        with patch("chcli_tui.load_rooms", side_effect=[page1, page2]) as mock:
            self.assertEqual(chcli_tui.friendly_room_name("b"), "飞猪音乐")
        self.assertEqual(mock.call_count, 2)

    def test_empty_channel_returns_empty(self):
        self.assertEqual(chcli_tui.friendly_room_name(""), "")

class PageHeightTests(unittest.TestCase):
    def test_page_height(self):
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size):
            self.assertEqual(chcli_tui.page_height(), 20)

class WidthFitTests(unittest.TestCase):
    def test_fit_truncates_by_display_width(self):
        self.assertEqual(chcli_tui.fit("中文abc", 4), "中文")
        self.assertEqual(chcli_tui.fit("中文abc", 5), "中文a")
        self.assertEqual(chcli_tui.fit("中文abc", 0), "")

    def test_fit_never_splits_a_wide_char(self):
        self.assertEqual(chcli_tui.fit("a中", 2), "a")
        self.assertEqual(chcli_tui.fit("a中", 3), "a中")

    def test_fit_flattens_newlines(self):
        self.assertNotIn("\n", chcli_tui.fit("a\nb", 80))
        self.assertNotIn("\r", chcli_tui.fit("a\r\nb", 80))

    def test_disp_width_counts_cjk_as_two(self):
        self.assertEqual(chcli_tui.disp_width("中文a"), 5)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

class AltScreenTests(unittest.TestCase):
    """备用屏幕缓冲区配对：进/出必须成对，嵌套深度正确，重绘不再产生滚动。"""

    def setUp(self):
        self._saved_depth = chcli_tui._ALT_DEPTH
        chcli_tui._ALT_DEPTH = 0

    def tearDown(self):
        chcli_tui._ALT_DEPTH = self._saved_depth

    def test_enter_leave_pair(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            chcli_tui.enter_alt()
            chcli_tui.leave_alt()
        self.assertIn("\x1b[?1049h", buf.getvalue())
        self.assertIn("\x1b[?1049l", buf.getvalue())

    def test_nested_depth_emits_restore_once(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            chcli_tui.enter_alt()
            chcli_tui.enter_alt()
            chcli_tui.leave_alt()
            self.assertNotIn("\x1b[?1049l", buf.getvalue())
            self.assertEqual(chcli_tui._ALT_DEPTH, 1)
            chcli_tui.leave_alt()
        self.assertIn("\x1b[?1049l", buf.getvalue())
        self.assertEqual(chcli_tui._ALT_DEPTH, 0)

    def test_leave_without_enter_is_noop(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            chcli_tui.leave_alt()
        self.assertEqual(buf.getvalue(), "")

class ScreenBoundedTests(unittest.TestCase):
    """Every redraw must fit the current window: no physical line wider than
    the terminal and no frame taller than the terminal."""

    def _check(self, buf, width=80, height=24):
        lines = [_ANSI_RE.sub("", ln) for ln in buf.getvalue().splitlines()]
        self.assertLessEqual(len(lines), height)
        for ln in lines:
            self.assertLessEqual(chcli_tui.disp_width(ln), width)

    def test_header_with_long_cjk_title_stays_one_line(self):
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_header("好" * 60)
        self._check(buf)

    def test_list_with_long_cjk_rows_stays_on_screen(self):
        rows = [{"title": "中文" * 40, "sub": "订阅说明" * 30} for _ in range(30)]
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_list(rows, 0, 0, hint="hint")
        self._check(buf)

    def test_keyvals_with_multiline_value_stays_on_screen(self):
        rows = [
            ("speakers", "\n".join(f"讲者{i} @user{i}" for i in range(20))),
            ("topic", "深夜电台" * 20),
        ]
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_keyvals(rows, 0, 0)
        self._check(buf)

    def test_room_with_long_cjk_chat_stays_on_screen(self):
        rows = [
            ("topic", "深夜电台" * 20),
            ("speakers", "A, " * 40),
            ("transcribe", "自动转写: 开"),
            ("chat", "--- 聊天 (5 条) ---"),
        ] + [("", f"用户{i}：" + "消息内容" * 20) for i in range(10)]
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_room({"rows": rows, "hint": "M=麦克风"})
        self._check(buf)

class InputFrameTests(unittest.TestCase):
    def test_backspace_deletes_characters(self):
        app = chcli_tui.make_app()
        app.push({"type": "input", "query": "abc", "hint": "", "on_submit": lambda q: None})
        app.handle("\x08")   # Windows 回退键
        self.assertEqual(app.cur["query"], "ab")
        app.handle("\x7f")   # DEL / 部分终端回退键
        self.assertEqual(app.cur["query"], "a")

    def test_escape_returns_to_previous_frame(self):
        app = chcli_tui.make_app()
        app.push({"type": "list", "title": "设置", "rows": []})
        app.push({"type": "input", "query": "x", "hint": "", "on_submit": lambda q: None})
        self.assertEqual(app.cur["type"], "input")
        app.handle("\x1b")   # Windows Esc 键
        self.assertEqual(app.cur["type"], "list")

class PagedListTests(unittest.TestCase):
    def _make_app_with_frame(self, n_rows, on_page_end):
        app = chcli_tui.make_app()
        app.push({
            "type": "list",
            "rows": [{"title": f"r{i}"} for i in range(n_rows)],
            "sel": 0,
            "top": 0,
            "hint": "",
            "on_page_end": on_page_end,
        })
        return app

    def test_down_at_bottom_triggers_page_end(self):
        fired = []
        app = self._make_app_with_frame(3, lambda f: fired.append(True))
        app.handle("DOWN")
        app.handle("DOWN")
        app.handle("DOWN")
        self.assertTrue(fired)
        self.assertTrue(fired[0])

    def test_page_end_not_fired_mid_list(self):
        fired = []
        app = self._make_app_with_frame(5, lambda f: fired.append(True))
        app.handle("DOWN")
        app.handle("DOWN")
        self.assertEqual(fired, [])

class DrawKeyvalsTests(unittest.TestCase):
    def test_empty_value_rows_are_skipped(self):
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_keyvals([("k", "v"), ("", ""), ("", "  ")], 0, 0)
        out = buf.getvalue()
        self.assertIn("k: v", out)
        self.assertNotIn("\n  \n", out)

class DrawRoomTests(unittest.TestCase):
    def _frame(self, **extra):
        frame = {
            "rows": [
                ("topic", "T"),
                ("channel", "c1"),
                ("audio", "on"),
                ("mic", "MUTED"),
                ("speakers", "A"),
                ("transcribe", "自动转写: 开"),
                ("pin1", "x | https://a | by X"),
                ("chat", "--- 聊天 (2 条) ---"),
                ("", "first"),
                ("", "second"),
            ],
            "hint": "M=麦克风",
        }
        frame.update(extra)
        return frame

    def test_room_draw_bounded_by_screen_height(self):
        rows = self._frame()["rows"] + [("", f"m{i}") for i in range(40)]
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_room({"rows": rows, "hint": ""})
        self.assertLessEqual(len(buf.getvalue().splitlines()), 24)

class DrawListTests(unittest.TestCase):
    def test_draw_list_with_subs_fits_screen(self):
        rows = [{"title": f"t{i}", "sub": f"s{i}"} for i in range(50)]
        buf = io.StringIO()
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size), redirect_stdout(buf):
            chcli_tui.draw_list(rows, 0, 0, hint="hint")
        line_count = len(buf.getvalue().splitlines())
        self.assertLessEqual(line_count, 24)

    def test_rows_per_screen_halves_for_subs(self):
        size = types.SimpleNamespace(columns=80, lines=24)
        with patch("chcli_tui.term_size", return_value=size):
            self.assertEqual(chcli_tui.rows_per_screen([{"title": "a", "sub": "b"}]), 10)
            self.assertEqual(chcli_tui.rows_per_screen([{"title": "a"}]), 20)

class RoomSelectionTests(unittest.TestCase):
    def test_room_row_without_sub_enters_room_directly(self):
        """房间列表选中房名后直接进入，不再显示房间操作菜单。"""
        with patch("chcli_tui.load_rooms", return_value=(
            [{"title": "[公] T · 1讲/2人", "id": "c1", "obj": {"channel": "c1", "url": "https://room/c1"}}],
            "",
        )), patch("chcli_tui.api", return_value={"success": False, "error_message": "join failed"}):
            app = chcli_tui.make_app()
            select_main_menu(app, "活动房间")
            app.handle("\r")   # open room list
            app.handle("\r")   # enter the first room directly
        self.assertEqual(app.cur["title"], "聆听模式 — c1")
        self.assertEqual(len(app.frames), 3)

class ModelSettingsTests(unittest.TestCase):
    def test_reload_model_settings_updates_globals(self):
        old_model = chcli_tui.CH_WHISPER_MODEL
        old_seconds = chcli_tui.CH_TRANSCRIBE_SECONDS
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "clubdeck.config.json")
                clubdeck_config.ensure_file(path)
                clubdeck_config.set_option("tui", "whisper_model", "tiny", path=path)
                saved = clubdeck_config.CONFIG_FILE
                clubdeck_config.CONFIG_FILE = path
                try:
                    chcli_tui.reload_model_settings()
                finally:
                    clubdeck_config.CONFIG_FILE = saved
                self.assertEqual(chcli_tui.CH_WHISPER_MODEL, "tiny")
                self.assertEqual(chcli_tui.CH_TRANSCRIBE_SECONDS, 25)
        finally:
            chcli_tui.CH_WHISPER_MODEL = old_model
            chcli_tui.CH_TRANSCRIBE_SECONDS = old_seconds

    def test_settings_menu_shows_model_and_picker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            clubdeck_config.ensure_file(path)
            saved = clubdeck_config.CONFIG_FILE
            clubdeck_config.CONFIG_FILE = path
            try:
                clubdeck_config.load(refresh=True)
                app = chcli_tui.make_app()
                select_main_menu(app, "设置")
                app.handle("\r")
                self.assertEqual(app.cur["title"], "设置")
                tui_row = next(i for i, r in enumerate(app.cur["rows"]) if r["title"] == "转写参数")
                app.cur["sel"] = tui_row
                app.handle("\r")  # 进入 TUI 参数
                self.assertEqual(app.cur["title"], "转写参数")
                model_row = next(r for r in app.cur["rows"] if r["obj"].get("key") == "whisper_model")
                self.assertEqual(model_row["sub"], "auto")  # 配置里存的是 auto
                app.cur["sel"] = app.cur["rows"].index(model_row)
                app.handle("\r")  # 进入模型选择
                self.assertIn("选择 转写模型", app.cur["title"])
                self.assertIn("当前 auto", app.cur["title"])
            finally:
                clubdeck_config.CONFIG_FILE = saved
                clubdeck_config.load(refresh=True)

class GenericSettingsTests(unittest.TestCase):
    def test_param_defs_for_covers_all_defaults(self):
        for section in clubdeck_config.DEFAULTS:
            keys = {d["key"] for d in chcli_tui.param_defs_for(section)}
            self.assertEqual(keys, set(clubdeck_config.DEFAULTS[section]))

    def test_settings_menu_contains_only_transcription_and_credentials(self):
        app = chcli_tui.make_app()
        select_main_menu(app, "设置")
        app.handle("\r")
        titles = [r["title"] for r in app.cur["rows"]]
        self.assertEqual(titles, ["转写参数", "敏感参数（单独保存）"])

    def test_main_menu_keeps_only_room_transcription_and_settings(self):
        app = chcli_tui.make_app()
        titles = [r["title"] for r in app.cur["rows"]]
        self.assertIn("活动房间", titles)
        self.assertIn("设置", titles)
        self.assertIn("退出", titles)
        self.assertFalse(any(token in title for title in titles for token in ("对话", "新建", "搜索", "社交")))

    def test_bool_toggle_updates_config(self):
        old = chcli_tui.CH_AUTO_TRANSCRIBE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "clubdeck.config.json")
                clubdeck_config.ensure_file(path)
                saved = clubdeck_config.CONFIG_FILE
                clubdeck_config.CONFIG_FILE = path
                try:
                    app = chcli_tui.make_app()
                    select_main_menu(app, "设置")
                    app.handle("\r")
                    tui_row = next(i for i, r in enumerate(app.cur["rows"]) if r["title"] == "转写参数")
                    app.cur["sel"] = tui_row
                    app.handle("\r")  # TUI 参数
                    row = next(r for r in app.cur["rows"] if r["obj"].get("key") == "auto_transcribe")
                    self.assertEqual(row["obj"]["type"], "bool")
                    before = clubdeck_config.load(path, refresh=True)["tui"]["auto_transcribe"]
                    app.cur["sel"] = app.cur["rows"].index(row)
                    app.handle("\r")  # 切换
                    after = clubdeck_config.load(path, refresh=True)["tui"]["auto_transcribe"]
                    self.assertEqual(after, not before)
                finally:
                    clubdeck_config.CONFIG_FILE = saved
        finally:
            chcli_tui.CH_AUTO_TRANSCRIBE = old

    def test_device_picker_lists_devices_and_custom_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clubdeck.config.json")
            clubdeck_config.ensure_file(path)
            saved = clubdeck_config.CONFIG_FILE
            clubdeck_config.CONFIG_FILE = path
            try:
                app = chcli_tui.make_app()
                select_main_menu(app, "设置")
                app.handle("\r")
                tui_row = next(i for i, r in enumerate(app.cur["rows"]) if r["title"] == "转写参数")
                app.cur["sel"] = tui_row
                app.handle("\r")  # TUI 参数
                row = next(r for r in app.cur["rows"] if r["obj"].get("key") == "agora_playback_device")
                app.cur["sel"] = app.cur["rows"].index(row)
                app.handle("\r")  # 打开选择器
                titles = [r["title"] for r in app.cur["rows"]]
                for dev in clubdeck_config.list_audio_output_devices():
                    self.assertTrue(any(dev in t for t in titles), f"选择器应列出实际输出设备: {dev}")
                self.assertTrue(any("自定义" in t for t in titles), "选择器应有自定义输入入口")
                # 走自定义输入入口:枚举失败或设备名变化时仍可手动指定
                custom = next(i for i, r in enumerate(app.cur["rows"]) if "自定义" in r["title"])
                app.cur["sel"] = custom
                app.handle("\r")  # 进入输入
                app.cur["query"] = "Yamaha"
                app.handle("\r")  # 保存
                self.assertEqual(
                    clubdeck_config.load(path, refresh=True)["tui"]["agora_playback_device"], "Yamaha"
                )
            finally:
                clubdeck_config.CONFIG_FILE = saved

    def test_secret_editing_uses_separate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "clubdeck.config.json")
            secrets_path = os.path.join(tmp, "clubdeck.secrets.json")
            clubdeck_config.ensure_file(cfg_path)
            saved_cfg = clubdeck_config.CONFIG_FILE
            saved_secrets = clubdeck_config.SECRETS_FILE
            clubdeck_config.CONFIG_FILE = cfg_path
            clubdeck_config.SECRETS_FILE = secrets_path
            try:
                app = chcli_tui.make_app()
                select_main_menu(app, "设置")
                app.handle("\r")
                row = next(r for r in app.cur["rows"] if r.get("obj") == "secrets")
                app.cur["sel"] = app.cur["rows"].index(row)
                app.handle("\r")  # 进入敏感参数
                self.assertEqual(app.cur["title"], "敏感参数（单独保存）")
                token_row = next(r for r in app.cur["rows"] if r["obj"].get("key") == "token")
                self.assertEqual(token_row["sub"], "未设置")
                app.cur["sel"] = app.cur["rows"].index(token_row)
                app.handle("\r")  # 输入
                app.cur["query"] = "secret-token"
                app.handle("\r")  # 保存
                self.assertEqual(clubdeck_config.get_secret("token", path=secrets_path), "secret-token")
                with open(cfg_path, encoding="utf-8") as fh:
                    self.assertNotIn("secret-token", fh.read())
            finally:
                clubdeck_config.CONFIG_FILE = saved_cfg
                clubdeck_config.SECRETS_FILE = saved_secrets

class StartupSelfCheckTests(unittest.TestCase):
    def test_countdown_first_key_stops_auto_entry(self):
        # 第一次按键只停止自动进入（不进入主界面），第二次按键才进入。
        with patch("msvcrt.kbhit", return_value=True), \
             patch("msvcrt.getwch") as getwch, \
             patch("chcli_tui.time.sleep"):
            chcli_tui.countdown_to_main(5)
        self.assertEqual(getwch.call_count, 2)

    def test_countdown_auto_after_timeout(self):
        fake_times = iter([100.0, 104.9, 105.0, 111.0])
        out = io.StringIO()
        with patch("chcli_tui.time.time", side_effect=lambda: next(fake_times)), \
             patch("msvcrt.kbhit", return_value=False), \
             patch("msvcrt.getwch") as getwch, \
             patch("chcli_tui.time.sleep"), \
             patch("sys.stdout", out):
            chcli_tui.countdown_to_main(5)
        getwch.assert_not_called()
        self.assertIn("按任意键停止倒计时", out.getvalue())
        self.assertIn("自动进入主菜单", out.getvalue())

class RoomLeaveConfirmationTests(unittest.TestCase):
    def _room_screen(self):
        app = chcli_tui.Screen()
        on_leave = Mock()
        app.push({
            "type": "kv",
            "title": "room",
            "rows": [("audio", "connected")],
            "hint": "normal hint",
            "room_channel": "c1",
            "on_leave": on_leave,
        })
        return app, on_leave

    def test_room_leave_requires_two_presses(self):
        app, on_leave = self._room_screen()
        with patch("chcli_tui._log_crash"):
            app.handle("q")
            on_leave.assert_not_called()
            self.assertIn("再次按", app.cur["hint"])
            app.handle("q")
        on_leave.assert_called_once_with()

    def test_other_key_cancels_room_leave_confirmation(self):
        app, on_leave = self._room_screen()
        with patch("chcli_tui._log_crash"):
            app.handle("L")
            app.handle("UP")
            app.handle("L")
        on_leave.assert_not_called()

    def test_non_room_screen_keeps_single_press_back(self):
        app = chcli_tui.Screen()
        app.push({"type": "list", "title": "root", "rows": []})
        app.push({"type": "kv", "title": "detail", "rows": [("x", "y")]})
        app.handle("q")
        self.assertEqual(app.cur["title"], "root")

class CrashLogTests(unittest.TestCase):
    def test_log_crash_writes_file(self):
        from pathlib import Path

        old = chcli_tui.CRASH_LOG
        with tempfile.TemporaryDirectory() as tmp:
            chcli_tui.CRASH_LOG = Path(tmp) / "tui-crash.log"
            try:
                chcli_tui._log_crash("测试: 桥断开")
                self.assertIn("测试: 桥断开", chcli_tui.CRASH_LOG.read_text(encoding="utf-8"))
            finally:
                chcli_tui.CRASH_LOG = old

if __name__ == "__main__":
    unittest.main()
