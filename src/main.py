"""CLI entrypoint, structured logging setup, and dependency wiring."""

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from src.application.ports import MultimodalAnalyzerPort
from src.application.use_cases import ProcessVideoUseCase
from src.domain.errors import DomainError
from src.infrastructure.auto_editor_adapter import AutoEditorCliAdapter
from src.infrastructure.ffmpeg_adapter import FfmpegCliAdapter
from src.infrastructure.gemini_adapter import GeminiApiAdapter
from src.infrastructure.storage_adapter import LocalOrDriveFsAdapter
from src.infrastructure.thumbnail_adapter import PillowThumbnailAdapter

LOG_FORMAT: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt_kids_video_pipeline",
        description="Automated post-production pipeline for YouTube Kids gaming videos.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the raw multitrack .mkv video file.",
    )
    parser.add_argument(
        "--base-dir",
        required=True,
        help="Root pipeline directory containing 00_assets, 01_raw, 02_staging, 03_output.",
    )
    parser.add_argument(
        "--force-reanalyze",
        action="store_true",
        help="Force proxy regeneration and Gemini analysis even if editorial_plan.json exists.",
    )
    return parser.parse_args(argv)


def _configure_logger(log_file_path: str) -> tuple[logging.Logger, list[logging.Handler]]:
    logger = logging.getLogger("yt_kids_video_pipeline")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [console_handler, file_handler]
    for handler in handlers:
        logger.addHandler(handler)
    return logger, handlers


def run_pipeline(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, wire hexagonal components, and execute the pipeline."""
    args = _parse_args(argv)
    video_id = Path(args.input).stem
    storage = LocalOrDriveFsAdapter(base_dir=args.base_dir)
    paths = storage.ensure_video_directories(video_id)
    log_path = storage.output_log_path(video_id)

    logger, handlers = _configure_logger(log_path)
    try:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        needs_multimodal = args.force_reanalyze or not storage.exists(paths.editorial_plan_path)
        if needs_multimodal and not api_key:
            logger.error(
                "Configuration error: GEMINI_API_KEY environment variable is required "
                "for multimodal analysis."
            )
            return 1

        analyzer = (
            GeminiApiAdapter(api_key=api_key) if api_key else cast(MultimodalAnalyzerPort, None)
        )
        use_case = ProcessVideoUseCase(
            media_processor=FfmpegCliAdapter(),
            silence_remover=AutoEditorCliAdapter(),
            multimodal_analyzer=analyzer,
            thumbnail_generator=PillowThumbnailAdapter(),
            storage=storage,
            logger=logger,
        )

        logger.info("Starting pipeline execution for video_id=%s", video_id)
        output = use_case.execute(
            input_path=args.input,
            video_id=video_id,
            force_reanalyze=args.force_reanalyze,
        )
        logger.info(
            "Pipeline completed for video_id=%s | final_video=%s",
            video_id,
            output.final_video_path,
        )
        return 0
    except DomainError as exc:
        logger.error("Pipeline aborted due to domain validation error: %s", exc)
        return 1
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
