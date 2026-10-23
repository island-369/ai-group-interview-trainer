"""FastAPI 入口：REST + WS demo（接口设计见 docs/02§6）。"""

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.agents.candidate import CandidateAgent
from app.core.config import get_settings
from app.schemas.ws import Incoming, out
from app.services.llm_gateway import LLMGateway
from app.services.personas import load_difficulty, load_personas
from app.services.question_bank import QuestionBank

settings = get_settings()
gateway = LLMGateway(settings)
personas = load_personas(settings.personas_dir)
difficulty_book = load_difficulty(settings.difficulty_file)
bank = QuestionBank.load(settings.questions_dir)

app = FastAPI(title="AI 群面陪练", version="0.1.0")


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


@app.websocket("/ws/demo/{persona_id}")
async def ws_demo(ws: WebSocket, persona_id: str):
    """M0 演示：与单个人格化 AI 候选人流式对话。"""
    await ws.accept()
    if persona_id not in personas:
        await ws.send_json(out(type="error", message=f"未知人格卡: {persona_id}"))
        await ws.close()
        return
    agent = CandidateAgent(personas[persona_id], gateway)
    await ws.send_json(
        out(
            type="hello",
            agent=persona_id,
            name=agent.persona.name,
            archetype=agent.persona.archetype,
            provider=settings.resolved_provider,
        )
    )
    history: list[dict] = []
    seq = 0
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
            seq += 1
            await ws.send_json(out(type="speak_start", agent=persona_id, seq=seq))
            buf: list[str] = []
            async for chunk in agent.speak(history):
                buf.append(chunk)
                seq += 1
                await ws.send_json(out(type="speak_chunk", agent=persona_id, seq=seq, chunk=chunk))
            history.append({"role": "assistant", "content": "".join(buf)})
            seq += 1
            await ws.send_json(out(type="speak_end", agent=persona_id, seq=seq, interrupted=False))
    except WebSocketDisconnect:
        return


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
