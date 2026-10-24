"""V1.0 语音冒烟：文字 → edge-tts mp3 → 解码重采样 → sherpa ASR → 文字 回环。

用法：.venv/bin/python scripts/voice_smoke.py
"""

import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import soundfile as sf

from app.core.config import Settings
from app.services.voice.tts_edge import EdgeTTS

TEXT = "各位好，我是候选人陈锐，我先用数据说话。这道题的关键是渠道库存。"
VOICE = {"provider_voice": "zh-CN-YunjianNeural", "rate": "+8%", "pitch": "-2Hz"}
TMP = Path("/tmp/gi_voice_smoke")


def asr_recognize(pcm16k: np.ndarray, models_dir: Path) -> str:
    import sherpa_onnx

    model_dir = models_dir / "sherpa-onnx-paraformer-zh-small-2024-03-09"
    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
    )
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, pcm16k)
    recognizer.decode_stream(stream)
    return stream.result.text


def mp3_to_pcm16k(mp3_path: Path) -> np.ndarray:
    data, sr = sf.read(mp3_path, dtype="float32")  # 24kHz mono
    n_in, n_out = len(data), int(len(data) * 16000 / sr)
    return np.interp(np.linspace(0, n_in - 1, n_out), np.arange(n_in), data).astype(np.float32)


def save_wav(pcm: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())


async def main() -> None:
    TMP.mkdir(exist_ok=True)
    s = Settings(_env_file=None)

    # 1) TTS
    t0 = time.monotonic()
    mp3 = TMP / "smoke.mp3"
    mp3.write_bytes(await EdgeTTS(s).synth(TEXT, VOICE))
    print(f"[TTS] {mp3}  {mp3.stat().st_size/1024:.0f}KB  {time.monotonic()-t0:.2f}s")

    # 2) 解码重采样 24k→16k
    pcm = mp3_to_pcm16k(mp3)
    save_wav(pcm, TMP / "smoke_16k.wav")
    print(f"[PCM] {len(pcm)/16000:.1f}s @16kHz → {TMP/'smoke_16k.wav'}")

    # 3) ASR 回环
    t0 = time.monotonic()
    heard = asr_recognize(pcm, Path(s.voice_models_dir))
    print(f"[ASR] {time.monotonic()-t0:.2f}s  识别: {heard}")
    print(f"[原文] {TEXT}")

    hit = sum(1 for ch in heard if ch in TEXT)
    rate = hit / max(len(heard), 1)
    print(f"[回环] 字符命中率 {rate:.0%}  {'✅ 通过' if rate >= 0.8 else '❌ 未达标'}")


if __name__ == "__main__":
    asyncio.run(main())
