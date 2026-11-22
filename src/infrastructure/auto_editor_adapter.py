"""CLI adapter for auto-editor v29.3.1 silence removal."""

import subprocess
import sys
from pathlib import Path

from src.domain.constants import (
    SILENCE_MARGIN_AFTER_SEC,
    SILENCE_MARGIN_BEFORE_SEC,
    SILENCE_MIN_CLIP_SEC,
    SILENCE_MIN_CUT_SEC,
)
from src.domain.models import CleanVideo, RawRecording
from src.domain.recipe import EditorialRecipe

DEFAULT_AUTO_EDITOR_TIMEOUT_SEC: float = 1800.0


class AutoEditorCliAdapter:
    """SilenceRemoverPort implementation invoking auto-editor v29.3.1 CLI safely."""

    def __init__(
        self,
        executable: str = "auto-editor",
        timeout_sec: float = DEFAULT_AUTO_EDITOR_TIMEOUT_SEC,
        recipe: EditorialRecipe | None = None,
    ) -> None:
        self._executable = executable
        self._timeout_sec = timeout_sec
        if recipe is not None:
            self._min_cut_sec = recipe.silence.min_cut_sec
            self._min_clip_sec = recipe.silence.min_clip_sec
            self._margin_before_sec = recipe.silence.margin_before_sec
            self._margin_after_sec = recipe.silence.margin_after_sec
        else:
            self._min_cut_sec = SILENCE_MIN_CUT_SEC
            self._min_clip_sec = SILENCE_MIN_CLIP_SEC
            self._margin_before_sec = SILENCE_MARGIN_BEFORE_SEC
            self._margin_after_sec = SILENCE_MARGIN_AFTER_SEC

    def remove_silences(self, raw_recording: RawRecording, output_clean_path: str) -> CleanVideo:
        """Analyze voice track 0:a:0 only and export clean_base.mp4 preserving separate tracks.

        auto-editor 29.3.1 has no ``--smooth`` option: mincut/minclip are attributes
        of the ``audio`` edit method (``audio:stream=0,mincut=...,minclip=...``).
        """
        command: list[str] = [
            self._executable,
            raw_recording.file_path,
            "--edit",
            f"audio:stream=0,mincut={self._min_cut_sec}s,minclip={self._min_clip_sec}s",
            "--margin",
            f"{self._margin_before_sec}s,{self._margin_after_sec}s",
            "--no-open",
            "--output",
            output_clean_path,
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
            )
        except subprocess.CalledProcessError as exc:
            Path(output_clean_path).unlink(missing_ok=True)
            for stream_output in (exc.stdout, exc.stderr):
                if stream_output:
                    sys.stderr.write(stream_output)
            raise
        except BaseException:
            Path(output_clean_path).unlink(missing_ok=True)
            raise

        return CleanVideo(
            file_path=output_clean_path,
            width=raw_recording.width,
            height=raw_recording.height,
            fps=raw_recording.fps,
            duration_sec=raw_recording.duration_sec,
            audio_streams=raw_recording.audio_streams,
        )
