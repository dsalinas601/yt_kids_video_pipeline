"""Immutable domain entities, value objects, and pure business rules."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.constants import (
    AD_MAX_DURATION_SEC,
    AD_MIN_DURATION_SEC,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_POINT_EVENT_DURATION_SEC,
    MAX_EVENT_TEXT_WORDS,
    MAX_HOOK_DURATION_SEC,
    MIN_AUDIO_TRACKS,
    MIN_EVENT_GAP_SEC,
    MIN_HOOK_DURATION_SEC,
)
from src.domain.errors import (
    DomainError,
    InvalidAudioTracksError,
    InvalidTimeSegmentError,
)


class EventCategory(StrEnum):
    """Supported emotional/gameplay event categories."""

    SURPRISE = "SURPRISE"
    LAUGH = "LAUGH"
    VICTORY = "VICTORY"
    FAIL = "FAIL"


class ZoomType(StrEnum):
    """Supported corner-anchored zoom presets."""

    CORNER_ZOOM_15 = "CORNER_ZOOM_15"
    FACE_CLOSEUP_50 = "FACE_CLOSEUP_50"


class SafetySeverity(StrEnum):
    """COPPA safety audit severities."""

    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TimeSegment(BaseModel):
    """Immutable time interval with validated positive duration."""

    model_config = ConfigDict(frozen=True)

    start_sec: float = Field(
        description="Inicio del segmento en segundos totales desde 0.0 (MM*60 + SS >= 0)."
    )
    end_sec: float = Field(
        description="Fin del segmento en segundos totales (MM*60 + SS, estrictamente > start_sec)."
    )

    @model_validator(mode="after")
    def _validate_bounds(self) -> "TimeSegment":
        if self.start_sec < 0 or self.end_sec <= self.start_sec:
            raise InvalidTimeSegmentError(
                f"Invalid TimeSegment [{self.start_sec}, {self.end_sec}]: "
                "start_sec must be >= 0 and end_sec > start_sec."
            )
        return self

    @property
    def duration(self) -> float:
        """Return segment duration in seconds."""
        return self.end_sec - self.start_sec

    def overlaps(self, other: "TimeSegment") -> bool:
        """Return whether two time segments overlap with positive intersection."""
        return self.start_sec < other.end_sec and other.start_sec < self.end_sec

    def contains(self, timestamp_sec: float) -> bool:
        """Return whether timestamp_sec lies within [start_sec, end_sec]."""
        return self.start_sec <= timestamp_sec <= self.end_sec


class RawRecording(BaseModel):
    """Validated metadata for the original multitrack .mkv recording."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    width: int
    height: int
    fps: float
    duration_sec: float
    audio_streams: int

    @model_validator(mode="after")
    def _validate_recording(self) -> "RawRecording":
        if self.audio_streams < MIN_AUDIO_TRACKS:
            raise InvalidAudioTracksError(
                f"Recording '{self.file_path}' has {self.audio_streams} audio stream(s); "
                f"at least {MIN_AUDIO_TRACKS} are required."
            )
        if (
            not self.file_path.strip()
            or self.width <= 0
            or self.height <= 0
            or self.fps <= 0
            or self.duration_sec <= 0
        ):
            raise DomainError("RawRecording metadata contains invalid or empty values.")
        return self


