"""CLI adapter for ffprobe and ffmpeg media processing."""

import json
import subprocess
from fractions import Fraction
from typing import Any

from src.domain.constants import PROXY_FPS, PROXY_HEIGHT
from src.domain.ffmpeg_builder import FilterComplexSpec
from src.domain.models import CleanVideo, RawRecording


class FfmpegCliAdapter:
    """MediaProcessorPort implementation using list[str] subprocess calls."""

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
    ) -> None:
        self._ffmpeg_bin = ffmpeg_bin
        self._ffprobe_bin = ffprobe_bin

    def probe_recording(self, input_path: str) -> RawRecording:
        """Inspect raw recording via ffprobe and construct validated RawRecording."""
        meta = self._probe_metadata(input_path)
        return RawRecording(
            file_path=input_path,
            width=meta["width"],
            height=meta["height"],
            fps=meta["fps"],
            duration_sec=meta["duration_sec"],
            audio_streams=meta["audio_streams"],
        )

    def probe_clean_video(self, clean_video_path: str) -> CleanVideo:
        """Inspect clean_base.mp4 via ffprobe and construct validated CleanVideo."""
        meta = self._probe_metadata(clean_video_path)
        return CleanVideo(
            file_path=clean_video_path,
            width=meta["width"],
            height=meta["height"],
            fps=meta["fps"],
            duration_sec=meta["duration_sec"],
            audio_streams=meta["audio_streams"],
        )

    def generate_proxy(self, clean_video_path: str, proxy_output_path: str) -> str:
        """Generate a 360p/15fps proxy video from clean_base.mp4."""
        command: list[str] = [
            self._ffmpeg_bin,
            "-y",
            "-i",
            clean_video_path,
            "-vf",
            f"scale=-2:{PROXY_HEIGHT},fps={PROXY_FPS}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            proxy_output_path,
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        return proxy_output_path

    def extract_frame(
        self,
        video_path: str,
        timestamp_sec: float,
        output_image_path: str,
    ) -> str:
        """Extract a single frame at timestamp_sec."""
        command: list[str] = [
            self._ffmpeg_bin,
            "-y",
            "-ss",
            str(timestamp_sec),
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            output_image_path,
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        return output_image_path

    def render_final_video(
        self,
        clean_video_path: str,
        intro_path: str,
        bgm_path: str,
        filter_spec: FilterComplexSpec,
        output_video_path: str,
    ) -> str:
        """Render final_video.mp4 injecting asset inputs and selecting GPU or CPU codec."""
        command: list[str] = [
            self._ffmpeg_bin,
            "-y",
            "-i",
            clean_video_path,
            "-i",
            intro_path,
            "-i",
            bgm_path,
        ]
        for sfx_path in filter_spec.sfx_input_paths:
            command.extend(["-i", sfx_path])

        if self._has_nvenc():
            video_codec_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
        else:
            video_codec_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20"]

        command.extend(
            [
                "-filter_complex",
                filter_spec.filter_complex,
                "-map",
                filter_spec.video_output_label,
                "-map",
                filter_spec.audio_output_label,
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                output_video_path,
            ]
        )
        subprocess.run(command, check=True, capture_output=True, text=True)
        return output_video_path

    def _has_nvenc(self) -> bool:
        result = subprocess.run(
            [self._ffmpeg_bin, "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and "h264_nvenc" in result.stdout

    def _probe_metadata(self, media_path: str) -> dict[str, Any]:
        command: list[str] = [
            self._ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            media_path,
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams:
            raise RuntimeError(f"No video stream found in '{media_path}'.")

        video = video_streams[0]
        audio_streams = sum(1 for s in streams if s.get("codec_type") == "audio")
        fps = float(Fraction(str(video["r_frame_rate"])))
        duration_sec = float(payload["format"]["duration"])

        return {
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": fps,
            "duration_sec": duration_sec,
            "audio_streams": audio_streams,
        }
