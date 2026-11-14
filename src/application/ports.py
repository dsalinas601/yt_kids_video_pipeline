"""Application layer ports (Protocol interfaces) and DTOs."""

from dataclasses import dataclass
from typing import Any, Protocol

from src.domain.ffmpeg_builder import FilterComplexSpec
from src.domain.models import CleanVideo, EditorialPlan, RawRecording


@dataclass(frozen=True)
class TokenUsage:
    """Input and output token counts reported by the multimodal analyzer."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class AnalysisResult:
    """Result returned by MultimodalAnalyzerPort."""

    plan: EditorialPlan
    token_usage: TokenUsage


@dataclass(frozen=True)
class VideoWorkspacePaths:
    """Resolved workspace paths for a given video_id."""

    clean_base_path: str
    proxy_path: str
    editorial_plan_path: str
    final_video_path: str
    frame_image_path: str
    thumbnail_path: str
    metadata_audit_path: str
    intro_path: str
    bgm_path: str
    font_path: str
    sfx_dir: str


class SilenceRemoverPort(Protocol):
    """Port for removing silences on voice stream 0:a:0."""

    def remove_silences(self, raw_recording: RawRecording, output_clean_path: str) -> CleanVideo:
        """Remove silences analyzing voice track 0:a:0 and export CleanVideo."""


class MultimodalAnalyzerPort(Protocol):
    """Port for multimodal editorial and COPPA analysis on a proxy video."""

    def analyze_video(self, proxy_video_path: str) -> AnalysisResult:
        """Analyze proxy video and return validated EditorialPlan with token metrics."""


class MediaProcessorPort(Protocol):
    """Port for probing media, generating proxies, extracting frames, and rendering."""

    def probe_recording(self, input_path: str) -> RawRecording:
        """Inspect raw recording and return validated RawRecording."""

    def probe_clean_video(self, clean_video_path: str) -> CleanVideo:
        """Inspect cached clean_base.mp4 and return validated CleanVideo."""

    def generate_proxy(self, clean_video_path: str, proxy_output_path: str) -> str:
        """Generate a lightweight 360p/15fps proxy video from clean_base.mp4."""

    def extract_frame(
        self,
        video_path: str,
        timestamp_sec: float,
        output_image_path: str,
    ) -> str:
        """Extract a single frame at timestamp_sec."""

    def render_final_video(
        self,
        clean_video_path: str,
        intro_path: str,
        bgm_path: str,
        filter_spec: FilterComplexSpec,
        output_video_path: str,
    ) -> str:
        """Render final_video.mp4 using the domain FilterComplexSpec."""


class ThumbnailGeneratorPort(Protocol):
    """Port for enhancing extracted frames and overlaying thumbnail text."""

    def generate_thumbnail(
        self,
        frame_image_path: str,
        text: str,
        font_path: str,
        output_thumbnail_path: str,
    ) -> str:
        """Generate final thumbnail image."""


class StorageRepositoryPort(Protocol):
    """Port for managing workspace directories, stage cache, and JSON artifacts."""

    def ensure_video_directories(self, video_id: str) -> VideoWorkspacePaths:
        """Prepare directory layout and return resolved paths for video_id."""

    def exists(self, path: str) -> bool:
        """Return whether an artifact exists at path."""

    def load_editorial_plan(self, path: str) -> EditorialPlan:
        """Load and validate an EditorialPlan from JSON."""

    def save_editorial_plan(self, plan: EditorialPlan, path: str) -> None:
        """Persist an EditorialPlan to JSON."""

    def save_metadata_and_audit(self, payload: dict[str, Any], path: str) -> None:
        """Persist metadata_and_audit.json."""
