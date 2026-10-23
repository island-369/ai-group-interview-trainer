"""导演调度：发言意愿公式与决策（纯逻辑，便于回归测试）。

M1（L1）：意愿调度 + 冷场处理，无打断/剧情（M2 接入，docs/03§4）。
"""

import random
from dataclasses import dataclass, field

from app.services.personas import PersonaCard


@dataclass
class AgentDyn:
    """候选人的对局内动态状态。"""

    persona: PersonaCard
    last_spoke_turn: int = -10
    provocation: float = 0.0        # 被点名/被反驳后的回应冲动，随轮次衰减
    spoke_count: int = 0
    extra: dict = field(default_factory=dict)

    def on_spoke(self, turn: int) -> None:
        self.last_spoke_turn = turn
        self.spoke_count += 1
        self.provocation = max(0.0, self.provocation - 0.3)

    def decay(self) -> None:
        self.provocation = max(0.0, self.provocation - 0.15)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def topic_affinity(agent: AgentDyn, last_text: str, recent_speakers: list[str]) -> float:
    """M1 启发式：被点名/被回应 → 0.8，近期发言过 → 0.65，否则 0.5。
    M2 换小模型按议题相关度打分。"""
    name = agent.persona.name
    if name and name in (last_text or ""):
        return 0.8
    if agent.persona.id in recent_speakers:
        return 0.65
    return 0.5


def desire(
    agent: AgentDyn,
    turn: int,
    *,
    last_text: str = "",
    recent_speakers: list[str] | None = None,
    scenario_modifier: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """发言意愿（docs/04§5.3 公式的 M1 实装）。"""
    rng = rng or random
    p = agent.persona.params
    gap = turn - agent.last_spoke_turn
    base = 0.35 * p.aggressiveness
    affinity = 0.25 * topic_affinity(agent, last_text, recent_speakers or [])
    provocation = 0.20 * clamp01(agent.provocation)
    silence = 0.15 * clamp01(gap / 8.0)
    fatigue = 0.15 * max(0.0, 1.0 - gap / 3.0)  # 刚发言 3 轮内递减惩罚
    noise = rng.gauss(0.0, 0.05)
    return clamp01(base + affinity + provocation + silence - fatigue + scenario_modifier + noise)


def schedule(
    agents: list[AgentDyn],
    turn: int,
    *,
    last_text: str = "",
    recent_speakers: list[str] | None = None,
    rng: random.Random | None = None,
) -> tuple[AgentDyn | None, dict[str, float]]:
    """返回（选定发言者或 None=冷场, 各 agent 意愿值）。"""
    rng = rng or random
    desires = {
        a.persona.id: desire(a, turn, last_text=last_text,
                             recent_speakers=recent_speakers, rng=rng)
        for a in agents
    }
    best = max(agents, key=lambda a: desires[a.persona.id])
    if desires[best.persona.id] < 0.25:
        return None, desires
    return best, desires


def cold_field_action(agents: list[AgentDyn], spoke_counts: dict[str, int]) -> tuple[str, str]:
    """冷场处理：优先点名最沉默者，否则推进议题。返回 (action, agent_id|'')。"""
    silentest = min(agents, key=lambda a: spoke_counts.get(a.persona.id, 0))
    if spoke_counts.get(silentest.persona.id, 0) == 0:
        return "nominate", silentest.persona.id
    return "advance", ""
