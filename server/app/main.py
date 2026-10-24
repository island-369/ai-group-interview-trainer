"""FastAPI 入口：REST + WS（demo 与对局会话，接口设计见 docs/02§6）。"""

import asyncio
import json
import random
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.agents.candidate import CandidateAgent
from app.agents.interviewer import InterviewerAgent
from app.core import director
from app.core.config import BASE_DIR, get_settings
from app.core.phases import PhasePlan
from app.core.session import Member, SessionConfig, SessionRuntime
from app.schemas.ws import Incoming, out
from app.services.evaluation import Evaluator
from app.services.llm_gateway import LLMGateway
from app.services.personas import load_difficulty, load_personas
from app.services.question_bank import QuestionBank
from app.services.rubrics import load_rubrics
from app.services.store import Store

settings = get_settings()
gateway = LLMGateway(settings)
personas = load_personas(settings.personas_dir)
difficulty_book = load_difficulty(settings.difficulty_file)
bank = QuestionBank.load(settings.questions_dir)
rubrics = load_rubrics(BASE_DIR / "configs" / "rubrics.yaml")

Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
store = Store(settings.db_path)
sessions: dict[str, SessionRuntime] = {}

# ---- 语音层单例（docs/06）：模型缺失/关闭时自动降级为纯文字 ----
from app.core.voice_bridge import VoiceBridge
from app.services.voice.asr import LocalASR, int16_bytes_to_f32
from app.services.voice.resolver import VoiceBook, VoiceResolver
from app.services.voice.tts_edge import EdgeTTS
from app.services.voice.vad import SileroVAD

voice_book = VoiceBook.load(settings.voices_file)
voice_resolver = VoiceResolver(voice_book)
tts = EdgeTTS(settings)
asr = LocalASR(settings.voice_models_dir)
voice_ready = tts.enabled and settings.voice_asr_provider == "local" and asr.available
voice_bridges: dict[str, VoiceBridge] = {}

app = FastAPI(title="AI 群面陪练", version="0.2.0")


def _user_id(anon_token: str | None) -> tuple[str, str]:
    token = anon_token or uuid.uuid4().hex
    return store.get_or_create_user(token), token


# ---------------------------------------------------------------- REST
@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "provider": settings.resolved_provider,
        "models": {"strong": settings.llm_model_strong, "light": settings.llm_model_light},
        "usage": gateway.usage.__dict__,
    }


@app.get("/api/personas")
def list_personas():
    return [p.model_dump() for p in personas.values()]


@app.get("/api/difficulty")
def list_difficulty():
    return [d.model_dump() for d in difficulty_book.values()]


@app.get("/api/questions")
def list_questions(
    type: str | None = None,
    industry: str | None = None,
    difficulty: int | None = None,
    random: bool = False,
    limit: int = 50,
):
    if random:
        q = bank.random(type_=type, industry=industry, difficulty=difficulty)
        return [q.model_dump()]
    return [
        q.model_dump() for q in bank.list(type_=type, industry=industry, difficulty=difficulty, limit=limit)
    ]


