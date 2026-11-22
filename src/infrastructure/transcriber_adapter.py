"""Faster-Whisper voice transcriber adapter for deterministic COPPA auditing."""

import ctypes
import site
import wave
from pathlib import Path
from typing import Any

from src.domain.coppa_rules import TranscriptSegment

DEFAULT_WHISPER_MODEL_SIZE: str = "small"
DEFAULT_WHISPER_LANGUAGE: str = "es"
DEFAULT_WHISPER_BEAM_SIZE: int = 5
PCM_INT16_SCALE: float = 32768.0
NVIDIA_SO_PATTERNS: tuple[str, ...] = (
    "nvidia/cublas/lib/libcublasLt.so*",
    "nvidia/cublas/lib/libcublas.so*",
    "nvidia/cudnn/lib/libcudnn*.so*",
)


def _preload_nvidia_cuda_libs(search_roots: tuple[Path, ...] | None = None) -> None:
    """Preload pip-installed NVIDIA cuBLAS/cuDNN shared libraries with RTLD_GLOBAL for Colab."""
    roots = (
        search_roots if search_roots is not None else tuple(Path(p) for p in site.getsitepackages())
    )
    rtld_global = getattr(ctypes, "RTLD_GLOBAL", 0)
    for root in roots:
        if not root.exists():
            continue
        for pattern in NVIDIA_SO_PATTERNS:
            for so_path in sorted(root.glob(pattern)):
                try:
                    ctypes.CDLL(str(so_path), mode=rtld_global)
                except OSError:
                    continue


def _load_pcm16_wav_as_float32_array(audio_wav_path: str) -> Any:
    """Load a 16kHz mono PCM16 WAV into a 1D float32 numpy array, bypassing PyAV."""
    wav_file = Path(audio_wav_path)
    if not wav_file.exists():
        return audio_wav_path
    import numpy as np  # noqa: PLC0415

    with wave.open(str(wav_file), "rb") as wf:
        pcm_bytes = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / PCM_INT16_SCALE


class FasterWhisperAdapter:
    """TranscriberPort implementation backed by faster-whisper with GPU->CPU fallback."""

    def __init__(
        self,
        model_size: str = DEFAULT_WHISPER_MODEL_SIZE,
        language: str = DEFAULT_WHISPER_LANGUAGE,
        beam_size: int = DEFAULT_WHISPER_BEAM_SIZE,
        model: Any | None = None,
    ) -> None:
        self._model_size = model_size
        self._language = language
        self._beam_size = beam_size
        self._model = model

    def _create_cpu_model(self) -> Any:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        self._model = WhisperModel(
            self._model_size,
            device="cpu",
            compute_type="int8",
        )
        return self._model

    def _get_or_create_model(self) -> Any:
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel  # noqa: PLC0415

        _preload_nvidia_cuda_libs()
        try:
            self._model = WhisperModel(
                self._model_size,
                device="cuda",
                compute_type="float16",
            )
        except Exception:
            return self._create_cpu_model()
        return self._model

    def _run_raw_transcribe(self, model: Any, audio_input: Any) -> list[Any]:
        raw_segments, _ = model.transcribe(
            audio_input,
            language=self._language,
            temperature=0.0,
            beam_size=self._beam_size,
            vad_filter=True,
        )
        return list(raw_segments)

    def transcribe(self, audio_wav_path: str) -> tuple[TranscriptSegment, ...]:
        """Transcribe 16kHz mono voice track with temperature=0.0 and lazy-CUDA fallback."""
        audio_input = _load_pcm16_wav_as_float32_array(audio_wav_path)
        model = self._get_or_create_model()
        try:
            consumed_segments = self._run_raw_transcribe(model, audio_input)
        except RuntimeError:
            cpu_model = self._create_cpu_model()
            consumed_segments = self._run_raw_transcribe(cpu_model, audio_input)

        segments: list[TranscriptSegment] = []
        for item in consumed_segments:
            text = str(getattr(item, "text", "")).strip()
            start_sec = max(0.0, float(getattr(item, "start", 0.0)))
            end_sec = float(getattr(item, "end", 0.0))
            if not text or end_sec <= start_sec:
                continue
            segments.append(
                TranscriptSegment(
                    start_sec=round(start_sec, 3),
                    end_sec=round(end_sec, 3),
                    text=text,
                )
            )
        return tuple(segments)
