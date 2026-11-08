import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vad_segment import has_volume_activity, transcribe_with_vad  # noqa: E402


class FakeSegment:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class FakeModel:
    """Records transcribe calls and replays queued segment lists."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        return list(result), None


class TranscribeWithVadTests(unittest.TestCase):
    def test_vad_filters_empty_and_short_segments(self):
        model = FakeModel([
            FakeSegment("", 0.0, 1.0),
            FakeSegment("你好", 0.5, 0.8),       # 0.3s, 低于默认 0.15? 高于
            FakeSegment("短", 1.0, 1.05),        # 0.05s, 被过滤
            FakeSegment("完整句子", 2.0, 4.0),
        ])
        segs = transcribe_with_vad(model, "x.wav", vad=True)
        self.assertEqual([s.text for s in segs], ["你好", "完整句子"])
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(model.calls[0]["vad_filter"])

    def test_fallback_retries_without_vad_when_empty_and_activity(self):
        model = FakeModel([], [FakeSegment("音乐房里的真话", 0.1, 1.8)])
        segs = transcribe_with_vad(model, "x.wav", vad=True, fallback_on_activity=True)
        self.assertEqual([s.text for s in segs], ["音乐房里的真话"])
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(model.calls[0]["vad_filter"])
        self.assertFalse(model.calls[1]["vad_filter"])

    def test_no_fallback_without_activity(self):
        model = FakeModel([])
        segs = transcribe_with_vad(model, "x.wav", vad=True, fallback_on_activity=False)
        self.assertEqual(segs, [])
        self.assertEqual(len(model.calls), 1)

    def test_no_fallback_when_vad_finds_speech(self):
        model = FakeModel([FakeSegment("有语音", 0.0, 2.0)], [])
        segs = transcribe_with_vad(model, "x.wav", vad=True, fallback_on_activity=True)
        self.assertEqual([s.text for s in segs], ["有语音"])
        self.assertEqual(len(model.calls), 1)

    def test_vad_off_single_call(self):
        model = FakeModel([FakeSegment("旧行为", 0.0, 1.5)])
        segs = transcribe_with_vad(model, "x.wav", vad=False, fallback_on_activity=True)
        self.assertEqual([s.text for s in segs], ["旧行为"])
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]["vad_filter"])


class HasVolumeActivityTests(unittest.TestCase):
    def test_in_window_non_self_uid(self):
        history = [(10.0, {0: 80, 7: 35})]
        self.assertTrue(has_volume_activity(history, 9.0, 11.0))

    def test_only_local_mix_excluded(self):
        history = [(10.0, {0: 200})]
        self.assertFalse(has_volume_activity(history, 9.0, 11.0))

    def test_self_uid_excluded(self):
        history = [(10.0, {42: 90})]
        self.assertFalse(has_volume_activity(history, 9.0, 11.0, self_uid=42))

    def test_outside_window(self):
        history = [(12.0, {7: 90})]
        self.assertFalse(has_volume_activity(history, 9.0, 11.0))

    def test_below_threshold(self):
        history = [(10.0, {7: 1})]
        self.assertFalse(has_volume_activity(history, 9.0, 11.0, threshold=5))

    def test_empty_history(self):
        self.assertFalse(has_volume_activity([], 0.0, 1.0))

    def test_large_sorted_history_only_window_matters(self):
        history = [(float(i), {7: 90}) for i in range(0, 1000)]
        self.assertTrue(has_volume_activity(history, 500.0, 501.0))
        self.assertFalse(has_volume_activity(history, 500.1, 500.9))


class ImportSmokeTests(unittest.TestCase):
    def test_tui_imports_transcription_helpers(self):
        import chcli_tui  # noqa: F401
        self.assertTrue(callable(has_volume_activity))


if __name__ == "__main__":
    unittest.main()
