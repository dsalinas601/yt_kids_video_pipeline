"""Deterministic COPPA transcript evaluation, flag union/padding, and quality gate."""

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, model_validator

from src.domain.constants import DEFAULT_COPPA_PADDING_SEC
from src.domain.errors import DomainError, InvalidTimeSegmentError
from src.domain.models import SafetyFlag, SafetySeverity, TimeSegment
from src.domain.recipe import CoppaRecipeConfig


class TranscriptSegment(BaseModel):
    """Immutable timestamped voice transcript segment."""

    model_config = ConfigDict(frozen=True)

    start_sec: float
    end_sec: float
    text: str

    @model_validator(mode="after")
    def _validate_bounds(self) -> "TranscriptSegment":
        if self.start_sec < 0 or self.end_sec <= self.start_sec:
            raise InvalidTimeSegmentError(
                f"Invalid TranscriptSegment [{self.start_sec}, {self.end_sec}]: "
                "start_sec must be >= 0 and end_sec > start_sec."
            )
        return self


def evaluate_transcript_coppa(
    segments: Sequence[TranscriptSegment],
    coppa_config: CoppaRecipeConfig,
) -> tuple[SafetyFlag, ...]:
    """Evaluate transcript segments against deterministic lexicon and PII regex rules."""
    flags: list[SafetyFlag] = []
    lexicon_regex: re.Pattern[str] | None = None
    if coppa_config.inappropriate_words:
        escaped_words = "|".join(re.escape(word) for word in coppa_config.inappropriate_words)
        lexicon_regex = re.compile(rf"(?i)\b({escaped_words})\b")

    compiled_pii = [(rule, re.compile(rule.pattern)) for rule in coppa_config.pii_patterns]

    for seg in segments:
        if lexicon_regex is not None:
            match = lexicon_regex.search(seg.text)
            if match is not None:
                flags.append(
                    SafetyFlag(
                        segment=TimeSegment(start_sec=seg.start_sec, end_sec=seg.end_sec),
                        severity=SafetySeverity.CRITICAL,
                        reason=f"Lenguaje inapropiado detectado: '{match.group(0)}'",
                        source="rule:lexicon",
                    )
                )
        for rule, pattern in compiled_pii:
            if pattern.search(seg.text) is not None:
                flags.append(
                    SafetyFlag(
                        segment=TimeSegment(start_sec=seg.start_sec, end_sec=seg.end_sec),
                        severity=SafetySeverity.CRITICAL,
                        reason=rule.reason,
                        source=f"rule:{rule.id}",
                    )
                )
    return tuple(flags)


def _pad_critical_flag(flag: SafetyFlag, padding_sec: float) -> SafetyFlag:
    if flag.severity != SafetySeverity.CRITICAL or padding_sec <= 0:
        return flag
    padded_start = max(0.0, round(flag.segment.start_sec - padding_sec, 3))
    padded_end = round(flag.segment.end_sec + padding_sec, 3)
    return SafetyFlag(
        segment=TimeSegment(start_sec=padded_start, end_sec=padded_end),
        severity=flag.severity,
        reason=flag.reason,
        source=flag.source,
    )


def _merge_two_critical_flags(first: SafetyFlag, second: SafetyFlag) -> SafetyFlag:
    merged_segment = TimeSegment(
        start_sec=first.segment.start_sec,
        end_sec=max(first.segment.end_sec, second.segment.end_sec),
    )
    reasons: list[str] = []
    for candidate_reason in (first.reason, second.reason):
        if candidate_reason not in reasons:
            reasons.append(candidate_reason)

    sources: list[str] = []
    for candidate_source in (first.source, second.source):
        for part in candidate_source.split("+"):
            if part not in sources:
                sources.append(part)

    return SafetyFlag(
        segment=merged_segment,
        severity=SafetySeverity.CRITICAL,
        reason="; ".join(reasons),
        source="+".join(sources),
    )


def merge_and_pad_safety_flags(
    rule_flags: Sequence[SafetyFlag],
    ai_flags: Sequence[SafetyFlag],
    padding_sec: float = DEFAULT_COPPA_PADDING_SEC,
) -> tuple[SafetyFlag, ...]:
    """Union deterministic rule flags with AI flags, pad CRITICAL intervals, and merge overlaps."""
    all_padded = [_pad_critical_flag(flag, padding_sec) for flag in (*rule_flags, *ai_flags)]
    critical_sorted = sorted(
        (flag for flag in all_padded if flag.severity == SafetySeverity.CRITICAL),
        key=lambda f: (f.segment.start_sec, f.segment.end_sec),
    )
    warnings_sorted = sorted(
        (flag for flag in all_padded if flag.severity == SafetySeverity.WARNING),
        key=lambda f: (f.segment.start_sec, f.segment.end_sec),
    )

    merged_critical: list[SafetyFlag] = []
    for flag in critical_sorted:
        if merged_critical and flag.segment.start_sec <= merged_critical[-1].segment.end_sec:
            merged_critical[-1] = _merge_two_critical_flags(merged_critical[-1], flag)
        else:
            merged_critical.append(flag)

    combined = sorted(
        (*merged_critical, *warnings_sorted),
        key=lambda f: (f.segment.start_sec, f.segment.end_sec),
    )
    return tuple(combined)


def verify_coppa_gate(
    rule_flags: Sequence[SafetyFlag],
    final_flags: Sequence[SafetyFlag],
) -> None:
    """Ensure every deterministic CRITICAL rule hit is covered by final CRITICAL flags."""
    critical_finals = [flag for flag in final_flags if flag.severity == SafetySeverity.CRITICAL]
    for rule_flag in rule_flags:
        is_covered = any(
            final.segment.start_sec <= rule_flag.segment.start_sec
            and final.segment.end_sec >= rule_flag.segment.end_sec
            for final in critical_finals
        )
        if not is_covered:
            raise DomainError(
                f"COPPA gate failed: rule flag [{rule_flag.segment.start_sec}, "
                f"{rule_flag.segment.end_sec}] ({rule_flag.source}) not covered."
            )
