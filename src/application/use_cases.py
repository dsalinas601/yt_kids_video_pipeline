"""Application use cases orchestrating the 5-stage post-production pipeline."""

import logging
import time
from collections.abc import Sequence
from typing import Any

from src.application.ports import (
    AnalysisResult,
    MediaProcessorPort,
    MultimodalAnalyzerPort,
    SilenceRemoverPort,
    StorageRepositoryPort,
    ThumbnailArtGeneratorPort,
    ThumbnailGeneratorPort,
    TokenUsage,
    TranscriberPort,
    VideoWorkspacePaths,
)
from src.domain.constants import FACECAM_REGION
from src.domain.coppa_rules import (
    TranscriptSegment,
    evaluate_transcript_coppa,
    merge_and_pad_safety_flags,
    verify_coppa_gate,
)
from src.domain.errors import DomainError, ThumbnailArtError
from src.domain.ffmpeg_builder import build_filter_complex
from src.domain.models import CleanVideo, EditorialPlan, PipelineOutput, SafetySeverity
from src.domain.recipe import EditorialRecipe, default_recipe


class ProcessVideoUseCase:
    """Orchestrates the 5 pipeline stages with versioned recipe caching and COPPA auditing."""

    def __init__(
        self,
        media_processor: MediaProcessorPort,
        silence_remover: SilenceRemoverPort,
        multimodal_analyzer: MultimodalAnalyzerPort,
        thumbnail_generator: ThumbnailGeneratorPort,
        storage: StorageRepositoryPort,
        thumbnail_art_generator: ThumbnailArtGeneratorPort | None = None,
        transcriber: TranscriberPort | None = None,
        recipe: EditorialRecipe | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._media_processor = media_processor
        self._silence_remover = silence_remover
        self._multimodal_analyzer = multimodal_analyzer
        self._thumbnail_generator = thumbnail_generator
        self._storage = storage
        self._thumbnail_art_generator = thumbnail_art_generator
        self._transcriber = transcriber
        self._recipe_provided = recipe is not None
        self._recipe = recipe or default_recipe()
        self._logger = logger or logging.getLogger(__name__)

    def execute(
        self,
        input_path: str,
        video_id: str,
        force_reanalyze: bool = False,
        stop_after_plan: bool = False,
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
        clean_video, clean_regenerated = self._resolve_clean_video(raw_recording, paths)
        if clean_regenerated and self._storage.exists(paths.editorial_plan_path):
            self._logger.warning(
                "clean_base.mp4 regenerated; invalidating cached editorial_plan.json: %s",
                paths.editorial_plan_path,
            )
        self._logger.info(
            "Stage 2 (Silence removal) completed in %.3fs",
            time.perf_counter() - t1,
        )

        # Stage 3: Proxy 360p/15fps + COPPA Transcription + Multimodal analysis
        t2 = time.perf_counter()
        self._logger.info("Starting Stage 3 (Multimodal analysis) for video_id=%s", video_id)
        plan, token_usage = self._resolve_editorial_plan(
            clean_video=clean_video,
            paths=paths,
            force_reanalyze=force_reanalyze or clean_regenerated,
        )
        if paths.editorial_review_path:
            review_md = _build_editorial_review_markdown(video_id, plan, self._recipe)
            self._storage.save_editorial_review(review_md, paths.editorial_review_path)
        self._logger.info(
            "Stage 3 (Multimodal analysis) completed in %.3fs | input_tokens=%d output_tokens=%d",
            time.perf_counter() - t2,
            token_usage.input_tokens,
            token_usage.output_tokens,
        )

        if stop_after_plan:
            audit_payload = _build_audit_payload(
                video_id=video_id,
                plan=plan,
                token_usage=token_usage,
                recipe=self._recipe,
            )
            self._storage.save_metadata_and_audit(audit_payload, paths.metadata_audit_path)
            self._logger.info(
                "Stopping after Stage 3 (--stop-after-plan) for human review: %s",
                paths.editorial_plan_path,
            )
            return PipelineOutput(
                final_video_path=paths.editorial_plan_path,
                thumbnail_path=paths.editorial_review_path or paths.editorial_plan_path,
                metadata_audit_path=paths.metadata_audit_path,
                critical_flags_mitigated=len(plan.critical_safety_flags),
            )

        # Stage 4: Final FFmpeg render
        t3 = time.perf_counter()
        self._logger.info("Starting Stage 4 (Render) for video_id=%s", video_id)
        filter_spec = build_filter_complex(
            plan=plan,
            font_path=paths.font_path,
            sfx_dir=paths.sfx_dir,
            recipe=self._recipe if self._recipe_provided else None,
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
        base_image_path, face_source_path, face_region = self._resolve_thumbnail_base(
            frame_path=frame_path,
            plan=plan,
            paths=paths,
        )
        thumbnail_path = self._thumbnail_generator.generate_thumbnail(
            base_image_path,
            thumb_text,
            paths.font_path,
            paths.thumbnail_path,
            face_source_path=face_source_path,
            face_region=face_region,
        )
        audit_payload = _build_audit_payload(
            video_id=video_id,
            plan=plan,
            token_usage=token_usage,
            recipe=self._recipe,
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
    ) -> tuple[CleanVideo, bool]:
        if self._storage.exists(paths.clean_base_path):
            self._logger.info("Cache hit for clean_base.mp4: %s", paths.clean_base_path)
            try:
                return self._media_processor.probe_clean_video(paths.clean_base_path), False
            except Exception as exc:
                self._logger.warning(
                    "Cached clean_base.mp4 is corrupted or unreadable (%s); regenerating: %s",
                    exc,
                    paths.clean_base_path,
                )
        return self._silence_remover.remove_silences(raw_recording, paths.clean_base_path), True

    def _has_recipe_mismatch(self, plan: EditorialPlan) -> bool:
        if self._recipe_provided:
            return plan.recipe_hash != self._recipe.recipe_hash
        return bool(plan.recipe_hash and plan.recipe_hash != self._recipe.recipe_hash)

    def _resolve_editorial_plan(
        self,
        clean_video: CleanVideo,
        paths: VideoWorkspacePaths,
        force_reanalyze: bool,
    ) -> tuple[EditorialPlan, TokenUsage]:
        if self._storage.exists(paths.editorial_plan_path) and not force_reanalyze:
            plan = self._storage.load_editorial_plan(paths.editorial_plan_path)
            if not self._has_recipe_mismatch(plan):
                self._logger.info(
                    "Cache hit for editorial_plan.json: %s",
                    paths.editorial_plan_path,
                )
                return plan, TokenUsage(input_tokens=0, output_tokens=0)
            self._logger.warning(
                "Recipe hash mismatch (cached=%s, active=%s); "
                "invalidating cached editorial_plan.json: %s",
                plan.recipe_hash,
                self._recipe.recipe_hash,
                paths.editorial_plan_path,
            )

        transcript_segments = self._resolve_transcript(
            clean_video=clean_video,
            paths=paths,
            force_reanalyze=force_reanalyze,
        )

        if not force_reanalyze and self._storage.exists(paths.proxy_path):
            self._logger.info("Cache hit for proxy_360p.mp4: %s", paths.proxy_path)
            proxy_path = paths.proxy_path
        else:
            self._logger.info("Generating 360p/15fps proxy video: %s", paths.proxy_path)
            proxy_path = self._media_processor.generate_proxy(
                clean_video.file_path,
                paths.proxy_path,
            )
        analysis = self._multimodal_analyzer.analyze_video(proxy_path)
        enriched_plan = self._enrich_plan_with_coppa_and_recipe(analysis, transcript_segments)
        self._storage.save_editorial_plan(enriched_plan, paths.editorial_plan_path)
        return enriched_plan, analysis.token_usage

    def _resolve_transcript(
        self,
        clean_video: CleanVideo,
        paths: VideoWorkspacePaths,
        force_reanalyze: bool,
    ) -> tuple[TranscriptSegment, ...]:
        if self._transcriber is None:
            return ()
        transcript_path = paths.transcript_path or f"{clean_video.file_path}.transcript.json"
        if not force_reanalyze and self._storage.exists(transcript_path):
            self._logger.info("Cache hit for transcript.json: %s", transcript_path)
            return self._storage.load_transcript(transcript_path)

        voice_wav = paths.voice_track_path or f"{clean_video.file_path}.voice.wav"
        self._logger.info("Extracting voice track for COPPA transcription: %s", voice_wav)
        extracted_wav = self._media_processor.extract_voice_track(clean_video.file_path, voice_wav)
        segments = self._transcriber.transcribe(extracted_wav)
        self._storage.save_transcript(segments, transcript_path)
        return segments

    def _enrich_plan_with_coppa_and_recipe(
        self,
        analysis: AnalysisResult,
        transcript_segments: Sequence[TranscriptSegment],
    ) -> EditorialPlan:
        plan = analysis.plan
        if self._transcriber is None and not self._recipe_provided:
            return plan

        rule_flags = evaluate_transcript_coppa(transcript_segments, self._recipe.coppa)
        merged_flags = merge_and_pad_safety_flags(
            rule_flags=rule_flags,
            ai_flags=plan.safety_flags,
            padding_sec=self._recipe.coppa.padding_sec,
        )
        verify_coppa_gate(rule_flags, merged_flags)

        if self._recipe.hook.forbid_coppa_overlap and any(
            flag.segment.overlaps(plan.hook_clip)
            for flag in merged_flags
            if flag.severity == SafetySeverity.CRITICAL
        ):
            raise DomainError("EditorialPlan hook_clip must not overlap any CRITICAL safety_flag.")

        return plan.model_copy(
            update={
                "safety_flags": merged_flags,
                "recipe_version": self._recipe.version,
                "recipe_hash": self._recipe.recipe_hash,
                "model_used": analysis.model_used or self._recipe.ai.primary_model,
            }
        )

    def _resolve_thumbnail_base(
        self,
        frame_path: str,
        plan: EditorialPlan,
        paths: VideoWorkspacePaths,
    ) -> tuple[str, str | None, tuple[float, float, float, float] | None]:
        if self._thumbnail_art_generator is None:
            return frame_path, None, None

        masked_output = paths.masked_frame_path or f"{frame_path}.masked.png"
        art_output = paths.thumbnail_art_path or f"{frame_path}.art.png"
        try:
            masked_frame = self._thumbnail_generator.mask_region(
                frame_path,
                FACECAM_REGION,
                masked_output,
            )
            art_path = self._thumbnail_art_generator.generate_art(
                masked_frame,
                _build_thumbnail_scene_prompt(plan),
                art_output,
            )
            return art_path, frame_path, FACECAM_REGION
        except ThumbnailArtError as exc:
            self._logger.warning(
                "AI thumbnail generation failed (%s); falling back to classic frame thumbnail.",
                exc,
            )
            return frame_path, None, None


def _build_thumbnail_scene_prompt(plan: EditorialPlan) -> str:
    parts: list[str] = []
    if plan.metadata.game_title.strip():
        parts.append(f"Juego: {plan.metadata.game_title.strip()}")
    if plan.metadata.thumbnail_scene.strip():
        parts.append(f"Escena: {plan.metadata.thumbnail_scene.strip()}")
    if not parts:
        parts.append(f"Tema del video: {plan.metadata.titles[0]}")
    return ". ".join(parts)


def _build_editorial_review_markdown(
    video_id: str,
    plan: EditorialPlan,
    recipe: EditorialRecipe,
) -> str:
    lines = [
        f"# Editorial Review — `{video_id}`",
        "",
        f"- **Receta:** `{recipe.recipe_name}` v`{plan.recipe_version or recipe.version}` "
        f"(hash: `{plan.recipe_hash or recipe.recipe_hash}`)",
        f"- **Modelo utilizado:** `{plan.model_used or recipe.ai.primary_model}`",
        f"- **Hook Clip:** `{plan.hook_clip.start_sec:.2f}s` → `{plan.hook_clip.end_sec:.2f}s` "
        f"(`{plan.hook_clip.duration:.2f}s`)",
        f"- **Título principal:** {plan.metadata.titles[0]}",
        f"- **Anuncios eliminados:** {len(plan.ad_segments)} (`{plan.total_ad_sec_removed:.1f}s`)",
        "",
        "## Auditoría COPPA (`safety_flags`)",
    ]
    if not plan.safety_flags:
        lines.append("- Sin alertas COPPA detectadas.")
    else:
        for flag in plan.safety_flags:
            lines.append(
                f"- **[{flag.severity.value}]** `{flag.segment.start_sec:.2f}s` → "
                f"`{flag.segment.end_sec:.2f}s` | origen: `{flag.source}` | {flag.reason}"
            )
    return "\n".join(lines) + "\n"


def _build_audit_payload(
    video_id: str,
    plan: EditorialPlan,
    token_usage: TokenUsage,
    recipe: EditorialRecipe | None = None,
) -> dict[str, Any]:
    active_recipe = recipe or default_recipe()
    critical_flags = plan.critical_safety_flags
    return {
        "video_id": video_id,
        "recipe": {
            "name": active_recipe.recipe_name,
            "version": plan.recipe_version or active_recipe.version,
            "hash": plan.recipe_hash or active_recipe.recipe_hash,
            "model_used": plan.model_used or active_recipe.ai.primary_model,
        },
        "metadata": plan.metadata.model_dump(mode="json"),
        "safety_flags": [
            {
                "start_sec": flag.segment.start_sec,
                "end_sec": flag.segment.end_sec,
                "severity": flag.severity.value,
                "reason": flag.reason,
                "source": flag.source,
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
                    "source": flag.source,
                }
                for flag in critical_flags
            ],
        },
        "ad_cuts": [
            {
                "start_sec": segment.start_sec,
                "end_sec": segment.end_sec,
            }
            for segment in plan.ad_segments
        ],
        "total_ad_sec_removed": plan.total_ad_sec_removed,
        "token_usage": {
            "input_tokens": token_usage.input_tokens,
            "output_tokens": token_usage.output_tokens,
        },
    }
