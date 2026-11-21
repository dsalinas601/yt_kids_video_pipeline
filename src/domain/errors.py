"""Typed domain errors for yt_kids_video_pipeline."""


class DomainError(Exception):
    """Base error for all business rule and invariant violations."""


class InvalidAudioTracksError(DomainError):
    """Raised when an input recording has fewer than the required audio streams."""


class InvalidTimeSegmentError(DomainError):
    """Raised when a TimeSegment has invalid or non-positive bounds."""


class ThumbnailArtError(DomainError):
    """Raised when AI thumbnail background generation fails or is blocked."""
