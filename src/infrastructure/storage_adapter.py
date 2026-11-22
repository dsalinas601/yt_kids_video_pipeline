"""Local or Google Drive filesystem storage adapter."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from src.application.ports import VideoWorkspacePaths
from src.domain.coppa_rules import TranscriptSegment
from src.domain.models import EditorialPlan
from src.domain.recipe import EditorialRecipe


class LocalOrDriveFsAdapter:
    """StorageRepositoryPort implementation over local disk or mounted Google Drive."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def raw_recording_path(self, video_id: str) -> str:
        """Return expected raw recording path under 01_raw/{video_id}.mkv."""
        return str(self._base_dir / "01_raw" / f"{video_id}.mkv")

    def output_log_path(self, video_id: str) -> str:
        """Return log file path under 03_output/{video_id}/pipeline.log."""
        return str(self._base_dir / "03_output" / video_id / "pipeline.log")

    def ensure_video_directories(self, video_id: str) -> VideoWorkspacePaths:
        """Create required pipeline directories and resolve paths for video_id."""
        assets_dir = self._base_dir / "00_assets"
        bgm_dir = assets_dir / "bgm"
        sfx_dir = assets_dir / "sfx"
        fonts_dir = assets_dir / "fonts"
        raw_dir = self._base_dir / "01_raw"
        staging_dir = self._base_dir / "02_staging" / video_id
        output_dir = self._base_dir / "03_output" / video_id

        for directory in (bgm_dir, sfx_dir, fonts_dir, raw_dir, staging_dir, output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        bgm_path = self._first_file(bgm_dir, default_name="default.mp3")
        font_path = self._first_file(fonts_dir, default_name="default.ttf")

        return VideoWorkspacePaths(
            clean_base_path=str(staging_dir / "clean_base.mp4"),
            proxy_path=str(staging_dir / "proxy_360p.mp4"),
            editorial_plan_path=str(staging_dir / "editorial_plan.json"),
            final_video_path=str(output_dir / "final_video.mp4"),
            frame_image_path=str(staging_dir / "thumb_frame.png"),
            thumbnail_path=str(output_dir / "thumbnail.jpg"),
            metadata_audit_path=str(output_dir / "metadata_and_audit.json"),
            intro_path=str(assets_dir / "intro.mp4"),
            bgm_path=bgm_path,
            font_path=font_path,
            sfx_dir=str(sfx_dir),
            masked_frame_path=str(staging_dir / "thumb_masked.png"),
            thumbnail_art_path=str(staging_dir / "thumb_art.png"),
            voice_track_path=str(staging_dir / "voice_track.wav"),
            transcript_path=str(staging_dir / "transcript.json"),
            editorial_review_path=str(staging_dir / "editorial_review.md"),
        )

    def exists(self, path: str) -> bool:
        """Return whether the path exists on disk."""
        return Path(path).exists()

    def load_recipe(self, path: str) -> EditorialRecipe:
        """Load and validate an EditorialRecipe from a YAML file."""
        raw_data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return EditorialRecipe.model_validate(raw_data or {})

    def load_editorial_plan(self, path: str) -> EditorialPlan:
        """Read and validate EditorialPlan from JSON file."""
        content = Path(path).read_text(encoding="utf-8")
        return EditorialPlan.model_validate_json(content)

    def save_editorial_plan(self, plan: EditorialPlan, path: str) -> None:
        """Write EditorialPlan JSON atomically to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    def load_transcript(self, path: str) -> tuple[TranscriptSegment, ...]:
        """Read and validate cached TranscriptSegment list from JSON file."""
        raw_items = json.loads(Path(path).read_text(encoding="utf-8"))
        return tuple(TranscriptSegment.model_validate(item) for item in raw_items)

    def save_transcript(self, segments: Sequence[TranscriptSegment], path: str) -> None:
        """Write TranscriptSegment list to JSON file."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = [seg.model_dump(mode="json") for seg in segments]
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def save_editorial_review(self, markdown_content: str, path: str) -> None:
        """Write human-readable editorial review markdown to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown_content, encoding="utf-8")

    def save_metadata_and_audit(self, payload: dict[str, Any], path: str) -> None:
        """Write metadata_and_audit.json to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _first_file(directory: Path, default_name: str) -> str:
        files = sorted(item for item in directory.iterdir() if item.is_file())
        if files:
            return str(files[0])
        return str(directory / default_name)
