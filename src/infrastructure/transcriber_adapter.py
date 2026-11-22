"""Faster-Whisper voice transcriber adapter for deterministic COPPA auditing."""

from typing import Any

from src.domain.coppa_rules import TranscriptSegment

DEFAULT_WHISPER_MODEL_SIZE: str = "large-v3"
DEFAULT_WHISPER_LANGUAGE: str = "es"
DEFAULT_WHISPER_BEAM_SIZE: int = 5


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

    def _get_or_create_model(self) -> Any:
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel  # noqa: PLC0415

        try:
            self._model = WhisperModel(
                self._model_size,
                device="cuda",
                compute_type="float16",
            )
        except Exception:
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    def transcribe(self, audio_wav_path: str) -> tuple[TranscriptSegment, ...]:
        """Transcribe 16kHz mono voice track with temperature=0.0 for deterministic output."""
        model = self._get_or_create_model()
        raw_segments, _ = model.transcribe(
            audio_wav_path,
            language=self._language,
            temperature=0.0,
            beam_size=self._beam_size,
            vad_filter=True,
        )
        segments: list[TranscriptSegment] = []
        for item in raw_segments:
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
