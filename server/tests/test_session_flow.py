"""全流程 e2e：一场完整群面（mock LLM，秒级时长）。"""

import asyncio
import time

import pytest

from app.agents.candidate import CandidateAgent
from app.agents.interviewer import InterviewerAgent
from app.core import director
from app.core.config import BASE_DIR
from app.core.phases import Phase, PhasePlan
from app.core.session import Member, SessionConfig, SessionRuntime
from app.services.evaluation import Evaluator
from app.services.llm_gateway import LLMGateway
from app.services.rubrics import load_rubrics
from app.services.store import Store

FAST = {
    "briefing": 0.3, "reading": 1.0, "opening_per": 1.0, "discussion": 6.0,
    "summary_solicit": 1.5, "summary_speech": 2.0, "closing": 0.2,
    "director_gap": (0.05, 0.15), "cold_wait": 1.0,
}


@pytest.fixture
def runtime(mock_settings, personas, difficulty, tmp_path):
    from app.services.question_bank import QuestionBank
    q = QuestionBank.load(mock_settings.questions_dir).get("q_2026_003")
    store = Store(tmp_path / "e2e.db")
    preset = difficulty["standard"]
    cards = [personas[k].scaled_by(preset) for k in ["c_001", "c_002", "c_003", "c_004", "c_005"]]
    candidates = [Member(c.id, c.name, "candidate", persona=c,
                         agent=CandidateAgent(c, LLMGateway(mock_settings)),
                         dyn=director.AgentDyn(c))
                  for c in cards]
    rubrics = load_rubrics(BASE_DIR / "configs" / "rubrics.yaml")
    gw = LLMGateway(mock_settings)
    cfg = SessionConfig(question=q, preset_key="standard",
                        plan=PhasePlan.from_overrides(FAST), user_position=3, rng_seed=7)
    rt = SessionRuntime("s_e2e", cfg, Member("user", "你", "user"), candidates,
                        InterviewerAgent(), gw, store, Evaluator(gw, rubrics))
    rt.store.create_session("s_e2e", "u_test", q.id, {"difficulty": "standard"})
    return rt


async def test_full_session_flow(runtime):
    rt = runtime
    events = rt.subscribe()
    task = asyncio.create_task(rt.run())

    await asyncio.sleep(0.1)
    await rt.handle_user({"type": "user_ready"})

    phases_seen: list[str] = []
    ai_speaks = 0
    user_opened = user_discussed = summary_applied = summary_given = False
    review_ready = False
    deadline = time.monotonic() + 90

    while time.monotonic() < deadline:
        if task.done():
            break
        try:
            m = events.get_nowait()
        except asyncio.QueueEmpty:
            # 按阶段扮演用户
            if rt.phase is Phase.OPENING and rt._pending_user and not user_opened:
                await rt.handle_user({"type": "user_speak",
                                      "text": "我建议先统一评判标准：一看可控性，二看影响面，然后按优先级排序，这是我的框架。"})
                user_opened = True
            elif rt.phase is Phase.DISCUSSION and user_discussed < 2:
                await rt.handle_user({"type": "user_speak",
                                      "text": "同意李想的框架，我们按标准逐项过；时间剩一半了，要开始收敛结论。"})
                user_discussed += 1
                await asyncio.sleep(0.3)
            elif rt.phase is Phase.SUMMARY and not summary_applied:
                await rt.handle_user({"type": "summary_apply"})
                summary_applied = True
            elif rt.phase is Phase.SUMMARY and summary_applied and rt._pending_user and not summary_given:
                await rt.handle_user({"type": "user_speak",
                                      "text": "总结：我们最终按可控性与影响面统一了排序标准，并对高风险项做了取舍。"})
                summary_given = True
            await asyncio.sleep(0.02)
            continue
        # 消费事件
        if m["type"] == "phase":
            phases_seen.append(m["phase"])
        elif m["type"] == "speak_end" and m.get("agent_id") and m["agent_id"] != "user":
            ai_speaks += 1
        elif m["type"] == "review_ready":
            review_ready = True

    if not task.done():
        await asyncio.wait_for(task, timeout=10)
    assert task.exception() is None, f"对局异常: {task.exception()}"

    # 排空残余事件（run() 结束前发出的最后几条可能仍在队列中）
    while True:
        try:
            m = events.get_nowait()
        except asyncio.QueueEmpty:
            break
        if m["type"] == "phase":
            phases_seen.append(m["phase"])
        elif m["type"] == "speak_end" and m.get("agent_id") and m["agent_id"] != "user":
            ai_speaks += 1
        elif m["type"] == "review_ready":
            review_ready = True

    # 阶段完整且有序
    expected = ["BRIEFING", "READING", "OPENING", "DISCUSSION", "SUMMARY", "CLOSING"]
    idx = [phases_seen.index(p) for p in expected if p in phases_seen]
    assert len(idx) == 6 and idx == sorted(idx), f"阶段流不完整: {phases_seen}"
    # AI 发言：5 段陈述 + 若干讨论 + 总结
    assert ai_speaks >= 6, f"AI 发言过少: {ai_speaks}"
    assert rt.status == "finished"
    assert review_ready

    # 落库与报告
    messages = rt.store.list_messages(rt.id)
    seqs = [m["seq"] for m in messages]
    assert seqs == sorted(seqs) and len(seqs) > 10
    report = rt.store.get_report(rt.id)
    assert report is not None
    payload = report["payload"]
    assert len(payload["dimensions"]) == 8
    assert all(d["evidence"] for d in payload["dimensions"]), "证据完备率必须 100%"
    assert 1 <= payload["rank"] <= 6 and len(payload["actions"]) == 3

    # 导演日志
    logs_count = len(rt.store._query("SELECT * FROM director_logs WHERE session_id=?", (rt.id,)))
    assert logs_count >= 3
