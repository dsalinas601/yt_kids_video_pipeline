"""Gemini multimodal analyzer adapter using Google GenAI File API."""

import json
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from src.application.ports import AnalysisResult, TokenUsage
from src.domain.constants import (
    DEFAULT_AI_FALLBACK_MODELS,
    DEFAULT_AI_PRIMARY_MODEL,
    DEFAULT_AI_SEED,
    DEFAULT_AI_TEMPERATURE,
    DEFAULT_AI_TOP_P,
    MILLISECONDS_PER_SECOND,
)
from src.domain.errors import DomainError
from src.domain.models import EditorialPlan, sanitize_editorial_plan_dict
from src.domain.recipe import EditorialRecipe

DEFAULT_LOGGER_NAME: str = "yt_kids_video_pipeline"
DEFAULT_GEMINI_MODEL: str = DEFAULT_AI_PRIMARY_MODEL
DEFAULT_FALLBACK_MODELS: tuple[str, ...] = DEFAULT_AI_FALLBACK_MODELS
DEFAULT_REQUEST_TIMEOUT_SEC: float = 600.0
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
    "Identifica: 1) el mejor hook_clip inicial de 8 a 15 segundos (nunca sobre un anuncio "
    "ni sobre un tramo con alerta COPPA), "
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
    "siempre 0 <= start_sec < end_sec. PROHIBIDO escribir MM:SS como MMSS (por ejemplo: "
    "el minuto 03:25 es 205.0, NUNCA 325.0; el minuto 06:22 es 382.0, NUNCA 622.0)."
)


def _build_prompt_for_recipe(recipe: EditorialRecipe | None) -> str:
    if recipe is None:
        return ANALYSIS_PROMPT
    return (
        f"{ANALYSIS_PROMPT} "
        f"REGLA DE HOOK (receta {recipe.recipe_name} v{recipe.version}): "
        f"Evita elegir los primeros {recipe.hook.exclude_first_sec} segundos del video "
        f"(saludo inicial). Rúbrica del hook: {recipe.hook.rubric}"
    )


class GeminiApiAdapter:
    """MultimodalAnalyzerPort implementation backed by google-genai File API."""

    def __init__(
        self,
        api_key: str | None = None,
        client: Any | None = None,
        model_name: str = DEFAULT_GEMINI_MODEL,
        fallback_models: Sequence[str] = DEFAULT_FALLBACK_MODELS,
        temperature: float = DEFAULT_AI_TEMPERATURE,
        seed: int = DEFAULT_AI_SEED,
        top_p: float = DEFAULT_AI_TOP_P,
        same_model_retries: int = 0,
        recipe: EditorialRecipe | None = None,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
        request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
        sleep_fn: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        if client is None:
            from google import genai  # noqa: PLC0415

            client = genai.Client(api_key=api_key)
        self._client = client
        if recipe is not None:
            self._model_name = recipe.ai.primary_model
            self._fallback_models: tuple[str, ...] = recipe.ai.fallback_models
            self._temperature = recipe.ai.temperature
            self._seed = recipe.ai.seed
            self._top_p = recipe.ai.top_p
            self._same_model_retries = recipe.ai.same_model_retries
        else:
            self._model_name = model_name
            self._fallback_models = tuple(fallback_models)
            self._temperature = temperature
            self._seed = seed
            self._top_p = top_p
            self._same_model_retries = same_model_retries
        self._prompt = _build_prompt_for_recipe(recipe)
        self._poll_interval_sec = poll_interval_sec
        self._max_poll_attempts = max_poll_attempts
        self._request_timeout_sec = request_timeout_sec
        self._sleep_fn = sleep_fn
        self._logger = logger or logging.getLogger(DEFAULT_LOGGER_NAME)

    def analyze_video(self, proxy_video_path: str) -> AnalysisResult:
        """Upload proxy to File API, poll until ACTIVE, analyze with model fallback, and delete."""
        self._logger.info("Uploading proxy video to Gemini File API: %s", proxy_video_path)
        uploaded_file = self._run_with_timeout(
            lambda: self._client.files.upload(file=proxy_video_path)
        )
        try:
            self._logger.info(
                "Proxy uploaded as '%s' (state=%s); waiting for ACTIVE state...",
                uploaded_file.name,
                uploaded_file.state.name,
            )
            active_file = self._wait_until_active(uploaded_file)
            self._logger.info("Gemini file '%s' is ACTIVE.", active_file.name)
            plan, token_usage, model_used = self._generate_with_fallback(active_file)
            return AnalysisResult(
                plan=plan,
                token_usage=token_usage,
                model_used=model_used,
            )
        finally:
            self._client.files.delete(name=uploaded_file.name)

    def _build_attempt_sequence(self) -> tuple[str, ...]:
        primary_attempts = (self._model_name,) * (1 + max(0, self._same_model_retries))
        return (*primary_attempts, *self._fallback_models)

    def _generate_with_fallback(self, active_file: Any) -> tuple[EditorialPlan, TokenUsage, str]:
        models_to_try = self._build_attempt_sequence()
        timeout_ms = int(self._request_timeout_sec * MILLISECONDS_PER_SECOND)
        for idx, candidate_model in enumerate(models_to_try):
            try:
                self._logger.info(
                    "Requesting multimodal analysis from Gemini model '%s' "
                    "(temp=%.2f, seed=%d, timeout=%.0fs)...",
                    candidate_model,
                    self._temperature,
                    self._seed,
                    self._request_timeout_sec,
                )
                response = self._run_with_timeout(
                    lambda model=candidate_model: self._client.models.generate_content(
                        model=model,
                        contents=[active_file, self._prompt],
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": EditorialPlan,
                            "temperature": self._temperature,
                            "seed": self._seed,
                            "top_p": self._top_p,
                            "http_options": {"timeout": timeout_ms},
                        },
                    )
                )
                plan = self._extract_plan(response)
                token_usage = self._extract_token_usage(response)
                self._logger.info(
                    "Gemini multimodal analysis completed using model '%s'.",
                    candidate_model,
                )
                return plan, token_usage, candidate_model
            except Exception as exc:
                has_next_model = idx + 1 < len(models_to_try)
                if not (has_next_model and self._is_fallback_eligible(exc)):
                    raise
                next_model = models_to_try[idx + 1]
                self._logger.warning(
                    "Gemini model '%s' failed (%s); retrying with '%s'.",
                    candidate_model,
                    exc,
                    next_model,
                )
        raise RuntimeError("No Gemini models configured for analysis.")  # pragma: no cover

    def _run_with_timeout(self, operation: Callable[[], Any]) -> Any:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(operation)
        try:
            return future.result(timeout=self._request_timeout_sec)
        except FuturesTimeoutError as exc:
            raise TimeoutError(
                f"Gemini request timed out after {self._request_timeout_sec}s."
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _is_fallback_eligible(exc: Exception) -> bool:
        if isinstance(exc, (DomainError, TimeoutError)):
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