class CleanVideo(BaseModel):
    """Validated metadata for clean_base.mp4 after silence removal."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    width: int = CANVAS_WIDTH
    height: int = CANVAS_HEIGHT
    fps: float = 60.0
    duration_sec: float
    audio_streams: int = MIN_AUDIO_TRACKS

    @model_validator(mode="after")
    def _validate_clean_video(self) -> "CleanVideo":
        if self.audio_streams < MIN_AUDIO_TRACKS:
            raise InvalidAudioTracksError(
                f"CleanVideo '{self.file_path}' has {self.audio_streams} audio stream(s); "
                f"at least {MIN_AUDIO_TRACKS} are required."
            )
        if (
            not self.file_path.strip()
            or self.width <= 0
            or self.height <= 0
            or self.fps <= 0
            or self.duration_sec <= 0
        ):
            raise DomainError("CleanVideo metadata contains invalid or empty values.")
        return self


class DynamicEvent(BaseModel):
    """Editorial dynamic event detected over CleanVideo."""

    model_config = ConfigDict(frozen=True)

    segment: TimeSegment
    category: EventCategory
    zoom_type: ZoomType
    impact_text: str | None = None

    @model_validator(mode="after")
    def _validate_impact_text(self) -> "DynamicEvent":
        if self.impact_text is None:
            return self
        normalized = self.impact_text.strip()
        if not normalized:
            object.__setattr__(self, "impact_text", None)
            return self
        words = normalized.split()
        if len(words) > MAX_EVENT_TEXT_WORDS:
            raise DomainError(
                f"DynamicEvent impact_text '{normalized}' exceeds {MAX_EVENT_TEXT_WORDS} words."
            )
        object.__setattr__(self, "impact_text", normalized)
        return self


class SafetyFlag(BaseModel):
    """COPPA compliance or child-safety flag detected on CleanVideo."""

    model_config = ConfigDict(frozen=True)

    segment: TimeSegment
    severity: SafetySeverity
    reason: str

    @model_validator(mode="after")
    def _validate_reason(self) -> "SafetyFlag":
        if not self.reason.strip():
            raise DomainError("SafetyFlag reason must not be empty.")
        return self


class VideoMetadata(BaseModel):
    """Suggested YouTube Kids publishing metadata."""

    model_config = ConfigDict(frozen=True)

    titles: tuple[str, ...]
    description: str
    thumbnail_timestamp: float
    thumbnail_text: str = ""
    tags: tuple[str, ...] = ()
    game_title: str = ""
    thumbnail_scene: str = ""

    @model_validator(mode="after")
    def _validate_metadata(self) -> "VideoMetadata":
        if not self.titles or any(not title.strip() for title in self.titles):
            raise DomainError("VideoMetadata must include at least one non-empty title.")
        if self.thumbnail_timestamp < 0:
            raise DomainError("VideoMetadata thumbnail_timestamp must be >= 0.")
        return self


def _normalize_ad_segments(segments: tuple[TimeSegment, ...]) -> tuple[TimeSegment, ...]:
    valid = [
        segment
        for segment in segments
        if AD_MIN_DURATION_SEC <= segment.duration <= AD_MAX_DURATION_SEC
    ]
    sorted_ads = sorted(valid, key=lambda seg: (seg.start_sec, seg.end_sec))
    merged: list[TimeSegment] = []
    for seg in sorted_ads:
        if merged and seg.start_sec <= merged[-1].end_sec:
            merged[-1] = TimeSegment(
                start_sec=merged[-1].start_sec,
                end_sec=max(merged[-1].end_sec, seg.end_sec),
            )
        else:
            merged.append(seg)
    return tuple(merged)


def sanitize_editorial_plan_dict(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Sanitize raw JSON dictionary for EditorialPlan.

    - Logs warning if mandatory ``hook_clip`` has invalid bounds (caller/validator will reject).
    - Discards inverted or zero-length ``ad_segments`` and ``safety_flags`` (never reorders).
    - Rescues point ``dynamic_events`` (``start_sec == end_sec >= 0``) by expanding ``end_sec``
      by ``DEFAULT_POINT_EVENT_DURATION_SEC`` (+3.0s), and discards inverted/negative ones.
    """
    sanitized = dict(data)
    warnings: list[str] = []

    raw_hook = sanitized.get("hook_clip")
    if isinstance(raw_hook, dict):
        hook_start = float(raw_hook["start_sec"])
        hook_end = float(raw_hook["end_sec"])
        if hook_start < 0 or hook_end <= hook_start:
            warnings.append(f"Invalid mandatory hook_clip TimeSegment [{hook_start}, {hook_end}].")

    clean_ads: list[Any] = []
    for idx, item in enumerate(sanitized.get("ad_segments") or ()):
        if isinstance(item, dict):
            start_sec = float(item["start_sec"])
            end_sec = float(item["end_sec"])
            if start_sec < 0 or end_sec <= start_sec:
                warnings.append(
                    f"Discarded invalid TimeSegment [{start_sec}, {end_sec}] in ad_segments[{idx}]."
                )
                continue
        clean_ads.append(item)
    sanitized["ad_segments"] = clean_ads

    clean_events: list[Any] = []
    for idx, item in enumerate(sanitized.get("dynamic_events") or ()):
        if isinstance(item, dict):
            seg = item["segment"]
            start_sec = float(seg["start_sec"])
            end_sec = float(seg["end_sec"])
            category = item.get("category", "UNKNOWN")
            if start_sec >= 0 and end_sec == start_sec:
                expanded_end = start_sec + DEFAULT_POINT_EVENT_DURATION_SEC
                clean_events.append(
                    {
                        **item,
                        "segment": {"start_sec": start_sec, "end_sec": expanded_end},
                    }
                )
                warnings.append(
                    f"Expanded point TimeSegment [{start_sec}, {end_sec}] to "
                    f"[{start_sec}, {expanded_end}] in dynamic_events[{idx}] "
                    f"(category={category})."
                )
                continue
            if start_sec < 0 or end_sec < start_sec:
                warnings.append(
                    f"Discarded invalid TimeSegment [{start_sec}, {end_sec}] "
                    f"in dynamic_events[{idx}] (category={category})."
                )
                continue
        clean_events.append(item)
    sanitized["dynamic_events"] = clean_events

    clean_flags: list[Any] = []
    for idx, item in enumerate(sanitized.get("safety_flags") or ()):
        if isinstance(item, dict):
            seg = item["segment"]
            start_sec = float(seg["start_sec"])
            end_sec = float(seg["end_sec"])
            if start_sec < 0 or end_sec <= start_sec:
                severity = item.get("severity", "UNKNOWN")
                reason = item.get("reason", "")
                warnings.append(
                    f"Discarded invalid TimeSegment [{start_sec}, {end_sec}] "
                    f"in safety_flags[{idx}] (severity={severity}, reason={reason})."
                )
                continue
        clean_flags.append(item)
    sanitized["safety_flags"] = clean_flags

    return sanitized, warnings


