"""Application use cases orchestrating the 5-stage post-production pipeline."""

import logging
import time
from typing import Any

from src.application.ports import (
    MediaProcessorPort,
    MultimodalAnalyzerPort,
    SilenceRemoverPort,
    StorageRepositoryPort,
    ThumbnailGeneratorPort,
    TokenUsage,
    VideoWorkspacePaths,
)
from src.domain.ffmpeg_builder import build_filter_complex
from src.domain.models import CleanVideo, EditorialPlan, PipelineOutput, SafetySeverity


class ProcessVideoUseCase:
    """Orchestrates the 5 pipeline stages with idempotent caching and COPPA auditing."""

    def __init__(
        self,
        media_processor: MediaProcessorPort,
        silence_remover: SilenceRemoverPort,
        multimodal_analyzer: MultimodalAnalyzerPort,
        thumbnail_generator: ThumbnailGeneratorPort,
        storage: StorageRepositoryPort,
        logger: logging.Logger | None = None,
    ) -> None:
        self._media_processor = media_processor
        self._silence_remover = silence_remover
        self._multimodal_analyzer = multimodal_analyzer
        self._thumbnail_generator = thumbnail_generator
        self._storage = storage
        self._logger = logger or logging.getLogger(__name__)

    def execute(
        self,
        input_path: str,
        video_id: str,
        force_reanalyze: bool = False,
    ) -> PipelineOutput:
        """Execute all 5 post-production stages sequentially for a single recording."""
        # Stage 1: Probe + validate RawRecording (aborts with InvalidAudioTracksError if <2 tracks)
        t0 = time.perf_counter()
        self._logger.info("Starting Stage 1 (Probe) for input=%s", input_path)
        raw_recording = self._media_processor.probe_recording(input_path)
        paths = self._storage.ensure_video_directories(video_id)
        self._logger.info("Stage 1 (Probe) completed in %.3fs", time.perf_counter() - t0)

        # Stage 2: Silence removal (idempotent if clean_base.mp4 exists)
        t1 = time.perf_counter()
        self._logger.info("Starting Stage 2 (Silence removal) for video_id=%s", video_id)
        clean_video = self._resolve_clean_video(raw_recording, paths)
        self._logger.info(
            "Stage 2 (Silence removal) completed in %.3fs",
            time.perf_counter() - t1,
        )

        # Stage 3: Proxy 360p/15fps + Multimodal analysis (idempotent unless force_reanalyze)
        t2 = time.perf_counter()
        self._logger.info("Starting Stage 3 (Multimodal analysis) for video_id=%s", video_id)
        plan, token_usage = self._resolve_editorial_plan(
            clean_video=clean_video,
            paths=paths,
            force_reanalyze=force_reanalyze,
        )
        self._logger.info(
            "Stage 3 (Multimodal analysis) completed in %.3fs | input_tokens=%d output_tokens=%d",
            time.perf_counter() - t2,
            token_usage.input_tokens,
            token_usage.output_tokens,
        )

        # Stage 4: Final FFmpeg render
        t3 = time.perf_counter()
        self._logger.info("Starting Stage 4 (Render) for video_id=%s", video_id)
        filter_spec = build_filter_complex(
            plan=plan,
            font_path=paths.font_path,
            sfx_dir=paths.sfx_dir,
        )
        final_video_path = self._media_processor.render_final_video(
            clean_video_path=clean_video.file_path,
            intro_path=paths.intro_path,
            bgm_path=paths.bgm_path,
            filter_spec=filter_spec,
            output_video_path=paths.final_video_path,
        )
        self._logger.info("Stage 4 (Render) completed in %.3fs", time.perf_counter() - t3)

        # Stage 5: Frame extraction, thumbnail generation, and COPPA metadata_and_audit.json
        t4 = time.perf_counter()
        self._logger.info("Starting Stage 5 (Thumbnail & Audit) for video_id=%s", video_id)
        frame_path = self._media_processor.extract_frame(
            clean_video.file_path,
            plan.metadata.thumbnail_timestamp,
            paths.frame_image_path,
        )
        thumb_text = plan.metadata.thumbnail_text or plan.metadata.titles[0]
        thumbnail_path = self._thumbnail_generator.generate_thumbnail(
            frame_path,
            thumb_text,
            paths.font_path,
            paths.thumbnail_path,
        )
        audit_payload = _build_audit_payload(
            video_id=video_id,
            plan=plan,
            token_usage=token_usage,
        )
        self._storage.save_metadata_and_audit(audit_payload, paths.metadata_audit_path)
        self._logger.info(
            "Stage 5 (Thumbnail & Audit) completed in %.3fs",
            time.perf_counter() - t4,
        )

        return PipelineOutput(
            final_video_path=final_video_path,
            thumbnail_path=thumbnail_path,
            metadata_audit_path=paths.metadata_audit_path,
            critical_flags_mitigated=len(plan.critical_safety_flags),
        )

    def _resolve_clean_video(
        self,
        raw_recording: Any,
        paths: VideoWorkspacePaths,
    ) -> CleanVideo:
        if self._storage.exists(paths.clean_base_path):
            self._logger.info("Cache hit for clean_base.mp4: %s", paths.clean_base_path)
            try:
                return self._media_processor.probe_clean_video(paths.clean_base_path)
            except Exception as exc:
                self._logger.warning(
                    "Cached clean_base.mp4 is corrupted or unreadable (%s); regenerating: %s",
                    exc,
                    paths.clean_base_path,
                )
        return self._silence_remover.remove_silences(raw_recording, paths.clean_base_path)

    def _resolve_editorial_plan(
        self,
        clean_video: CleanVideo,
        paths: VideoWorkspacePaths,
        force_reanalyze: bool,
    ) -> tuple[EditorialPlan, TokenUsage]:
        if self._storage.exists(paths.editorial_plan_path) and not force_reanalyze:
            self._logger.info(
                "Cache hit for editorial_plan.json: %s",
                paths.editorial_plan_path,
            )
            plan = self._storage.load_editorial_plan(paths.editorial_plan_path)
            return plan, TokenUsage(input_tokens=0, output_tokens=0)

        proxy_path = self._media_processor.generate_proxy(
            clean_video.file_path,
            paths.proxy_path,
        )
        analysis = self._multimodal_analyzer.analyze_video(proxy_path)
        self._storage.save_editorial_plan(analysis.plan, paths.editorial_plan_path)
        return analysis.plan, analysis.token_usage


def _build_audit_payload(
    video_id: str,
    plan: EditorialPlan,
    token_usage: TokenUsage,
) -> dict[str, Any]:
    critical_flags = plan.critical_safety_flags
    return {
        "video_id": video_id,
        "metadata": plan.metadata.model_dump(mode="json"),
        "safety_flags": [
            {
                "start_sec": flag.segment.start_sec,
                "end_sec": flag.segment.end_sec,
                "severity": flag.severity.value,
                "reason": flag.reason,
                "mitigated": flag.severity == SafetySeverity.CRITICAL,
            }
            for flag in plan.safety_flags
        ],
        "coppa_audit": {
            "total_flags": len(plan.safety_flags),
            "critical_flags_count": len(critical_flags),
            "mitigation_applied": bool(critical_flags),
            "mitigated_segments": [
                {
                    "start_sec": flag.segment.start_sec,
                    "end_sec": flag.segment.end_sec,
                    "reason": flag.reason,
                }
                for flag in critical_flags
            ],
        },
        "token_usage": {
            "input_tokens": token_usage.input_tokens,
            "output_tokens": token_usage.output_tokens,
        },
    }
