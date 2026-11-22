"""Versioned editorial recipe value objects and canonical hash calculation."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.constants import (
    DEFAULT_AI_FALLBACK_MODELS,
    DEFAULT_AI_PRIMARY_MODEL,
    DEFAULT_AI_SEED,
    DEFAULT_AI_TEMPERATURE,
    DEFAULT_AI_TOP_P,
    DEFAULT_COPPA_PADDING_SEC,
    DEFAULT_HOOK_EXCLUDE_FIRST_SEC,
    DEFAULT_RECIPE_NAME,
    DEFAULT_RECIPE_VERSION,
    DEFAULT_SAME_MODEL_RETRIES,
    GAME_ATTENUATION_DB,
    MAX_HOOK_DURATION_SEC,
    MIN_HOOK_DURATION_SEC,
    MUSIC_DROP_DURATION_SEC,
    RECIPE_HASH_HEX_LENGTH,
    SILENCE_MARGIN_AFTER_SEC,
    SILENCE_MARGIN_BEFORE_SEC,
    SILENCE_MIN_CLIP_SEC,
    SILENCE_MIN_CUT_SEC,
    VOICE_TARGET_LUFS,
)
from src.domain.errors import DomainError
from src.domain.models import SafetySeverity

DEFAULT_HOOK_RUBRIC: str = (
    "Elige el momento de mayor emoción genuina (sorpresa, victoria épica o risa intensa) "
    "con acción visual clara en el juego. Prohibido elegir saludos iniciales, menús, "
    "pantallas de carga o tramos con anuncios o alertas COPPA."
)

DEFAULT_INAPPROPRIATE_WORDS: tuple[str, ...] = (
    "idiota",
    "estúpido",
    "estupido",
    "imbécil",
    "imbecil",
    "maldito",
    "mierda",
    "carajo",
    "puto",
    "puta",
    "pendejo",
    "cabrón",
    "cabron",
    "matar",
    "muérete",
    "muerete",
)


class SilenceRecipeConfig(BaseModel):
    """Silence removal pacing parameters for auto-editor."""

    model_config = ConfigDict(frozen=True)

    margin_before_sec: float = SILENCE_MARGIN_BEFORE_SEC
    margin_after_sec: float = SILENCE_MARGIN_AFTER_SEC
    min_cut_sec: float = SILENCE_MIN_CUT_SEC
    min_clip_sec: float = SILENCE_MIN_CLIP_SEC


class AudioRecipeConfig(BaseModel):
    """Audio normalization and ducking parameters for FFmpeg."""

    model_config = ConfigDict(frozen=True)

    voice_lufs: float = VOICE_TARGET_LUFS
    game_attenuation_db: float = GAME_ATTENUATION_DB
    music_drop_sec: float = MUSIC_DROP_DURATION_SEC

    @property
    def game_target_lufs(self) -> float:
        """Return target LUFS for game track relative to voice track."""
        return self.voice_lufs + self.game_attenuation_db


class AiRecipeConfig(BaseModel):
    """Deterministic generation and retry policy for Gemini analysis."""

    model_config = ConfigDict(frozen=True)

    primary_model: str = DEFAULT_AI_PRIMARY_MODEL
    fallback_models: tuple[str, ...] = DEFAULT_AI_FALLBACK_MODELS
    temperature: float = DEFAULT_AI_TEMPERATURE
    seed: int = DEFAULT_AI_SEED
    top_p: float = DEFAULT_AI_TOP_P
    same_model_retries: int = DEFAULT_SAME_MODEL_RETRIES


class HookRecipeConfig(BaseModel):
    """Selection bounds and editorial rubric for the opening hook clip."""

    model_config = ConfigDict(frozen=True)

    min_duration_sec: float = MIN_HOOK_DURATION_SEC
    max_duration_sec: float = MAX_HOOK_DURATION_SEC
    exclude_first_sec: float = DEFAULT_HOOK_EXCLUDE_FIRST_SEC
    forbid_coppa_overlap: bool = True
    rubric: str = DEFAULT_HOOK_RUBRIC


class PiiPatternRule(BaseModel):
    """Deterministic regex pattern for COPPA PII detection over transcripts."""

    model_config = ConfigDict(frozen=True)

    id: str
    pattern: str
    reason: str


DEFAULT_PII_PATTERNS: tuple[PiiPatternRule, ...] = (
    PiiPatternRule(
        id="pii_full_name",
        pattern=r"(?i)\b(me llamo|mi nombre completo es|mi apellido es)\s+[a-záéíóúñ]+",
        reason="Mención de nombre o apellido propio (PII)",
    ),
    PiiPatternRule(
        id="pii_location",
        pattern=(
            r"(?i)(\b(vivo en|mi dirección es|mi direccion es|"
            r"vivo por la calle|mi casa queda en|la calle donde vivo|mi barrio es)\b|"
            r"\bcalle\s+[a-záéíóúñ0-9]+\b|"
            r"\b\d{1,3}[,\s]+(?:[a-záéíóúñ]{1,4}\s+)?sur\b)"
        ),
        reason="Mención de dirección o ubicación física (PII)",
    ),
    PiiPatternRule(
        id="pii_school",
        pattern=r"(?i)\b(estudio en el colegio|mi colegio se llama|mi escuela se llama)\b",
        reason="Mención de colegio o escuela (PII)",
    ),
    PiiPatternRule(
        id="pii_phone",
        pattern=(
            r"(?i)(\b(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){7,10}\b|"
            r"\b(mi número|mi numero|mi teléfono|mi telefono|"
            r"mi celular es|llámenme al|llamenme al)\b)"
        ),
        reason="Posible número telefónico (PII)",
    ),
    PiiPatternRule(
        id="pii_contact",
        pattern=r"(?i)\b(mi whatsapp|mi instagram|mi correo es|agrégame a|agregame a)\b",
        reason="Mención de red social o contacto personal (PII)",
    ),
)


class CoppaRecipeConfig(BaseModel):
    """Deterministic lexicon, PII patterns, and mitigation padding for COPPA auditing."""

    model_config = ConfigDict(frozen=True)

    padding_sec: float = DEFAULT_COPPA_PADDING_SEC
    mitigate_severities: tuple[SafetySeverity, ...] = (SafetySeverity.CRITICAL,)
    inappropriate_words: tuple[str, ...] = DEFAULT_INAPPROPRIATE_WORDS
    pii_patterns: tuple[PiiPatternRule, ...] = DEFAULT_PII_PATTERNS


class EditorialRecipe(BaseModel):
    """Immutable versioned roadmap governing all editorial and COPPA rules."""

    model_config = ConfigDict(frozen=True)

    recipe_name: str = DEFAULT_RECIPE_NAME
    version: str = DEFAULT_RECIPE_VERSION
    silence: SilenceRecipeConfig = Field(default_factory=SilenceRecipeConfig)
    audio: AudioRecipeConfig = Field(default_factory=AudioRecipeConfig)
    ai: AiRecipeConfig = Field(default_factory=AiRecipeConfig)
    hook: HookRecipeConfig = Field(default_factory=HookRecipeConfig)
    coppa: CoppaRecipeConfig = Field(default_factory=CoppaRecipeConfig)

    @model_validator(mode="after")
    def _validate_recipe(self) -> "EditorialRecipe":
        if not self.recipe_name.strip() or not self.version.strip():
            raise DomainError("EditorialRecipe recipe_name and version must not be empty.")
        if self.coppa.padding_sec < 0:
            raise DomainError("EditorialRecipe coppa.padding_sec must be >= 0.")
        if (
            self.hook.min_duration_sec <= 0
            or self.hook.max_duration_sec < self.hook.min_duration_sec
        ):
            raise DomainError("EditorialRecipe hook duration bounds are invalid.")
        return self

    @property
    def recipe_hash(self) -> str:
        """Return a deterministic 12-char SHA-256 fingerprint of the entire recipe."""
        canonical_json = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:RECIPE_HASH_HEX_LENGTH]


def default_recipe() -> EditorialRecipe:
    """Return the default v1.0.0 kids_gaming editorial recipe."""
    return EditorialRecipe()
