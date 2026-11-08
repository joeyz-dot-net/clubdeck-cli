"""Traditional-to-simplified Chinese conversion for transcription output."""

from __future__ import annotations

import re


_converter = None
_ZHU_VERBS = "背想拿放看听说走留穿笑等忙急跟坐睡躺靠借记藏找见用吃喝带抱追敲推拉打住跑停站蹲趴躲摸抓握抬扛提拖接顺沿朝向对比为"


def _get_converter():
    global _converter
    if _converter is None:
        from opencc import OpenCC
        _converter = OpenCC("t2s")
    return _converter


def _fix_zhu(text: str) -> str:
    """OpenCC t2s 保留"著";这里只把口语里常见的"动词+著"补成"着"(背着/想着/着急),不动"著作/显著"等。"""
    text = re.sub(r"([{0}])著".format(_ZHU_VERBS), r"\1着", text)
    text = re.sub(r"(不|没|別)著", r"\1着", text)
    text = text.replace("著急", "着急")
    return text


def to_simplified(text: str) -> str:
    """Convert traditional Chinese to simplified; unchanged if OpenCC is unavailable."""
    if not text:
        return text
    try:
        return _fix_zhu(_get_converter().convert(text))
    except Exception:
        return text
