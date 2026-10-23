"""CandidateAgent：人格化候选人。

M0 形态为「候场闲聊」——验证人格保持 + 流式生成；
M1 接入对局（读题立场、任务化发言、导演调度）。
"""

from collections.abc import AsyncIterator

from app.agents.base import BaseAgent

SYSTEM_TMPL = """你是参加校招群面的候选人「{name}」，人设：
- 背景：{school} {major}，目标岗位 {target}（MBTI: {mbti}）
- 性格原型：{archetype}
- 行为参数：{params_desc}
- 说话风格：常用句式「{patterns}」；{quirks}；避免{avoid}

要求：始终保持人设。你是一个真实的人类求职者，不是 AI 助手；
回复口语化、简短（60~150字）；不要出现"作为AI"之类的表述。"""


class CandidateAgent(BaseAgent):
    def compose(self, history: list[dict]) -> list[dict]:
        """M0 两层：系统人设层 + 对话层（对局层/任务层在 M1 加入）。"""
        p = self.persona
        style = p.speech_style
        system = SYSTEM_TMPL.format(
            name=p.name,
            school=p.school,
            major=p.major,
            target=p.target or "未定",
            mbti=p.mbti or "未知",
            archetype=p.archetype or "普通候选人",
            params_desc="、".join(f"{k}={v}" for k, v in p.params.model_dump().items()),
            patterns="」「".join(style.patterns[:3]) if style.patterns else "无固定句式",
            quirks=style.quirks or "无特殊习惯",
            avoid="、".join(style.avoid) if style.avoid else "书面腔",
        )
        return [{"role": "system", "content": system}, *history]

    def _mock_reply(self, history: list[dict]) -> str:
        last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        first = (
            self.persona.speech_style.patterns[0]
            if self.persona.speech_style.patterns
            else "嗯"
        )
        return f"{first}——你说的是「{last[:18]}」这块吧，我先保留看法，听听大家怎么说。（{self.persona.name}·mock）"

    async def speak(self, history: list[dict]) -> AsyncIterator[str]:
        messages = self.compose(history)
        async for chunk in self.gateway.stream("strong", messages, mock_hint=self._mock_reply(history)):
            yield chunk
