"""Gemini multimodal analyzer adapter using Google GenAI File API."""

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from src.application.ports import AnalysisResult, TokenUsage
from src.domain.errors import DomainError
from src.domain.models import EditorialPlan, sanitize_editorial_plan_dict

DEFAULT_LOGGER_NAME: str = "yt_kids_video_pipeline"
DEFAULT_GEMINI_MODEL: str = "gemini-3.8-flash"
DEFAULT_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
RETRYABLE_ERROR_MARKERS: tuple[str, ...] = (
    "503",
    "UNAVAILABLE",
    "429",
    "RESOURCE_EXHAUSTED",
    "HIGH DEMAND",
    "OVERLOADED",
)
DEFAULT_POLL_INTERVAL_SEC: float = 2.0
DEFAULT_MAX_POLL_ATTEMPTS: int = 150
PROCESSING_STATE_NAME: str = "PROCESSING"
ACTIVE_STATE_NAME: str = "ACTIVE"

ANALYSIS_PROMPT: str = (
    "Analiza este video proxy de gaming infantil en español para YouTube Kids. "
    "Identifica: 1) el mejor hook_clip inicial de 8 a 15 segundos (nunca sobre un anuncio), "
    "2) eventos dinámicos clave (SURPRISE, LAUGH, VICTORY, FAIL) con tipo de zoom "
    "(CORNER_ZOOM_15 o FACE_CLOSEUP_50) y texto corto opcional de máximo 2 palabras, "
    "3) auditoría COPPA de safety_flags (WARNING o CRITICAL por datos personales o "
    "lenguaje inapropiado), 4) metadata sugerida con títulos, descripción, "
    "thumbnail_timestamp, thumbnail_text, game_title (nombre del videojuego) y "
    "thumbnail_scene (descripción breve de la escena visual del juego en ese momento, "
    "sin describir personas reales), y 5) ad_segments: rangos donde aparece publicidad dentro del "
    "juego (pantalla completa de otro juego/producto, etiquetas 'Ad'/'Anuncio', botones "
    "de cerrar 'X' o cuenta regresiva, botones de tienda externa). NO marcar tiendas "
    "internas del juego, menús ni pantallas de carga. Mínimo 2 segundos. "
    "IMPORTANTE: Todos los tiempos (start_sec, end_sec, thumbnail_timestamp) deben "
    "expresarse en segundos totales desde el inicio del video (MM*60 + SS), cumpliendo "
    "siempre 0 <= start_sec < end_sec."
)


class GeminiApiAdapter:
    """MultimodalAnalyzerPort implementation backed by google-genai File API."""

    def __init__(
        self,
        api_key: str | None = None,
        client: Any | None = None,
        model_name: str = DEFAULT_GEMINI_MODEL,
        fallback_models: Sequence[str] = DEFAULT_FALLBACK_MODELS,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
        sleep_fn: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if client is None:
            from google import genai  # noqa: PLC0415

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model_name = model_name
        self._fallback_models: tuple[str, ...] = tuple(fallback_models)
        self._poll_interval_sec = poll_interval_sec
        self._max_poll_attempts = max_poll_attempts
        self._sleep_fn = sleep_fn
        self._logger = logger or logging.getLogger(DEFAULT_LOGGER_NAME)

    def analyze_video(self, proxy_video_path: str) -> AnalysisResult:
        """Upload proxy to File API, poll until ACTIVE, analyze with model fallback, and delete."""
        uploaded_file = self._client.files.upload(file=proxy_video_path)
        try:
            active_file = self._wait_until_active(uploaded_file)
            plan, token_usage = self._generate_with_fallback(active_file)
            return AnalysisResult(plan=plan, token_usage=token_usage)
        finally:
            self._client.files.delete(name=uploaded_file.name)

    def _generate_with_fallback(self, active_file: Any) -> tuple[EditorialPlan, TokenUsage]:
        models_to_try = (self._model_name, *self._fallback_models)
        for idx, candidate_model in enumerate(models_to_try):
            try:
                response = self._client.models.generate_content(
                    model=candidate_model,
                    contents=[active_file, ANALYSIS_PROMPT],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": EditorialPlan,
                    },
                )
                plan = self._extract_plan(response)
                token_usage = self._extract_token_usage(response)
                self._logger.info(
                    "Gemini multimodal analysis completed using model '%s'.",
                    candidate_model,
                )
                return plan, token_usage
            except Exception as exc:
                has_next_model = idx + 1 < len(models_to_try)
                if not (has_next_model and self._is_fallback_eligible(exc)):
                    raise
                next_model = models_to_try[idx + 1]
                self._logger.warning(
                    "Gemini model '%s' failed (%s); falling back to '%s'.",
                    candidate_model,
                    exc,
                    next_model,
                )
        raise RuntimeError("No Gemini models configured for analysis.")  # pragma: no cover

    @staticmethod
    def _is_fallback_eligible(exc: Exception) -> bool:
        if isinstance(exc, DomainError):
            return True
        code = getattr(exc, "code", None)
        if isinstance(code, int) and code in RETRYABLE_STATUS_CODES:
            return True
        message = str(exc).upper()
        return any(marker in message for marker in RETRYABLE_ERROR_MARKERS)

    def _wait_until_active(self, uploaded_file: Any) -> Any:
        current_file = uploaded_file
        attempts = 0
        while current_file.state.name == PROCESSING_STATE_NAME:
            if attempts >= self._max_poll_attempts:
                raise TimeoutError(
                    f"Timed out waiting for Gemini file '{current_file.name}' to become ACTIVE."
                )
            self._sleep_fn(self._poll_interval_sec)
            current_file = self._client.files.get(name=current_file.name)
            attempts += 1

        if current_file.state.name != ACTIVE_STATE_NAME:
            raise RuntimeError(
                f"Gemini File API processing ended with state '{current_file.state.name}'."
            )
        return current_file

    def _extract_plan(self, response: Any) -> EditorialPlan:
        _, warnings = sanitize_editorial_plan_dict(json.loads(response.text))
        for warning_msg in warnings:
            self._logger.warning("%s", warning_msg)
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, EditorialPlan):
            return parsed
        return EditorialPlan.model_validate_json(response.text)

    @staticmethod
    def _extract_token_usage(response: Any) -> TokenUsage:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return TokenUsage(input_tokens=0, output_tokens=0)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
