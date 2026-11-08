# -*- coding: utf-8 -*-
"""Tests for the last-entered-room persistence (data/last_room.json)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clubdeck_config import load_last_room, save_last_room  # noqa: E402


class LastRoomStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def test_save_then_load_roundtrip(self):
        save_last_room("MKbqag0e", "深夜读书房", data_dir=self.data_dir)
        last = load_last_room(data_dir=self.data_dir)
        self.assertIsNotNone(last)
        self.assertEqual(last["channel"], "MKbqag0e")
        self.assertEqual(last["room_name"], "深夜读书房")
        self.assertIn("entered_at", last)

    def test_save_overwrites_previous(self):
        save_last_room("aaa", "A 房", data_dir=self.data_dir)
        save_last_room("bbb", "B 房", data_dir=self.data_dir)
        last = load_last_room(data_dir=self.data_dir)
        self.assertEqual(last["channel"], "bbb")
        self.assertEqual(last["room_name"], "B 房")

    def test_load_returns_none_when_missing(self):
        self.assertIsNone(load_last_room(data_dir=self.data_dir))

    def test_load_returns_none_when_corrupt(self):
        (self.data_dir / "last_room.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_last_room(data_dir=self.data_dir))

    def test_save_ignores_empty_channel(self):
        save_last_room("", "空", data_dir=self.data_dir)
        self.assertFalse((self.data_dir / "last_room.json").exists())


if __name__ == "__main__":
    unittest.main()
