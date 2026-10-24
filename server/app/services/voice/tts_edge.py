"""edge-tts 封装：句切分与按句合成（docs/06§2、§5）。

V1 按句整段合成 mp3（1~4s/句，浏览器 decodeAudioData 播放）；
首句遇逗号即断以压首包。更换商用 TTS 时只改本文件（resolver 已隔离音色参数）。
"""

import re

_SENT_END = "。！？；?!\n"
_FIRST_SOFT = "，,"


def split_sentences(text: str, *, first_soft: bool = True) -> list[str]:
    """按句边界切分；first_soft=True 时首句遇逗号也切（压首包），之后只按强边界。"""
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        soft_cut = first_soft and ch in _FIRST_SOFT and buf[:-1].strip()
        if (ch in _SENT_END or soft_cut) and buf.strip():
            out.append(buf)
            buf = ""
            first_soft = False
    if buf.strip():
        out.append(buf)
    return out


def clean_for_tts(text: str) -> str:
    """去除 markdown/表情等不宜朗读的符号。"""
    text = re.sub(r"[*_`#>~\[\]()【】「」]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class EdgeTTS:
    def __init__(self, settings):
        self._s = settings

    @property
    def enabled(self) -> bool:
        return self._s.voice_tts_provider == "edge"

    async def synth(self, text: str, voice: dict) -> bytes:
        """按句合成完整 mp3 字节。voice 为 persona.voice 快照（provider_voice/rate/pitch）。"""
        import edge_tts
        com = edge_tts.Communicate(
            clean_for_tts(text), voice["provider_voice"],
            rate=voice.get("rate", "+0%"), pitch=voice.get("pitch", "+0Hz"))
        buf = bytearray()
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)