class EditorialPlan(BaseModel):
    """Aggregate root enforcing hook bounds, ad rules, and non-overlapping dynamic events."""

    model_config = ConfigDict(frozen=True)

    hook_clip: TimeSegment
    dynamic_events: tuple[DynamicEvent, ...] = Field(default=())
    safety_flags: tuple[SafetyFlag, ...] = Field(default=())
    ad_segments: tuple[TimeSegment, ...] = Field(default=())
    metadata: VideoMetadata

    @model_validator(mode="before")
    @classmethod
    def _sanitize_raw_segments(cls, data: dict[str, Any]) -> dict[str, Any]:
        sanitized, _ = sanitize_editorial_plan_dict(data)
        return sanitized

    @model_validator(mode="after")
    def _enforce_editorial_rules(self) -> "EditorialPlan":
        if self.hook_clip.duration < MIN_HOOK_DURATION_SEC:
            raise DomainError(
                f"Hook clip duration {self.hook_clip.duration}s is below minimum "
                f"{MIN_HOOK_DURATION_SEC}s."
            )
        if self.hook_clip.duration > MAX_HOOK_DURATION_SEC:
            truncated = TimeSegment(
                start_sec=self.hook_clip.start_sec,
                end_sec=self.hook_clip.start_sec + MAX_HOOK_DURATION_SEC,
            )
            object.__setattr__(self, "hook_clip", truncated)

        normalized_ads = _normalize_ad_segments(self.ad_segments)
        object.__setattr__(self, "ad_segments", normalized_ads)

        if any(ad.overlaps(self.hook_clip) for ad in normalized_ads):
            raise DomainError("EditorialPlan hook_clip must not overlap any ad_segment.")

        sorted_events = sorted(
            (
                event
                for event in self.dynamic_events
                if not any(ad.overlaps(event.segment) for ad in normalized_ads)
            ),
            key=lambda event: (event.segment.start_sec, event.segment.end_sec),
        )
        kept: list[DynamicEvent] = []
        for event in sorted_events:
            if (
                not kept
                or (event.segment.start_sec - kept[-1].segment.end_sec) >= MIN_EVENT_GAP_SEC
            ):
                kept.append(event)
        object.__setattr__(self, "dynamic_events", tuple(kept))

        if any(ad.contains(self.metadata.thumbnail_timestamp) for ad in normalized_ads):
            midpoint = (self.hook_clip.start_sec + self.hook_clip.end_sec) / 2.0
            relocated = self.metadata.model_copy(update={"thumbnail_timestamp": midpoint})
            object.__setattr__(self, "metadata", relocated)
        return self

    @property
    def critical_safety_flags(self) -> tuple[SafetyFlag, ...]:
        """Return all COPPA flags with CRITICAL severity."""
        return tuple(flag for flag in self.safety_flags if flag.severity == SafetySeverity.CRITICAL)

    @property
    def has_critical_flags(self) -> bool:
        """Return whether the plan contains at least one CRITICAL safety flag."""
        return bool(self.critical_safety_flags)

    @property
    def total_ad_sec_removed(self) -> float:
        """Return total duration in seconds of normalized ad segments."""
        return sum(segment.duration for segment in self.ad_segments)


class PipelineOutput(BaseModel):
    """Verified output paths produced by the pipeline."""

    model_config = ConfigDict(frozen=True)

    final_video_path: str
    thumbnail_path: str
    metadata_audit_path: str
    critical_flags_mitigated: int = 0

    @model_validator(mode="after")
    def _validate_output(self) -> "PipelineOutput":
        if (
            not self.final_video_path.strip()
            or not self.thumbnail_path.strip()
            or not self.metadata_audit_path.strip()
        ):
            raise DomainError("PipelineOutput paths must be non-empty.")
        if self.critical_flags_mitigated < 0:
            raise DomainError("critical_flags_mitigated must be >= 0.")
        return self