@app.post("/api/sessions")
async def create_session(
    payload: Annotated[dict, Body(
        examples=[{"question_id": "q_2026_001", "difficulty": "standard", "durations": {}}]
    )],
    x_anon_token: str | None = Header(default=None),
):
    question = bank.get(payload.get("question_id") or "") or bank.random()
    preset_key = payload.get("difficulty", "standard")
    if preset_key not in difficulty_book:
        raise HTTPException(400, f"未知难度档位: {preset_key}")
    preset = difficulty_book[preset_key]

    persona_ids = payload.get("persona_ids") or list(personas)[:5]
    unknown = [p for p in persona_ids if p not in personas]
    if unknown:
        raise HTTPException(400, f"未知人格卡: {unknown}")
    seed = int(payload.get("rng_seed") or uuid.uuid4().int >> 96)  # 音色/调度可复现
    rng = random.Random(seed)
    cards = []
    for p in persona_ids:
        card = personas[p].scaled_by(preset)  # 难度缩放快照
        card = card.model_copy(update={"voice": voice_resolver.resolve_candidate(card, rng).model_dump()})
        cards.append(card)
    interviewer_voice = voice_resolver.resolve_interviewer(rng).model_dump()

    user_id, token = _user_id(x_anon_token)
    session_id = f"s_{uuid.uuid4().hex[:12]}"
    store.create_session(session_id, user_id, question.id, {
        "difficulty": preset_key, "persona_ids": persona_ids, "rng_seed": seed,
        "user_position": int(payload.get("user_position", 3)),
        "durations": payload.get("durations") or {},
    })
    store.add_member(session_id, "interviewer", "interviewer", "林面试官", None)
    store.add_member(session_id, "user", "user", "你", None)
    for c in cards:
        store.add_member(session_id, c.id, "candidate", c.name, c.model_dump())

    runtime = _build_runtime(session_id, question, preset_key, cards,
                             int(payload.get("user_position", 3)),
                             PhasePlan.from_overrides(payload.get("durations")), seed)
    runtime.interviewer_voice = interviewer_voice
    sessions[session_id] = runtime
    asyncio.create_task(runtime.run())
    resp = JSONResponse({
        "session_id": session_id, "question_id": question.id, "difficulty": preset_key,
        "ws_url": f"/ws/session/{session_id}",
        "voice": {"enabled": voice_ready, "ws_url": f"/ws/voice/{session_id}"},
        "members": [{"id": c.id, "name": c.name, "archetype": c.archetype} for c in cards],
    })
    resp.headers["X-Anon-Token"] = token
    return resp


def _build_runtime(session_id, question, preset_key, cards, user_position, plan, seed=None) -> SessionRuntime:
    user = Member("user", "你", "user")
    interviewer = InterviewerAgent()
    candidates = []
    for c in cards:
        m = Member(c.id, c.name, "candidate", persona=c,
                   agent=CandidateAgent(c, gateway), dyn=director.AgentDyn(c))
        candidates.append(m)
    cfg = SessionConfig(question=question, preset_key=preset_key, plan=plan,
                        user_position=user_position, rng_seed=seed)
    return SessionRuntime(session_id, cfg, user, candidates, interviewer,
                          gateway, store, Evaluator(gateway, rubrics))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(404, "对局不存在")
    rt = sessions.get(session_id)
    return {**s, "current_phase": rt.phase.value if rt else s["phase"],
            "members": store.get_members(session_id)}


@app.get("/api/sessions/{session_id}/transcript")
def get_transcript(session_id: str):
    if not store.get_session(session_id):
        raise HTTPException(404, "对局不存在")
    return store.list_messages(session_id)


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: str):
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(404, "对局不存在")
    if s["status"] != "finished":
        raise HTTPException(409, "对局尚未结束")
    report = store.get_report(session_id)
    if not report:
        return JSONResponse({"status": "generating"}, status_code=202)
    return report["payload"]


# ---------------------------------------------------------------- WS
@app.websocket("/ws/demo/{persona_id}")
async def ws_demo(ws: WebSocket, persona_id: str):
    """M0 演示：与单个人格化 AI 候选人流式对话。"""
    await ws.accept()
    if persona_id not in personas:
        await ws.send_json(out(type="error", message=f"未知人格卡: {persona_id}"))
        await ws.close()
        return
    agent = CandidateAgent(personas[persona_id], gateway)
    await ws.send_json(out(type="hello", agent=persona_id, name=agent.persona.name,
                           archetype=agent.persona.archetype,
                           provider=settings.resolved_provider))
    history: list[dict] = []
    try:
        while True:
            raw = await ws.receive_json()
            try:
                msg = Incoming.model_validate(raw)
            except ValidationError:
                await ws.send_json(out(type="error", message="无法解析的消息"))
                continue
            if msg.type == "ping":
                await ws.send_json(out(type="pong"))
                continue
            text = (msg.text or "").strip()
            if not text:
                await ws.send_json(out(type="error", message="text 不能为空"))
                continue
            history.append({"role": "user", "content": text})
            await ws.send_json(out(type="speak_start", agent=persona_id))
            buf = []
            async for chunk in agent.chat(history):  # M0 演示沿用候选人口语人设
                buf.append(chunk)
                await ws.send_json(out(type="speak_chunk", agent=persona_id, chunk=chunk))
            history.append({"role": "assistant", "content": "".join(buf)})
            await ws.send_json(out(type="speak_end", agent=persona_id, interrupted=False))
    except WebSocketDisconnect:
        return


