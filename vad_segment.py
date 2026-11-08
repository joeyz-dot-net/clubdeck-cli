# -*- coding: utf-8 -*-
"""VAD-first transcription with a music-room anti-miss fallback.

Why this design:
  - faster-whisper's built-in Silero VAD (vad_filter=True) drops speech that sits
    on top of loud music, so a naive VAD pass can silence music rooms entirely.
  - vad_filter=False transcribes everything, which hallucinates speech-like text
    from music alone (observed on real room recordings).
The optimal path for a voice chatbot is therefore: try VAD first (clean
segments, no music hallucination); if the whole chunk yields nothing but Agora
volume events show a real speaker, retry without VAD to rescue speech-over-music.
"""

from __future__ import annotations

import bisect
from typing import Any, Iterable


WHISPER_INITIAL_PROMPT = "以下是普通话的简体中文内容。"


def has_volume_activity(
    volume_history: Iterable[tuple[float, dict]],
    start: float,
    end: float,
    *,
    self_uid: str | int | None = None,
    threshold: int = 1,
) -> bool:
    """True if any volume snapshot in [start, end] shows a non-self uid above threshold.

    volume_history entries are (wall-clock timestamp, {uid: volume}); uid 0 is the
    local mix and is always excluded.
    """
    exclude = {"0", "0.0", str(self_uid or "")}
    hist = list(volume_history or ())
    try:
        lo = bisect.bisect_left(hist, start, key=lambda item: item[0])
        hi = bisect.bisect_right(hist, end, key=lambda item: item[0])
        window = hist[lo:hi]
    except TypeError:
        # 非序列输入（例如生成器）退化为全量过滤，行为与旧版一致
        window = [item for item in hist if start <= item[0] <= end]
    for t, snapshot in window:
        if not (start <= t <= end):
            continue
        for uid, vol in (snapshot or {}).items():
            if str(uid) not in exclude and int(vol or 0) > threshold:
                return True
    return False


def transcribe_with_vad(
    model: Any,
    wav_path: Any,
    *,
    vad: bool = True,
    min_speech: float = 0.15,
    fallback_on_activity: bool = False,
    beam_size: int = 1,
    initial_prompt: str = WHISPER_INITIAL_PROMPT,
) -> list[Any]:
    """Transcribe with Silero VAD first; retry without VAD if empty + activity.

    Returns whisper segments whose text is non-empty and duration >= min_speech.
    When ``vad`` is True and the VAD pass yields nothing, a second pass without
    VAD runs only if ``fallback_on_activity`` is True (caller detected a real
    speaker via volume events), rescuing speech-over-music without re-enabling
    music hallucination in quiet rooms.
    """
    def _run(vad_filter: bool) -> list[Any]:
        segments, _info = model.transcribe(
            str(wav_path),
            vad_filter=vad_filter,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
        )
        out: list[Any] = []
        for seg in segments:
            text = (getattr(seg, "text", None) or "").strip()
            if not text:
                continue
            duration = float(getattr(seg, "end", 0.0) - getattr(seg, "start", 0.0))
            if duration < min_speech:
                continue
            out.append(seg)
        return out

    segs = _run(vad)
    if vad and not segs and fallback_on_activity:
        segs = _run(False)
    return segs
