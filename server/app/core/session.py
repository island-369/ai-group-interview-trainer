"""SessionRuntime：一场群面对局的运行时（状态机 + 调度 + 广播）。

生命周期：INIT(等 ready) → BRIEFING → READING → OPENING → DISCUSSION → SUMMARY → CLOSING → REVIEW。
M1（L1）：意愿调度、无打断、无剧情引擎（M2 接入，docs/03§4）。
"""

import asyncio
import random
import time
from dataclasses import dataclass

from app.agents.candidate import CandidateAgent
from app.agents.interviewer import InterviewerAgent
from app.core import director
from app.core.phases import PHASE_LABELS, Phase, PhasePlan
from app.schemas.ws import out
from app.services.evaluation import EvalMember, Evaluator
from app.services.llm_gateway import LLMGateway
from app.services.personas import PersonaCard
from app.services.question_bank import Question
from app.services.store import Store

PERSIST_KINDS = {"speak", "interviewer", "phase", "system", "summary"}


@dataclass
class Member:
    id: str
    name: str
    role: str  # user / candidate / interviewer
    persona: PersonaCard | None = None
    agent: CandidateAgent | None = None
    dyn: director.AgentDyn | None = None
    stance: str = ""


@dataclass
class SessionConfig:
    question: Question
    preset_key: str
    plan: PhasePlan
    user_position: int = 3  # 1-based，用户在陈述顺序中的位次
    rng_seed: int | None = None


