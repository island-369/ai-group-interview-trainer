"""抢话打断：user_barge 取消 AI 生成、部分文本落库、interrupt 事件广播。"""

import asyncio

from app.agents.candidate import CandidateAgent
from app.agents.interviewer import InterviewerAgent
from app.core import director
from app.core.config import BASE_DIR
from app.core.phases import PhasePlan
from app.core.session import Member, SessionConfig, SessionRuntime
from app.services.evaluation import Evaluator
from app.services.rubrics import load_rubrics
from app.services.store import Store


class SlowGateway:
    """慢速流式假网关：验证生成中途可被取消。"""

    usage = type("U", (), {"calls": 0})()

    async def stream(self, role, messages, mock_hint=None):
        for ch in ("这是一个", "很慢的", "流式发言", "用来测试抢话"):
            await asyncio.sleep(0.15)
            yield ch


async def test_barge_cancels_generation(tmp_path, mock_settings, personas, bank):
    gw = SlowGateway()
    store = Store(tmp_path / "barge.db")
    q = bank.get("q_2026_003")
    card = personas["c_001"]
    m = Member(card.id, card.name, "candidate", persona=card,
               agent=CandidateAgent(card, gw), dyn=director.AgentDyn(card))
    rt = SessionRuntime(
        "s_barge", SessionConfig(question=q, preset_key="standard", plan=PhasePlan()),
        Member("user", "你", "user"), [m], InterviewerAgent(), gw, store,
        Evaluator(gw, load_rubrics(BASE_DIR / "configs" / "rubrics.yaml")))
    rt.store.create_session("s_barge", "u_t", q.id, {"difficulty": "standard"})

    events = rt.subscribe()
    task = asyncio.create_task(rt._agent_speak(m, m.agent.discuss(q, "", [], 1)))
    await asyncio.sleep(0.25)  # 已产出部分 chunk，但未讲完

    await rt.handle_user({"type": "user_barge"})
    await asyncio.wait_for(task, timeout=3)

    got = []
    while not events.empty():
        got.append(events.get_nowait())
    types = [e["type"] for e in got]

    assert "interrupt" in types
    ends = [e for e in got if e["type"] == "speak_end"]
    assert ends and ends[-1].get("interrupted") is True
    assert ends[-1]["text"].endswith("（被打断）")

    # 落库：部分文本 + interrupt 事件均在转录中
    msgs = store.list_messages("s_barge")
    assert any(x["kind"] == "interrupt" for x in msgs)
    speaks = [x for x in msgs if x["kind"] == "speak"]
    assert speaks and speaks[-1]["text"].endswith("（被打断）")

    # 被打断者获得 provocation（下次更想发言）
    assert m.dyn.provocation > 0
