"""CLI adapter for auto-editor v29.3.1 silence removal."""

import subprocess

from src.domain.models import CleanVideo, RawRecording


class AutoEditorCliAdapter:
    """SilenceRemoverPort implementation invoking auto-editor v29.3.1 CLI safely."""

    def __init__(self, executable: str = "auto-editor") -> None:
        self._executable = executable

    def remove_silences(self, raw_recording: RawRecording, output_clean_path: str) -> CleanVideo:
        """Analyze voice track 0:a:0 only and export clean_base.mp4 preserving separate tracks."""
        command: list[str] = [
            self._executable,
            raw_recording.file_path,
            "--edit",
            "audio:stream=0",
            "--no-open",
            "--output",
            output_clean_path,
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

        return CleanVideo(
            file_path=output_clean_path,
            width=raw_recording.width,
            height=raw_recording.height,
            fps=raw_recording.fps,
            duration_sec=raw_recording.duration_sec,
            audio_streams=raw_recording.audio_streams,
        )