class SessionRuntime:
    def __init__(
        self,
        session_id: str,
        cfg: SessionConfig,
        user: Member,
        candidates: list[Member],
        interviewer: InterviewerAgent,
        gateway: LLMGateway,
        store: Store,
        evaluator: Evaluator,
    ):
        self.id = session_id
        self.cfg = cfg
        self.question = cfg.question
        self.plan = cfg.plan
        self.user = user
        self.candidates = candidates
        self.interviewer = interviewer
        self.gateway = gateway
        self.store = store
        self.evaluator = evaluator
        self.rng = random.Random(cfg.rng_seed)

        self.phase = Phase.INIT
        self.status = "waiting"
        self.turn = 0
        self.transcript: list[tuple[str, str]] = []  # (name, text) 供 prompt 窗口
        self._subs: list[asyncio.Queue] = []
        self._ready_evt = asyncio.Event()
        self._skip_reading = asyncio.Event()
        self._pending_user: asyncio.Future | None = None
        self._summary_applied = False
        self._typing_until = 0.0
        self._forced_agent: str | None = None
        self._last_speech_at = time.monotonic()
        self.report: dict | None = None

    # ------------------------------------------------------------ 广播与订阅
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def emit(self, kind: str, *, text: str = "", speaker: Member | None = None,
             persist: bool | None = None, **extra) -> int:
        persist = PERSIST_KINDS.__contains__(kind) if persist is None else persist
        seq = -1
        if persist:
            seq = self.store.append_message(
                self.id, kind if kind != "speak" else "speak", text or "",
                speaker_id=speaker.id if speaker else None,
                meta={"phase": self.phase.value, **extra.pop("meta", {})},
            )
        msg = {
            "type": kind, "seq": seq, "ts": round(time.time(), 3),
            "agent": speaker.id if speaker else None,
            "name": speaker.name if speaker else None,
            "text": text,
        }
        msg.update(extra)
        for q in list(self._subs):
            q.put_nowait(msg)
        return seq

    def snapshot(self) -> dict:
        return out(
            type="snapshot", session_id=self.id, phase=self.phase.value,
            phase_label=PHASE_LABELS[self.phase],
            question=self.question.model_dump(),
            members=[
                {"id": m.id, "name": m.name, "role": m.role,
                 "archetype": m.persona.archetype if m.persona else ""}
                for m in [self.user, *self.candidates, Member(self.interviewer.id, self.interviewer.name, "interviewer")]
            ],
            transcript=[{"name": n, "text": t} for n, t in self.transcript[-50:]],
        )

    # ------------------------------------------------------------ 用户输入路由
    async def handle_user(self, raw: dict) -> None:
        t = raw.get("type", "")
        if t == "user_ready":
            if self.phase is Phase.INIT:
                self._ready_evt.set()
            return
        if t == "user_typing":
            self._typing_until = time.monotonic() + 3.0
            return
        if t == "ping":
            return
        text = (raw.get("text") or "").strip()
        if t == "user_skip" and self.phase is Phase.READING:
            self._skip_reading.set()
            return
        if t == "summary_apply" and self.phase is Phase.SUMMARY:
            self._summary_applied = True
            self.emit("system", text=f"{self.user.name} 举手申请做总结", meta={"event": "summary_apply"})
            return
        if t != "user_speak" or not text:
            self.emit("error", text="无法处理的消息", persist=False)
            return
        if self.phase is Phase.INIT:
            self.emit("error", text="请先点击\"准备开始\"", persist=False)
            return
        if self.phase is Phase.OPENING:
            fut = self._pending_user
            if fut is not None and not fut.done():
                fut.set_result(text)
                return
            self.emit("error", text="现在不是你的陈述时间", persist=False)
            return
        if self.phase is Phase.DISCUSSION:
            self.emit("speak", text=text, speaker=self.user)
            self.transcript.append((self.user.name, text))
            self._typing_until = 0.0
            self._last_speech_at = time.monotonic()
            return
        if self.phase is Phase.SUMMARY:
            fut = self._pending_user
            if fut is not None and not fut.done():
                fut.set_result(text)
                return
            self.emit("error", text="当前无需发言（等待总结代表）", persist=False)
            return
        self.emit("error", text="当前阶段不支持发言", persist=False)

    # ---------------------------------------------------------------- 主流程
    async def run(self) -> None:
        await self._ready_evt.wait()
        self.status = "running"
        self.store.update_session(self.id, status="running")
        try:
            await self._briefing()
            await self._reading()
            await self._opening()
            await self._discussion()
            await self._summary()
            self._set_phase(Phase.CLOSING)
            await self._interviewer_say(self.interviewer.closing())
            await asyncio.sleep(self.plan.closing)
            self.status = "finished"
            self.store.update_session(self.id, status="finished", phase=Phase.REVIEW.value)
            await self._review()
        except Exception as e:  # 对局异常终止：落库并广播
            self.status = "aborted"
            self.store.update_session(self.id, status="aborted")
            self.emit("error", text=f"对局异常终止：{e}", persist=False)
            raise

    def _set_phase(self, p: Phase) -> None:
        self.phase = p
        self.store.update_session(self.id, phase=p.value)
        self.emit("phase", phase=p.value, phase_label=PHASE_LABELS[p], persist=True)

    async def _interviewer_say(self, text: str) -> None:
        m = Member(self.interviewer.id, self.interviewer.name, "interviewer")
        self.emit("interviewer", speaker=m, text=text)

    async def _agent_speak(self, m: Member, task) -> None:
        """流式输出一次 AI 发言；整段完整文本落库一次。"""
        self.emit("speak_start", agent_id=m.id, name=m.name, archetype=m.persona.archetype, persist=False)
        buf = []
        async for chunk in task:
            buf.append(chunk)
            self.emit("speak_chunk", agent_id=m.id, chunk=chunk, persist=False)
        text = "".join(buf).strip() or "……"
        seq = self.store.append_message(self.id, "speak", text, speaker_id=m.id,
                                        meta={"phase": self.phase.value})
        self.emit("speak_end", agent_id=m.id, name=m.name, text=text, seq=seq, persist=False)
        self.transcript.append((m.name, text))
        self._last_speech_at = time.monotonic()

    async def _briefing(self) -> None:
        self._set_phase(Phase.BRIEFING)
        await self._interviewer_say(self.interviewer.briefing(
            self.question, opening_sec=self.plan.opening_per,
            discussion_min=round(self.plan.discussion / 60)))
        await asyncio.sleep(self.plan.briefing)

    async def _reading(self) -> None:
        self._set_phase(Phase.READING)
        # AI 并行生成私密立场笔记（不外显；stance_note 为协程，直接 await）
        async def note(m: Member) -> None:
            if m.agent:
                m.stance = await m.agent.stance_note(self.question)
        await asyncio.gather(*(note(m) for m in self.candidates))
        wait_skip = asyncio.create_task(self._skip_reading.wait())
        wait_time = asyncio.create_task(asyncio.sleep(self.plan.reading))
        done, _ = await asyncio.wait({wait_skip, wait_time}, return_when=asyncio.FIRST_COMPLETED)
        for t in _.union(done):
            t.cancel()

    async def _opening(self) -> None:
        self._set_phase(Phase.OPENING)
        await self._interviewer_say(self.interviewer.opening_start())
        order = self._opening_order()
        for m in order:
            await asyncio.sleep(0.4)
            if m.role == "user":
                await self._interviewer_say(self.interviewer.invite(m.name))
                text = await self._wait_user_text(timeout=max(self.plan.opening_per, 3.0) + 2.0,
                                                  on_timeout=f"{m.name}（你）放弃了个人陈述")
                if text is not None:
                    self.emit("speak", speaker=m, text=text)
                    self.transcript.append((m.name, text))
            else:
                await asyncio.sleep(0.3)
                await self._agent_speak(m, m.agent.opening(self.question, m.stance))
                m.dyn.on_spoke(self.turn)

    def _opening_order(self) -> list[Member]:
        pos = max(1, min(self.cfg.user_position, len(self.candidates) + 1))
        order: list[Member] = self.candidates[: pos - 1] + [self.user] + self.candidates[pos - 1:]
        return order

    async def _wait_user_text(self, *, timeout: float, on_timeout: str) -> str | None:
        loop = asyncio.get_running_loop()
        self._pending_user = loop.create_future()
        try:
            return await asyncio.wait_for(self._pending_user, timeout)
        except asyncio.TimeoutError:
            self.emit("system", text=on_timeout)
            return None
        finally:
            self._pending_user = None

    async def _discussion(self) -> None:
        self._set_phase(Phase.DISCUSSION)
        await self._interviewer_say(self.interviewer.discussion_start(round(self.plan.discussion / 60)))
        deadline = time.monotonic() + self.plan.discussion
        half_at = deadline - self.plan.discussion / 2
        final_at = deadline - min(300, self.plan.discussion * 0.2)
        half_done = final_done = False
        self._last_speech_at = time.monotonic()
        turns = 0
        while time.monotonic() < deadline and turns < 80:
            await asyncio.sleep(self.rng.uniform(*self.plan.director_gap))
            now = time.monotonic()
            if now < self._typing_until and now < deadline:  # 用户输入保护窗
                continue
            if not half_done and now >= half_at:
                half_done = True
                await self._interviewer_say(self.interviewer.half_time())
                self._last_speech_at = now
                continue
            if not final_done and now >= final_at:
                final_done = True
                await self._interviewer_say(self.interviewer.five_minutes())
                self._last_speech_at = now
                continue

            dyns = [m.dyn for m in self.candidates]
            recent = [n for n, _ in self.transcript[-2:]]
            last_text = self.transcript[-1][1] if self.transcript else ""
            chosen, desires = None, {}
            if self._forced_agent:
                chosen = next((m for m in self.candidates if m.id == self._forced_agent), None)
                self._forced_agent = None
            else:
                chosen, desires = director.schedule(
                    dyns, self.turn, last_text=last_text, recent_speakers=recent, rng=self.rng)
            self.store.save_director_log(self.id, self.turn, {
                "phase": "DISCUSSION", "desires": desires,
                "chosen": chosen.persona.id if chosen else None,
                "forced": chosen is not None and not desires})
            for d in dyns:
                d.decay()
            if chosen is None:
                idle = now - self._last_speech_at
                if idle >= self.plan.cold_wait:
                    action, target = director.cold_field_action(
                        dyns, {m.id: m.dyn.spoke_count for m in self.candidates})
                    if action == "nominate":
                        tm = next(m for m in self.candidates if m.id == target)
                        await self._interviewer_say(self.interviewer.nominate(tm.name))
                        tm.dyn.provocation = min(1.0, tm.dyn.provocation + 0.5)
                        self._forced_agent = target
                    else:
                        await self._interviewer_say(self.interviewer.advance())
                        for d in dyns:
                            d.provocation = min(1.0, d.provocation + 0.2)
                    self._last_speech_at = time.monotonic()
                continue

            m = next(m for m in self.candidates if m.id == chosen.persona.id)
            await self._agent_speak(m, m.agent.discuss(self.question, m.stance, self.transcript, self.turn))
            chosen.on_spoke(self.turn)
            self.turn += 1
            turns += 1
        await self._interviewer_say(self.interviewer.discussion_end())

    async def _summary(self) -> None:
        self._set_phase(Phase.SUMMARY)
        await self._interviewer_say(self.interviewer.summary_solicit(self.plan.summary_solicit))
        # AI 举手中途触发：initiative 高者申请
        await asyncio.sleep(min(self.plan.summary_solicit * 0.4, 3.0))
        applicants = [m for m in self.candidates if m.persona.params.initiative >= 0.6]
        for m in applicants:
            self.emit("system", text=f"{m.name} 举手申请做总结")
        await asyncio.sleep(max(self.plan.summary_solicit - min(self.plan.summary_solicit * 0.4, 3.0), 0.1))

        if self._summary_applied:
            chosen = self.user
        elif applicants:
            chosen = max(applicants, key=lambda m: m.persona.params.initiative)
        else:
            chosen = max(self.candidates, key=lambda m: m.persona.params.content_quality)
        await self._interviewer_say(self.interviewer.summary_chosen(chosen.name))

        if chosen is self.user:
            text = await self._wait_user_text(timeout=self.plan.summary_speech,
                                              on_timeout="总结代表未发言，由其他成员补充总结")
            if text is None:
                fallback = max(self.candidates, key=lambda m: m.persona.params.content_quality)
                await self._agent_speak(fallback,
                                        fallback.agent.summary(self.question, fallback.stance, self.transcript))
            else:
                self.emit("summary", speaker=chosen, text=text)
                self.transcript.append((chosen.name, text))
        else:
            await self._agent_speak(chosen,
                                    chosen.agent.summary(self.question, chosen.stance, self.transcript))

    async def _review(self) -> None:
        self.phase = Phase.REVIEW
        messages = self.store.list_messages(self.id)
        ais = [EvalMember(m.id, m.name, "candidate") for m in self.candidates]
        payload = await self.evaluator.evaluate(
            self.question, EvalMember(self.user.id, self.user.name, "user"), ais, messages)
        self.report = payload
        self.store.save_report(self.id, payload["overall"], payload["rank"],
                               payload["radar"], payload)
        self.emit("review_ready", persist=False)


async def _collect(agen) -> str:
    return "".join([c async for c in agen]).strip()
