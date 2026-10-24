"""sherpa-onnx 离线 ASR 封装：按整段发言解码（端点检测后一次解码，M 系列 CPU RTF≈0.1）。

懒加载单例；模型缺失时 available=False，语音层自动降级。
"""

import threading
from pathlib import Path

MODEL_DIR = "sherpa-onnx-paraformer-zh-small-2024-03-09"


def int16_bytes_to_f32(b: bytes):
    import numpy as np
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0


class LocalASR:
    def __init__(self, models_dir: str | Path):
        self._root = Path(models_dir)
        self._recognizer = None
        self._lock = threading.Lock()
        self.available = (self._root / MODEL_DIR / "model.int8.onnx").is_file()

    def transcribe(self, pcm16k_f32) -> str:
        """float32 16kHz 单频段 → 文本（阻塞，调用方须放线程池）。"""
        with self._lock:
            self._ensure()
            stream = self._recognizer.create_stream()
            stream.accept_waveform(16000, pcm16k_f32)
            self._recognizer.decode_stream(stream)
            return stream.result.text.strip()

    def _ensure(self) -> None:
        if self._recognizer is not None:
            return
        import sherpa_onnx
        d = self._root / MODEL_DIR
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=2, sample_rate=16000, feature_dim=80,
        )
