"""Gemini multimodal analyzer adapter using Google GenAI File API."""

import time
from collections.abc import Callable
from typing import Any

from src.application.ports import AnalysisResult, TokenUsage
from src.domain.models import EditorialPlan

DEFAULT_GEMINI_MODEL: str = "gemini-3.8-flash"
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
    "internas del juego, menús ni pantallas de carga. Mínimo 2 segundos."
)


class GeminiApiAdapter:
    """MultimodalAnalyzerPort implementation backed by google-genai File API."""

    def __init__(
        self,
        api_key: str | None = None,
        client: Any | None = None,
        model_name: str = DEFAULT_GEMINI_MODEL,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            from google import genai  # noqa: PLC0415

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model_name = model_name
        self._poll_interval_sec = poll_interval_sec
        self._max_poll_attempts = max_poll_attempts
        self._sleep_fn = sleep_fn

    def analyze_video(self, proxy_video_path: str) -> AnalysisResult:
        """Upload proxy to File API, poll until ACTIVE, analyze, and delete in finally."""
        uploaded_file = self._client.files.upload(file=proxy_video_path)
        try:
            active_file = self._wait_until_active(uploaded_file)
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[active_file, ANALYSIS_PROMPT],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": EditorialPlan,
                },
            )
            plan = self._extract_plan(response)
            token_usage = self._extract_token_usage(response)
            return AnalysisResult(plan=plan, token_usage=token_usage)
        finally:
            self._client.files.delete(name=uploaded_file.name)

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

    @staticmethod
    def _extract_plan(response: Any) -> EditorialPlan:
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
