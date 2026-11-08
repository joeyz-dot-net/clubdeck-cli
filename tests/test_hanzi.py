import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hanzi


class HanziTests(unittest.TestCase):
    def test_converts_traditional_to_simplified(self):
        try:
            import opencc  # noqa: F401
        except ImportError:
            self.skipTest("opencc not installed")
        self.assertEqual(hanzi.to_simplified("你怎麼說 你知道嗎"), "你怎么说 你知道吗")
        self.assertEqual(hanzi.to_simplified("背著 想不著 著急"), "背着 想不着 着急")

    def test_keeps_zhu_variant_words(self):
        self.assertEqual(hanzi.to_simplified("著作 著名 显著"), "著作 著名 显著")

    def test_already_simplified_unchanged(self):
        self.assertEqual(hanzi.to_simplified("今天天气怎么样"), "今天天气怎么样")

    def test_empty_returns_empty(self):
        self.assertEqual(hanzi.to_simplified(""), "")

    def test_fallback_when_converter_unavailable(self):
        with patch("hanzi._get_converter", side_effect=RuntimeError("no opencc")):
            self.assertEqual(hanzi.to_simplified("繁體"), "繁體")


if __name__ == "__main__":
    unittest.main()
