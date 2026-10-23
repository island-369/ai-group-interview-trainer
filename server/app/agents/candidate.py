"""CandidateAgent：人格化候选人（M1 对局版）。

四层 prompt（docs/04§5.1）：人设层 + 对局层（题目/立场/规则）+ 上下文层（近K轮）+ 任务层。
mock 模式下按任务生成确定性台词，保证无密钥也能完整体验与测试。
"""

import hashlib
from collections.abc import AsyncIterator

from app.agents.base import BaseAgent
from app.services.question_bank import Question

PERSONA_SYSTEM = """你是参加无领导小组讨论（群面）的候选人「{name}」，人设：
- 背景：{school} {major}，目标岗位 {target}（MBTI: {mbti}）
- 性格原型：{archetype}
- 行为参数：{params_desc}
- 说话风格：常用句式「{patterns}」；{quirks}；避免{avoid}

铁律：始终保持人设，你是真实的人类求职者，不是 AI 助手；口语化；
不要出现"作为AI""根据设定"之类的表述；只输出台词本身，不要旁白、引号或动作描述。"""

SESSION_LAYER = """
【本场群面】
题目：《{title}》
材料：{material}
任务：{requirement}
【我的立场笔记（阅读阶段所写，仅自己可见）】{stance}
【规则】当前处于{phase_desc}；你的发言应推进小组达成任务，而非自我表现。"""


def _fmt_params(p) -> str:
    return "、".join(f"{k}={v}" for k, v in p.model_dump().items())


def _stable_index(seed: str, mod: int) -> int:
    return int(hashlib.md5(seed.encode()).hexdigest(), 16) % max(mod, 1)


class CandidateAgent(BaseAgent):
    # ------------------------------------------------------------ prompt 组装
    def _system(self) -> str:
        p, s = self.persona, self.persona.speech_style
        return PERSONA_SYSTEM.format(
            name=p.name, school=p.school, major=p.major,
            target=p.target or "未定", mbti=p.mbti or "未知",
            archetype=p.archetype or "普通候选人", params_desc=_fmt_params(p.params),
            patterns="」「".join(s.patterns[:3]) if s.patterns else "无固定句式",
            quirks=s.quirks or "无特殊习惯",
            avoid="、".join(s.avoid) if s.avoid else "书面腔",
        )

    def _session(self, q: Question, stance: str, phase_desc: str) -> str:
        return SESSION_LAYER.format(
            title=q.title, material=" ".join(q.material.split())[:400],
            requirement=" ".join(q.requirement.split())[:200],
            stance=stance or "（尚未形成明确立场）", phase_desc=phase_desc,
        )

    @staticmethod
    def _window(transcript: list[tuple[str, str]], k: int = 8) -> str:
        lines = [f"{name}：{text}" for name, text in transcript[-k:]]
        return "\n".join(lines) if lines else "（尚无发言）"

    def _messages(self, q, stance, phase_desc, transcript, task) -> list[dict]:
        return [
            {"role": "system", "content": self._system()},
            {"role": "system", "content": self._session(q, stance, phase_desc)},
            {"role": "user", "content": f"【最近发言】\n{self._window(transcript)}\n\n【本次任务】{task}"},
        ]

    # ---------------------------------------------------------------- 任务
    async def stance_note(self, q: Question) -> str:
        """READING 阶段的私密立场笔记（不外显）。"""
        task = "写下你的私密立场笔记：初始立场一句话 + 两条核心论据 + 你打算扮演的讨论角色。50字以内。"
        kp = q.key_points or []
        idx = _stable_index(self.persona.id + q.id, max(len(kp), 1))
        mock = (
            f"立场：围绕《{q.title}》，我倾向 {kp[idx].split('，')[0] if kp else '先把标准定下来'}；"
            f"论据：{kp[0][:20] if kp else '分步拆解'}。角色：做推进者。"
        )
        messages = self._messages(q, "", "独立阅读阶段（写笔记，不会公开）", [], task)
        text = ""
        async for ch in self.gateway.stream("strong", messages, mock_hint=mock):
            text += ch
        return text.strip()

    async def opening(self, q: Question, stance: str) -> AsyncIterator[str]:
        task = "轮到你做个人陈述：先亮立场，再给两点理由，最后一句收尾。60~120字，口语。"
        async for ch in self._speak_task(q, stance, "个人陈述环节", [], task, mock_kind="opening"):
            yield ch

    async def discuss(
        self, q: Question, stance: str, transcript: list[tuple[str, str]], turn: int
    ) -> AsyncIterator[str]:
        target = {0: "40字内", 1: "80字内", 2: "120字内"}[
            0 if self.persona.params.verbosity < 0.4 else (1 if self.persona.params.verbosity < 0.75 else 2)
        ]
        task = (
            f"自由讨论第{turn}轮，轮到你发言，{target}。可以：回应刚才发言者、补充论据、"
            "指出风险、或推动小组收敛结论。不要重复自己说过的内容。"
        )
        async for ch in self._speak_task(q, stance, "自由讨论", transcript, task, mock_kind="discuss", turn=turn):
            yield ch

    async def summary(self, q: Question, stance: str, transcript: list[tuple[str, str]]) -> AsyncIterator[str]:
        task = "你是总结代表，做最终总结陈词：复述小组结论、关键理由与分歧处理过程。100~180字。"
        async for ch in self._speak_task(q, stance, "总结陈词", transcript, task, mock_kind="summary"):
            yield ch

    # ------------------------------------------------------------- mock 台词
    def _mock(self, kind: str, q: Question, stance: str, turn: int = 0) -> str:
        p = self.persona
        patterns = p.speech_style.patterns or ["嗯"]
        pat = patterns[turn % len(patterns)]
        stance_frag = stance.split("；")[0].replace("立场：", "")[:22] if stance else q.title
        if kind == "opening":
            return f"{pat}。我的立场是：{stance_frag}。理由有两点，一是先定标准再讨论，二是控制时间节奏。先说这些，后面再补充。"
        if kind == "summary":
            return f"我代表小组总结：经过讨论，我们就《{q.title}》达成了一致，核心结论是{stance_frag}。过程中主要分歧在标准取舍，最终按多数意见收敛。谢谢大家。"
        tail = "别绕了，直接拍板。" if p.params.cooperativeness < 0.4 else "大家觉得呢？"
        return f"{pat}——我还是那个观点：{stance_frag}，{tail}"

    async def chat(self, history: list[dict]) -> AsyncIterator[str]:
        """M0 演示页的候场闲聊（旧接口保留）。"""
        last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        mock = f"（{self.persona.name}·{self.persona.archetype}）「{last[:16]}」这事吧，我先保留看法，听大家怎么说。"
        messages = [
            {"role": "system", "content": self._system()},
            *history,
            {"role": "user", "content": "【任务】候场闲聊：像候选同伴一样自然回应，60~150字。"},
        ]
        async for ch in self.gateway.stream("strong", messages, mock_hint=mock):
            yield ch

    async def _speak_task(
        self, q, stance, phase_desc, transcript, task, *, mock_kind: str, turn: int = 0
    ) -> AsyncIterator[str]:
        messages = self._messages(q, stance, phase_desc, transcript, task)
        async for ch in self.gateway.stream("strong", messages, mock_hint=self._mock(mock_kind, q, stance, turn)):
            yield ch
