"""对局阶段与时长规格（docs/02§3.1、04§3.1）。"""

from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    INIT = "INIT"
    BRIEFING = "BRIEFING"
    READING = "READING"
    OPENING = "OPENING"
    DISCUSSION = "DISCUSSION"
    SUMMARY = "SUMMARY"
    CLOSING = "CLOSING"
    REVIEW = "REVIEW"

    @classmethod
    def order(cls) -> list["Phase"]:
        return [cls.INIT, cls.BRIEFING, cls.READING, cls.OPENING,
                cls.DISCUSSION, cls.SUMMARY, cls.CLOSING, cls.REVIEW]


PHASE_LABELS = {
    Phase.INIT: "准备",
    Phase.BRIEFING: "宣题",
    Phase.READING: "阅读",
    Phase.OPENING: "个人陈述",
    Phase.DISCUSSION: "自由讨论",
    Phase.SUMMARY: "总结陈词",
    Phase.CLOSING: "收尾",
    Phase.REVIEW: "复盘",
}


@dataclass
class PhasePlan:
    """各阶段秒数；DISCUSSION 为总时长，OPENING 为每人限时。"""

    briefing: int = 20
    reading: int = 240
    opening_per: int = 60
    discussion: int = 1500
    summary_solicit: int = 45
    summary_speech: int = 180
    closing: int = 8
    # 导演调度节奏：决策等待抖动区间（秒）；测试用小值加速
    director_gap: tuple[float, float] = field(default=(0.8, 2.5))
    # 用户发言保护：typing 后 AI 让路的观察窗上限（秒）
    user_protect: float = 6.0
    # 冷场阈值：max(desire) 低于此值且静默超过 cold_wait 秒 → 冷场处理
    cold_floor: float = 0.25
    cold_wait: float = 10.0

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "PhasePlan":
        plan = cls()
        for k, v in (overrides or {}).items():
            if hasattr(plan, k):
                setattr(plan, k, v)
        return plan
