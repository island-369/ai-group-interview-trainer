"""Agent 基类：持有身份与模型网关（四层 prompt 组装的公共骨架，docs/04§5.1）。"""

from app.services.llm_gateway import LLMGateway
from app.services.personas import PersonaCard


class BaseAgent:
    def __init__(self, persona: PersonaCard, gateway: LLMGateway):
        self.persona = persona
        self.gateway = gateway