@app.websocket("/ws/session/{session_id}")
async def ws_session(ws: WebSocket, session_id: str):
    """对局会话：快照 + 事件流下行 / 用户输入上行。"""
    rt = sessions.get(session_id)
    if rt is None:
        await ws.accept()
        await ws.send_json(out(type="error", message="对局不存在或已结束"))
        await ws.close()
        return
    await ws.accept()
    q = rt.subscribe()
    await ws.send_json(rt.snapshot())
    sender = asyncio.create_task(_drain(q, ws))
    try:
        while True:
            raw = await ws.receive_json()
            try:
                Incoming.model_validate(raw)
            except ValidationError:
                await ws.send_json(out(type="error", text="无法解析的消息"))
                continue
            await rt.handle_user(raw)
    except WebSocketDisconnect:
        pass
    finally:
        rt.unsubscribe(q)
        sender.cancel()


async def _drain(q: asyncio.Queue, ws: WebSocket) -> None:
    while True:
        msg = await q.get()
        await ws.send_json(msg)


@app.websocket("/ws/voice/{session_id}")
async def ws_voice(ws: WebSocket, session_id: str):
    """语音通道：上行 16k PCM 分片 + 控制消息；下行 TTS 音频与状态（docs/06§2）。

    半双工门控：AI 说话中（bridge.busy）服务端丢弃麦克分片，防回声误识别。
    """
    await ws.accept()
    rt = sessions.get(session_id)
    if rt is None:
        await ws.send_json(out(type="error", text="对局不存在或已结束"))
        await ws.close()
        return
    if not voice_ready:
        await ws.send_json(out(type="error", text="语音未启用（缺少模型或 provider=off），请用文字模式"))
        await ws.close()
        return

    old = voice_bridges.get(session_id)
    if old:
        old.detach()
    bridge = VoiceBridge(rt, tts)
    voice_bridges[session_id] = bridge
    bridge.attach(ws.send_json, ws.send_bytes)
    await ws.send_json(out(type="voice_hello", session=session_id))

    vad = SileroVAD(settings.voice_models_dir)
    seg_queue: asyncio.Queue = asyncio.Queue()

    async def _asr_worker() -> None:
        """ASR 与接收循环解耦：识别耗时 0.5s/段，若内联会灌满 ws 消息队列、
        触发传输层暂停读（pong 也被暂停）→ 客户端 keepalive 超时。"""
        while True:
            seg = await seg_queue.get()
            if seg is None:
                return
            text = await asyncio.to_thread(asr.transcribe, seg)
            if text and len(text) >= 2:
                await ws.send_json(out(type="heard", text=text))
                await rt.handle_user({"type": "user_speak", "text": text})

    worker = asyncio.create_task(_asr_worker())

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                # 半双工门控在客户端（按其播放队列闭麦，room.html micAllowed）；
                # 服务端不按合成队列丢帧——合成进度落后于对局进度时帧会被误杀。
                for seg in vad.feed(int16_bytes_to_f32(msg["bytes"])):
                    seg_queue.put_nowait(seg)
            elif msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                t = data.get("type")
                if t == "user_barge":
                    bridge.barge()
                    await rt.handle_user({"type": "user_barge"})
                    await ws.send_json(out(type="barge"))
                elif t == "voice_stop":
                    for seg in vad.flush():
                        seg_queue.put_nowait(seg)
                elif t == "ping":
                    await ws.send_json(out(type="pong"))
    except WebSocketDisconnect:
        pass
    finally:
        worker.cancel()
        bridge.detach()
        voice_bridges.pop(session_id, None)


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
