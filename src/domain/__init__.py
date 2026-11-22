"""Domain layer exports."""

from src.domain.coppa_rules import (
    TranscriptSegment,
    evaluate_transcript_coppa,
    merge_and_pad_safety_flags,
    verify_coppa_gate,
)
from src.domain.errors import (
    DomainError,
    InvalidAudioTracksError,
    InvalidTimeSegmentError,
    ThumbnailArtError,
)
from src.domain.models import (
    CleanVideo,
    DynamicEvent,
    EditorialPlan,
    EventCategory,
    PipelineOutput,
    RawRecording,
    SafetyFlag,
    SafetySeverity,
    TimeSegment,
    VideoMetadata,
    ZoomType,
)
from src.domain.recipe import (
    AiRecipeConfig,
    AudioRecipeConfig,
    CoppaRecipeConfig,
    EditorialRecipe,
    HookRecipeConfig,
    PiiPatternRule,
    SilenceRecipeConfig,
    default_recipe,
)

__all__ = [
    "AiRecipeConfig",
    "AudioRecipeConfig",
    "CleanVideo",
    "CoppaRecipeConfig",
    "DomainError",
    "DynamicEvent",
    "EditorialPlan",
    "EditorialRecipe",
    "EventCategory",
    "HookRecipeConfig",
    "InvalidAudioTracksError",
    "InvalidTimeSegmentError",
    "PiiPatternRule",
    "PipelineOutput",
    "RawRecording",
    "SafetyFlag",
    "SafetySeverity",
    "SilenceRecipeConfig",
    "ThumbnailArtError",
    "TimeSegment",
    "TranscriptSegment",
    "VideoMetadata",
    "ZoomType",
    "default_recipe",
    "evaluate_transcript_coppa",
    "merge_and_pad_safety_flags",
    "verify_coppa_gate",
]
