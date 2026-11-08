import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clubdeck_config
import chcli_tui
from services import accounts


def _profile(token="tok", user_id="42", device_id="11111111-2222-4333-8444-555555555555"):
    return {
        "token": token,
        "userId": user_id,
        "deviceId": device_id,
        "appVersion": "26.08.11",
        "appBuild": "1037961",
        "userAgent": "clubhouse/android/1037961",
        "languages": "en-US",
        "locale": "en_US",
    }


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._data = Path(self._tmp.name)
        # 重定向存储目录，避免污染真实 data/
        self._patcher = patch.object(clubdeck_config, "DATA_DIR", self._data)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # 重置 chcli_tui 的全局身份状态，避免测试间串号
        self._id_patcher = patch.object(chcli_tui, "_CLUBDECK_PROFILE_IDENTITY", {})
        self._id_patcher.start()
        self.addCleanup(self._id_patcher.stop)

    def test_sanitize_name(self):
        self.assertEqual(accounts.sanitize_name("  xiaofei  "), "xiaofei")
        self.assertEqual(accounts.sanitize_name("xiao-zhu_1"), "xiao-zhu_1")
        with self.assertRaises(accounts.AccountError):
            accounts.sanitize_name("../evil")
        with self.assertRaises(accounts.AccountError):
            accounts.sanitize_name("")
        with self.assertRaises(accounts.AccountError):
            accounts.sanitize_name("a b")

    def test_save_load_list_remove_roundtrip(self):
        p = _profile()
        accounts.save_account("xiaofei", p)
        self.assertEqual(accounts.load_account("xiaofei"), p)
        names = [a["name"] for a in accounts.list_accounts()]
        self.assertIn("xiaofei", names)
        # 列表不含 token
        self.assertNotIn("token", accounts.list_accounts()[0])
        self.assertTrue(accounts.list_accounts()[0]["has_token"])
        self.assertTrue(accounts.remove_account("xiaofei"))
        self.assertIsNone(accounts.load_account("xiaofei"))
        self.assertFalse(accounts.remove_account("xiaofei"))

    def test_import_account_validates_token_and_user(self):
        # 缺 userId → 拒绝
        with self.assertRaises(accounts.AccountError):
            accounts.import_account("bad", {"token": "t"})
        # 缺 token → 拒绝
        with self.assertRaises(accounts.AccountError):
            accounts.import_account("bad", {"userId": "1"})
        # 非 dict → 拒绝
        with self.assertRaises(accounts.AccountError):
            accounts.import_account("bad", ["not", "a", "dict"])
        # 传 None 会读实时 profile；无实时 profile（空 dict）→ 缺 token/userId 拒绝
        with patch.object(chcli_tui, "load_clubdeck_profile", return_value={}):
            with self.assertRaises(accounts.AccountError):
                accounts.import_account("bad", None)

    def test_import_account_with_explicit_profile(self):
        name, summary = accounts.import_account("xiaofei", _profile())
        self.assertEqual(name, "xiaofei")
        self.assertEqual(summary["user_id"], "42")
        self.assertEqual(accounts.load_account("xiaofei")["token"], "tok")

    def test_active_persistence(self):
        accounts.save_account("a", _profile(user_id="1"))
        accounts.save_account("b", _profile(user_id="2"))
        self.assertIsNone(accounts.get_active_account())
        accounts.set_active_account("b")
        self.assertEqual(accounts.get_active_account(), "b")
        # 删除 active 账户时清除 active
        accounts.remove_account("b")
        self.assertIsNone(accounts.get_active_account())

    def test_set_active_requires_existing(self):
        with self.assertRaises(accounts.AccountError):
            accounts.set_active_account("nope")

    def test_select_single_account(self):
        accounts.save_account("solo", _profile())
        token, user_id, profile = accounts.select_account(interactive=False)
        self.assertEqual((token, user_id), ("tok", "42"))
        self.assertEqual(profile["deviceId"], "11111111-2222-4333-8444-555555555555")

    def test_select_arg_wins_and_fails_closed(self):
        accounts.save_account("a", _profile(token="ta", user_id="1"))
        accounts.save_account("b", _profile(token="tb", user_id="2"))
        token, user_id, _ = accounts.select_account(arg="b", interactive=False)
        self.assertEqual((token, user_id), ("tb", "2"))
        with self.assertRaises(accounts.AccountError):
            accounts.select_account(arg="missing", interactive=False)

    def test_select_env_ch_account(self):
        accounts.save_account("envpick", _profile(token="te", user_id="7"))
        token, user_id, _ = accounts.select_account(env={"CH_ACCOUNT": "envpick"}, interactive=False)
        self.assertEqual((token, user_id), ("te", "7"))

    def test_select_active_when_multiple(self):
        accounts.save_account("a", _profile(token="ta", user_id="1"))
        accounts.save_account("b", _profile(token="tb", user_id="2"))
        accounts.set_active_account("a")
        token, user_id, _ = accounts.select_account(interactive=False)
        self.assertEqual((token, user_id), ("ta", "1"))

    def test_select_multiple_no_active_fails_closed(self):
        accounts.save_account("a", _profile(token="ta", user_id="1"))
        accounts.save_account("b", _profile(token="tb", user_id="2"))
        with self.assertRaises(accounts.AccountError):
            accounts.select_account(interactive=False)

    def test_select_legacy_fallback_no_accounts(self):
        live = _profile(token="lt", user_id="99")
        with patch.object(chcli_tui, "load_clubdeck_profile", return_value=live):
            token, user_id, profile = accounts.select_account(interactive=False)
        # 无 env/secrets 时 token/user_id 留空，由 configure_credentials 从 profile 补齐
        self.assertEqual((token, user_id), ("", ""))
        self.assertEqual(profile, live)

    def test_select_legacy_fallback_env(self):
        live = _profile(token="lt", user_id="99")
        env = {"CH_TOKEN": "envt", "CH_USER_ID": "88", "CH_ACCOUNT": ""}
        with patch.object(chcli_tui, "load_clubdeck_profile", return_value=live):
            token, user_id, profile = accounts.select_account(env=env, interactive=False)
        self.assertEqual((token, user_id), ("envt", "88"))
        self.assertEqual(profile, live)

    def test_account_identity_binding(self):
        dev = "22222222-3333-4444-8555-666666666666"
        accounts.save_account("x", _profile(token="tx", user_id="9", device_id=dev))
        token, user_id, profile = accounts.select_account(arg="x", interactive=False)
        t, u = chcli_tui.configure_credentials(token, user_id, profile=profile)
        self.assertEqual((t, u), ("tx", "9"))
        self.assertEqual(chcli_tui._CLUBDECK_PROFILE_IDENTITY.get("deviceId"), dev)


if __name__ == "__main__":
    unittest.main()
