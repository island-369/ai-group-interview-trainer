"""silero VAD 端点检测：喂 16k PCM 流，产出完整语音段（说话结束即出段）。"""

from pathlib import Path


class SileroVAD:
    def __init__(self, models_dir: str | Path, *, min_silence: float = 0.7,
                 min_speech: float = 0.25, threshold: float = 0.5):
        # min_silence 0.7s：合并句中逗号停顿（~0.5s），只在真正说完时出段
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(Path(models_dir) / "silero_vad.onnx")
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = min_silence
        config.silero_vad.min_speech_duration = min_speech
        config.sample_rate = 16000
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)

    def feed(self, f32_chunk) -> list:
        """喂一段 float32 16k 采样；返回（可能多个）完整语音段。"""
        self._vad.accept_waveform(f32_chunk)
        return self._drain()

    def flush(self) -> list:
        """强制收尾（用户点"说完"时调用），返回残余段。"""
        self._vad.flush()
        return self._drain()

    def _drain(self) -> list:
        out = []
        while not self._vad.empty():
            out.append(self._vad.front.samples)
            self._vad.pop()
        return out
