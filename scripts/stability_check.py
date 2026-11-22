"""CLI tool to evaluate Stage 3 stability (COPPA flags and Hook clip) across N runs."""

import argparse
import os
from collections.abc import Sequence

from src.domain.constants import DEFAULT_RECIPE_REL_PATH
from src.domain.coppa_rules import evaluate_transcript_coppa, merge_and_pad_safety_flags
from src.domain.models import EditorialPlan
from src.infrastructure.ffmpeg_adapter import FfmpegCliAdapter
from src.infrastructure.gemini_adapter import GeminiApiAdapter
from src.infrastructure.storage_adapter import LocalOrDriveFsAdapter
from src.infrastructure.transcriber_adapter import FasterWhisperAdapter


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stability_check",
        description="Run Stage 3 N times on the same video and report COPPA/Hook stability.",
    )
    parser.add_argument("--base-dir", required=True, help="Root pipeline directory.")
    parser.add_argument("--video-id", required=True, help="Video ID under 02_staging/{video_id}.")
    parser.add_argument(
        "--recipe",
        default=DEFAULT_RECIPE_REL_PATH,
        help="Path to the editorial recipe YAML.",
    )
    parser.add_argument("--runs", type=int, default=3, help="Number of analysis runs.")
    return parser.parse_args(argv)


def run_stability_check(argv: Sequence[str] | None = None) -> int:
    """Execute N Stage-3 runs and print a comparison report."""
    args = _parse_args(argv)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY is required.")
        return 1

    storage = LocalOrDriveFsAdapter(base_dir=args.base_dir)
    paths = storage.ensure_video_directories(args.video_id)
    recipe = storage.load_recipe(args.recipe)
    ffmpeg = FfmpegCliAdapter()
    transcriber = FasterWhisperAdapter()
    analyzer = GeminiApiAdapter(api_key=api_key, recipe=recipe)

    if not storage.exists(paths.proxy_path):
        ffmpeg.generate_proxy(paths.clean_base_path, paths.proxy_path)
    if not storage.exists(paths.transcript_path):
        ffmpeg.extract_voice_track(paths.clean_base_path, paths.voice_track_path)
        segments = transcriber.transcribe(paths.voice_track_path)
        storage.save_transcript(segments, paths.transcript_path)
    else:
        segments = storage.load_transcript(paths.transcript_path)

    rule_flags = evaluate_transcript_coppa(segments, recipe.coppa)
    plans: list[EditorialPlan] = []
    for run_idx in range(1, args.runs + 1):
        res = analyzer.analyze_video(paths.proxy_path)
        merged = merge_and_pad_safety_flags(
            rule_flags=rule_flags,
            ai_flags=res.plan.safety_flags,
            padding_sec=recipe.coppa.padding_sec,
        )
        plan = res.plan.model_copy(
            update={
                "safety_flags": merged,
                "recipe_version": recipe.version,
                "recipe_hash": recipe.recipe_hash,
                "model_used": res.model_used,
            }
        )
        plans.append(plan)
        print(
            f"[Run {run_idx}/{args.runs}] model={plan.model_used} | "
            f"hook=[{plan.hook_clip.start_sec:.1f}, {plan.hook_clip.end_sec:.1f}] | "
            f"critical_coppa={len(plan.critical_safety_flags)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(run_stability_check())
