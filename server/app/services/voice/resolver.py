"""音色解析：音色池加载、按人格原型过滤、seed 抽签、参数映射（docs/06§4）。

对局内固定（快照进 persona.voice），跨局随 seed 重抽。
"""

import random
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.services.personas import PersonaCard


class VoiceSpec(BaseModel):
    provider_voice: str
    tags: list[str] = []
    base_rate: str = "+0%"
    base_pitch: str = "+0Hz"


class VoiceBook(BaseModel):
    interviewer_voices: list[VoiceSpec]
    candidate_voices: list[VoiceSpec]
    archetype_voice_tags: dict[str, list[str]] = {}

    @classmethod
    def load(cls, path: Path) -> "VoiceBook":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        book = cls.model_validate(data)
        if not book.candidate_voices or not book.interviewer_voices:
            raise ValueError("音色池不能为空")
        return book


def _num(spec: str) -> float:
    m = re.search(r"(-?\d+(?:\.\d+)?)", spec)
    return float(m.group(1)) if m else 0.0


def _fmt_rate(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.0f}%"


def _fmt_pitch(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:.0f}Hz"


class ResolvedVoice(BaseModel):
    """快照进 session_members.persona.voice 的最终音色参数。"""

    provider_voice: str
    rate: str
    pitch: str
    voice_id: str  # 音色池条目名（voices.yaml 中的 key）

    def as_prompt_note(self) -> str:
        return f"voice={self.voice_id}"


class VoiceResolver:
    def __init__(self, book: VoiceBook):
        self.book = book

    def resolve_interviewer(self, rng: random.Random) -> ResolvedVoice:
        spec = rng.choice(self.book.interviewer_voices)
        return ResolvedVoice(
            provider_voice=spec.provider_voice, rate=spec.base_rate,
            pitch=spec.base_pitch, voice_id=spec.provider_voice,
        )

    def resolve_candidate(self, persona: PersonaCard, rng: random.Random) -> ResolvedVoice:
        # 1) 按人格原型气质标签过滤（优先交集大的池子；无交集则全池可抽）
        preferred = self.book.archetype_voice_tags.get(persona.archetype, [])
        pool = self.book.candidate_voices
        scored = [(len(set(v.tags) & set(preferred)), v) for v in pool]
        best = max((s for s, _ in scored), default=0)
        if best > 0:
            pool = [v for s, v in scored if s == best]
        spec = rng.choice(pool)

        # 2) 人格参数 → 语速/音调（docs/06§4 公式），叠加基准值后夹紧
        p = persona.params
        rate = _num(spec.base_rate) + (p.verbosity - 0.5) * 30 + (p.aggressiveness - 0.5) * 20
        pitch = _num(spec.base_pitch) + (p.aggressiveness - 0.5) * 10
        rate = max(-30.0, min(40.0, rate))
        pitch = max(-20.0, min(20.0, pitch))
        return ResolvedVoice(
            provider_voice=spec.provider_voice,
            rate=_fmt_rate(rate), pitch=_fmt_pitch(pitch),
            voice_id=spec.provider_voice,
        )
