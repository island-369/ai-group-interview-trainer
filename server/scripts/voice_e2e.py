"""语音链路服务端集成验证：模拟麦克风走完整场（docs/06 V1.5）。

用法：先起服（建议 LLM_PROVIDER=mock）
  LLM_PROVIDER=mock .venv/bin/uvicorn app.main:app --port 8126
  .venv/bin/python scripts/voice_e2e.py 8126
"""

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PORT = sys.argv[1] if len(sys.argv) > 1 else "8126"
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}"

USER_OPENING = "我的思路是先把问题拆成三层，渠道，产品，营销。铺货率没变，重点看营销转化和渠道库存。"
USER_SUMMARY = "我代表小组总结，主因是渠道库存积压叠加竞品分流。短期先停压货清库存，一个月内复盘动销。"
FAST = {"briefing": 4, "reading": 2, "opening_per": 30, "discussion": 60,
        "summary_solicit": 6, "summary_speech": 30, "closing": 1, "director_gap": [0.4, 1.0]}


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-Anon-Token": "voice_e2e"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


async def text_to_frames(text: str) -> list[bytes]:
    """模拟麦克风：edge-tts 合成 → 24k→16k 重采样 → int16 → 100ms 帧 + 0.6s 静音尾。"""
    from app.core.config import Settings
    from app.services.voice.tts_edge import EdgeTTS
    mp3 = await EdgeTTS(Settings(_env_file=None)).synth(
        text, {"provider_voice": "zh-CN-YunxiNeural", "rate": "+0%", "pitch": "+0Hz"})
    tmp = Path(f"/tmp/voice_e2e_{abs(hash(text)) % 9999}.mp3")
    tmp.write_bytes(mp3)
    data, sr = sf.read(tmp, dtype="float32")
    n_out = int(len(data) * 16000 / sr)
    pcm = np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data)
    pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
    frames = [pcm16[i:i + 1600].tobytes() for i in range(0, len(pcm16), 1600)]
    frames += [bytes(3200)] * 9  # 0.9s 静音尾，超过 min_silence=0.7s 触发端点
    tmp.unlink()
    return frames


async def speak(ws, text: str) -> None:
    for i, f in enumerate(await text_to_frames(text)):
        await ws.send(f)
        if i % 10 == 0:
            await asyncio.sleep(0.03)


async def main() -> None:
    created = post("/api/sessions", {"question_id": "q_2026_008", "difficulty": "standard",
                                     "durations": FAST})
    sid = created["session_id"]
    assert created["voice"]["enabled"], "voice 应已启用"
    print(f"session={sid} voice=enabled")

    heard, audio_sentences, audio_bytes, ai_speak_ends = 0, 0, 0, 0
    active_audio = 0
    phase = "INIT"
    opened = applied = False
    summary_invited = True  # True=已武装（收到点名即发） "pending"=等空闲 "sent"=已发
    t0 = asyncio.get_event_loop().time()

    async def drain_voice(timeout: float) -> None:
        """消费语音 WS 事件，跟踪在播音频数（半双工：等 AI 说完才能开口）。"""
        nonlocal heard, audio_sentences, audio_bytes, active_audio
        try:
            m = await asyncio.wait_for(vo.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return
        if isinstance(m, (bytes, bytearray)):
            audio_bytes += len(m)
            return
        m = json.loads(m)
        if m["type"] == "heard":
            heard += 1
            print(f"  [ heard] {m['text'][:40]}", flush=True)
        elif m["type"] == "audio_start":
            audio_sentences += 1
            active_audio += 1
        elif m["type"] == "audio_end":
            active_audio = max(0, active_audio - 1)
        elif m["type"] == "voice_hello":
            print("  [voice] hello", flush=True)

    async def speak_when_idle(text: str) -> None:
        deadline = asyncio.get_event_loop().time() + 15
        while active_audio > 0 and asyncio.get_event_loop().time() < deadline:
            await drain_voice(0.4)
        await asyncio.sleep(0.3)
        await speak(vo, text)

    async with websockets.connect(f"{WS}/ws/session/{sid}", ping_timeout=60) as ev, \
               websockets.connect(f"{WS}/ws/voice/{sid}", max_size=2**22, ping_timeout=60) as vo:
        await ev.send(json.dumps({"type": "user_ready"}))

        while asyncio.get_event_loop().time() - t0 < 180:
            # ---- 语音 WS：二进制=AI音频；JSON=状态事件 ----
            await drain_voice(0.4)
            # ---- 事件 WS：驱动脚本用户 ----
            try:
                m = json.loads(await asyncio.wait_for(ev.recv(), timeout=0.5))
            except asyncio.TimeoutError:
                if summary_invited == "pending":
                    await speak_when_idle(USER_SUMMARY)
                    summary_invited = "sent"
                continue
            t = m.get("type")
            if t == "phase":
                phase = m.get("phase", phase)
                print(f"== {m.get('phase_label')} ==", flush=True)
            elif t == "speak_end" and m.get("agent_id") not in (None, "user"):
                ai_speak_ends += 1
            elif t == "interviewer" and "有请" in m.get("text", "") and "总结" not in m.get("text", "") and not opened:
                await speak_when_idle(USER_OPENING)
                opened = True
            elif t == "interviewer" and "有请" in m.get("text", "") and "总结" in m.get("text", "") \
                    and applied and summary_invited is True:
                summary_invited = "pending"  # 下一次 ev 空闲时发
            elif t == "phase" and m.get("phase") == "SUMMARY" and not applied:
                await ev.send(json.dumps({"type": "summary_apply"}))
                applied = True

    print(f"\n结果: heard={heard} 语音句={audio_sentences} 音频字节={audio_bytes} "
          f"AI发言段={ai_speak_ends}")
    ok = heard >= 3 and audio_sentences >= 3 and ai_speak_ends >= 5
    print("✅ 语音链路通过" if ok else "❌ 未达标")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
