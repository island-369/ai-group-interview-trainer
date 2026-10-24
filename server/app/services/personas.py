"""人格卡与难度档位：YAML 加载、难度缩放（docs/04§5.1、04§7）。"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PersonaParams(BaseModel):
    aggressiveness: float = Field(ge=0, le=1)  # 抢话/打断/主动发言冲动
    content_quality: float = Field(ge=0, le=1)  # 观点逻辑深度
    cooperativeness: float = Field(ge=0, le=1)  # 附和/结盟倾向
    verbosity: float = Field(ge=0, le=1)  # 发言长度偏好
    composure: float = Field(ge=0, le=1)  # 被反驳后的稳定性
    initiative: float = Field(ge=0, le=1)  # 立框架/推进议程倾向


class SpeechStyle(BaseModel):
    patterns: list[str] = []
    quirks: str = ""
    avoid: list[str] = []


class PersonaCard(BaseModel):
    id: str
    name: str
    archetype: str = ""  # 强势型/框架型/…（供抽组与展示）
    school: str = ""
    major: str = ""
    target: str = ""
    mbti: str = ""
    params: PersonaParams
    speech_style: SpeechStyle = SpeechStyle()
    voice: dict | None = None  # 音色快照（resolver 按seed抽签后写入，docs/06§4）

    def scaled_by(self, preset: "DifficultyPreset") -> "PersonaCard":
        """按难度档位生成参数快照（不修改原卡；仅缩放激进度与内容质量）。"""
        p = self.params.model_copy()
        p.aggressiveness = round(min(1.0, p.aggressiveness * preset.aggressiveness_scale), 2)
        p.content_quality = round(min(1.0, p.content_quality * preset.content_quality_scale), 2)
        return self.model_copy(update={"params": p})


class DifficultyPreset(BaseModel):
    key: str
    label: str
    description: str = ""
    aggressiveness_scale: float = Field(default=0.5, ge=0, le=1)
    content_quality_scale: float = Field(default=0.5, ge=0, le=1)
    diversity: float = Field(default=0.5, ge=0, le=1)
    pressure_events: float = Field(default=0.5, ge=0, le=1)
    pace: int = Field(default=10, ge=5, le=30)  # 冷场容忍秒数
    user_interruptible: bool = False
    interviewer_style: str = "normal"  # off | normal | stress


def load_personas(dir_: Path) -> dict[str, PersonaCard]:
    cards = [
        PersonaCard.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        for f in sorted(dir_.glob("*.yaml"))
    ]
    if not cards:
        raise ValueError(f"人格卡目录为空: {dir_}")
    return {c.id: c for c in cards}


def load_difficulty(path: Path) -> dict[str, DifficultyPreset]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    presets = [DifficultyPreset.model_validate(d) for d in data["presets"]]
    return {p.key: p for p in presets}
