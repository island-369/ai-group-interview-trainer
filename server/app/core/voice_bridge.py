"""VoiceBridge：每场对局一个，把运行时文字事件流转成语音流（docs/06§2）。

事件流 → 句切分（首句逗号即断压首包）→ 按成员音色顺序合成 → audio_start/bytes/audio_end 下行。
抢话：清空待合成队列 + 取消在飞合成 + 通知客户端清播放队列（barge）。
"""

import asyncio

from app.services.voice.tts_edge import split_sentences


class VoiceBridge:
    def __init__(self, runtime, tts):
        self.rt = runtime
        self.tts = tts
        self._send_json = None
        self._send_bytes = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pump_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._synth_task: asyncio.Task | None = None
        self._acc = ""              # 当前发言累积文本（speak_chunk 流）
        self._first_pending = True  # 首句逗号软切仅一次
        self._current: tuple[str, str, dict | None] | None = None  # (agent_id, name, voice)
        self.busy = False           # 有待合成/合成中（服务端麦克风门控兜底用）

    # ---------------------------------------------------------------- 生命周期
    def attach(self, send_json, send_bytes) -> None:
        self._send_json, self._send_bytes = send_json, send_bytes
        self._reader_task = asyncio.create_task(self._read_runtime())
        self._pump_task = asyncio.create_task(self._pump())

    def detach(self) -> None:
        for t in (self._reader_task, self._pump_task, self._synth_task):
            if t and not t.done():
                t.cancel()

    # ---------------------------------------------------------------- 抢话
    def barge(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        if self._synth_task and not self._synth_task.done():
            self._synth_task.cancel()
        self.busy = False

    # ---------------------------------------------------------------- 事件流
    async def _read_runtime(self) -> None:
        q = self.rt.subscribe()
        try:
            while True:
                m = await q.get()
                t = m.get("type")
                if t == "speak_start":
                    self._current = (m.get("agent_id") or "", m.get("name") or "",
                                     self._voice_of(m.get("agent_id") or ""))
                    self._acc, self._first_pending = "", True
                elif t == "speak_chunk" and self._current:
                    self._acc += m.get("chunk", "")
                    self._drain_sentences()
                elif t == "speak_end" and self._current:
                    self._flush_rest()
                    self._current = None
                elif t == "phase":
                    # 阶段推进：清掉上一阶段的过期语音积压（含在飞合成），防语音越落后越多
                    if not self._queue.empty() or self.busy:
                        self.barge()
                elif t == "interviewer":
                    voice = self.rt.interviewer_voice
                    text = (m.get("text") or "").strip()
                    if voice and text:
                        for s in split_sentences(text):
                            self._enqueue("interviewer", "面试官", voice, s)
        finally:
            self.rt.unsubscribe(q)

    def _drain_sentences(self) -> None:
        while True:
            pieces = split_sentences(self._acc, first_soft=self._first_pending)
            if len(pieces) < 2:
                break
            agent_id, name, voice = self._current or ("", "", None)
            self._enqueue(agent_id, name, voice, pieces[0])
            self._acc = "".join(pieces[1:])
            self._first_pending = False

    def _flush_rest(self) -> None:
        if not self._acc.strip():
            return
        agent_id, name, voice = self._current or ("", "", None)
        self._enqueue(agent_id, name, voice, self._acc.strip())
        self._acc = ""

    def _voice_of(self, agent_id: str) -> dict | None:
        for m in self.rt.candidates:
            if m.id == agent_id:
                return m.persona.voice
        return None

    def _enqueue(self, agent_id: str, name: str, voice: dict | None, sentence: str) -> None:
        if not voice or not sentence.strip():
            return  # 无音色快照（未启用语音）则跳过，字幕照常
        self._queue.put_nowait((agent_id, name, voice, sentence.strip()))
        self.busy = True

    # ---------------------------------------------------------------- 合成泵
    async def _pump(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            agent_id, name, voice, sentence = item
            self.busy = True
            await self._send_json({"type": "audio_start", "agent": agent_id, "name": name})
            self._synth_task = asyncio.create_task(self.tts.synth(sentence, voice))
            try:
                audio = await self._synth_task
                if audio:
                    await self._send_bytes(audio)
            except asyncio.CancelledError:
                await self._send_json({"type": "audio_end", "agent": agent_id, "aborted": True})
                await self._send_json({"type": "barge"})
                self._synth_task = None
                self.busy = not self._queue.empty()
                continue
            except Exception as e:  # noqa: BLE001 —— 单句合成失败只跳过本句，字幕仍在
                await self._send_json({"type": "error", "text": f"语音合成失败: {e}"})
            await self._send_json({"type": "audio_end", "agent": agent_id, "aborted": False})
            self._synth_task = None
            self.busy = not self._queue.empty()
