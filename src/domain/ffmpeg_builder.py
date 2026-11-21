"""Pure FFmpeg filter_complex expression builders (zero I/O)."""

from pydantic import BaseModel, ConfigDict

from src.domain.constants import (
    ASSET_SFX_DIR_REL_PATH,
    AUDIO_SAMPLE_RATE,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CORNER_ZOOM_15_RATIO,
    FACE_CLOSEUP_50_RATIO,
    GAME_TARGET_LUFS,
    MILLISECONDS_PER_SECOND,
    MUSIC_DROP_DURATION_SEC,
    VOICE_TARGET_LUFS,
)
from src.domain.models import (
    DynamicEvent,
    EditorialPlan,
    EventCategory,
    SafetyFlag,
    SafetySeverity,
    TimeSegment,
    ZoomType,
)

SFX_INPUT_START_INDEX: int = 3
BASE_AUDIO_MIX_INPUTS: int = 3
MUSIC_DROP_CATEGORIES: frozenset[EventCategory] = frozenset(
    {EventCategory.SURPRISE, EventCategory.LAUGH}
)
ZOOM_RATIOS: dict[ZoomType, float] = {
    ZoomType.CORNER_ZOOM_15: CORNER_ZOOM_15_RATIO,
    ZoomType.FACE_CLOSEUP_50: FACE_CLOSEUP_50_RATIO,
}


class FilterComplexSpec(BaseModel):
    """Deterministic FFmpeg filtergraph specification and required SFX asset paths."""

    model_config = ConfigDict(frozen=True)

    filter_complex: str
    video_output_label: str = "[outv]"
    audio_output_label: str = "[outa]"
    sfx_input_paths: tuple[str, ...] = ()


def _escape_drawtext_value(value: str) -> str:
    """Escape backslashes, colons, and single quotes for FFmpeg drawtext."""
    normalized = value.replace("\\", "/")
    if ":" in normalized or "'" in normalized or "\\" in value:
        # Preserve explicit backslashes for Windows/escaped text tests while keeping POSIX slashes
        escaped = value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        return escaped
    return normalized


def _escape_font_path(font_path: str) -> str:
    """Escape font path for FFmpeg drawtext fontfile parameter."""
    posix_path = font_path.replace("\\", "/")
    return posix_path.replace(":", "\\:").replace("'", "\\'")


def build_zoom_filter(
    zoom_type: ZoomType,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> str:
    """Build a top-left corner-anchored crop and scale expression."""
    ratio = ZOOM_RATIOS[zoom_type]
    crop_w = int(round(canvas_width * ratio))
    crop_h = int(round(canvas_height * ratio))
    return f"crop=w={crop_w}:h={crop_h}:x=0:y=0,scale={canvas_width}:{canvas_height}"


def build_drawtext_filter(
    text: str,
    start_sec: float,
    end_sec: float,
    font_path: str,
) -> str:
    """Build an FFmpeg drawtext filter expression bounded to [start_sec, end_sec]."""
    escaped_font = _escape_font_path(font_path)
    escaped_text = _escape_drawtext_value(text)
    return (
        f"drawtext=fontfile='{escaped_font}':text='{escaped_text}':"
        "x=(w-text_w)/2:y=h*0.80:fontsize=72:fontcolor=white:"
        f"borderw=4:bordercolor=black:enable='between(t,{start_sec},{end_sec})'"
    )


def _build_coppa_mute_filters(safety_flags: tuple[SafetyFlag, ...]) -> list[str]:
    return [
        f"volume=enable='between(t,{flag.segment.start_sec},{flag.segment.end_sec})':volume=0"
        for flag in safety_flags
        if flag.severity == SafetySeverity.CRITICAL
    ]


def _build_bgm_drop_filters(events: tuple[DynamicEvent, ...]) -> list[str]:
    return [
        (
            f"volume=enable='between(t,{event.segment.start_sec},"
            f"{event.segment.start_sec + MUSIC_DROP_DURATION_SEC})':volume=0"
        )
        for event in events
        if event.category in MUSIC_DROP_CATEGORIES
    ]


def _build_ad_exclusion_expr(ad_segments: tuple[TimeSegment, ...]) -> str:
    clauses = "+".join(
        f"between(t,{segment.start_sec},{segment.end_sec})" for segment in ad_segments
    )
    return f"not({clauses})"


def _build_gameplay_video_chain(
    events: tuple[DynamicEvent, ...],
    font_path: str,
    output_label: str = "[gameplay_v]",
) -> list[str]:
    chains: list[str] = []
    current_label = "[v_main_src]"

    for idx, event in enumerate(events):
        base_label = f"[gv_base_{idx}]"
        zoom_src_label = f"[gv_zsrc_{idx}]"
        zoomed_label = f"[gv_zoom_{idx}]"
        out_label = f"[gv_{idx}]"
        zoom_expr = build_zoom_filter(event.zoom_type)
        chains.append(f"{current_label}split=2{base_label}{zoom_src_label}")
        chains.append(f"{zoom_src_label}{zoom_expr}{zoomed_label}")
        chains.append(
            f"{base_label}{zoomed_label}overlay=0:0:"
            f"enable='between(t,{event.segment.start_sec},{event.segment.end_sec})'{out_label}"
        )
        current_label = out_label

    drawtext_filters = [
        build_drawtext_filter(
            text=event.impact_text,
            start_sec=event.segment.start_sec,
            end_sec=event.segment.end_sec,
            font_path=font_path,
        )
        for event in events
        if event.impact_text is not None
    ]
    if drawtext_filters:
        chains.append(f"{current_label}{','.join(drawtext_filters)}{output_label}")
    else:
        chains.append(f"{current_label}scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}{output_label}")
    return chains


def build_filter_complex(
    plan: EditorialPlan,
    font_path: str,
    sfx_dir: str = ASSET_SFX_DIR_REL_PATH,
) -> FilterComplexSpec:
    """Build the complete deterministic FFmpeg filter_complex graph for an EditorialPlan."""
    chains: list[str] = []

    # 1. Split raw clean_base video and normalized voice/game audio streams for hook + gameplay
    chains.append("[0:v:0]split=2[v_hook_src][v_main_src]")

    coppa_filters = _build_coppa_mute_filters(plan.safety_flags)
    voice_norm_chain = (
        f"loudnorm=I={VOICE_TARGET_LUFS}:TP=-1.5:LRA=11,"
        f"aresample={AUDIO_SAMPLE_RATE},"
        "asplit=3[voice_hook_src][voice_mix][voice_sc]"
    )
    if coppa_filters:
        chains.append(f"[0:a:0]{','.join(coppa_filters)},{voice_norm_chain}")
    else:
        chains.append(f"[0:a:0]{voice_norm_chain}")

    chains.append(
        f"[0:a:1]loudnorm=I={GAME_TARGET_LUFS}:TP=-1.5:LRA=11,"
        f"aresample={AUDIO_SAMPLE_RATE},"
        "asplit=2[game_hook_src][game_att]"
    )

    # 2. Extract hook_clip at 00:00 (voice + game audio) and normalize intro timestamps
    hook_start = plan.hook_clip.start_sec
    hook_end = plan.hook_clip.end_sec
    chains.append(
        f"[v_hook_src]trim=start={hook_start}:end={hook_end},"
        f"setpts=PTS-STARTPTS,scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}[hook_v]"
    )
    chains.append(
        f"[voice_hook_src]atrim=start={hook_start}:end={hook_end},asetpts=PTS-STARTPTS[hook_voice_a]"
    )
    chains.append(
        f"[game_hook_src]atrim=start={hook_start}:end={hook_end},asetpts=PTS-STARTPTS[hook_game_a]"
    )
    chains.append("[hook_voice_a][hook_game_a]amix=inputs=2:duration=first:normalize=0[hook_a]")
    chains.append(f"[1:v:0]scale={CANVAS_WIDTH}:{CANVAS_HEIGHT},setpts=PTS-STARTPTS[intro_v]")
    chains.append("[1:a:0]asetpts=PTS-STARTPTS[intro_a]")

    has_ads = bool(plan.ad_segments)
    gameplay_v_out = "[gameplay_v_fx]" if has_ads else "[gameplay_v]"
    gameplay_a_out = "[gameplay_a_mix]" if has_ads else "[gameplay_a]"

    # 3. Gameplay video: corner-anchored zoom + conditional drawtext
    chains.extend(
        _build_gameplay_video_chain(
            plan.dynamic_events,
            font_path=font_path,
            output_label=gameplay_v_out,
        )
    )

    # 4. Audio mixing: -14 LUFS voice, -26 LUFS game, sidechaincompress + Music Drops on bgm, SFX
    bgm_drops = _build_bgm_drop_filters(plan.dynamic_events)
    if bgm_drops:
        chains.append(f"[2:a:0]{','.join(bgm_drops)}[bgm_dropped]")
        bgm_source_label = "[bgm_dropped]"
    else:
        bgm_source_label = "[2:a:0]"

    chains.append(
        f"{bgm_source_label}[voice_sc]sidechaincompress="
        "threshold=0.02:ratio=6:attack=50:release=300[bgm_ducked]"
    )

    sfx_paths: list[str] = []
    sfx_labels: list[str] = []
    normalized_sfx_dir = sfx_dir.rstrip("/")
    for idx, event in enumerate(plan.dynamic_events):
        input_idx = SFX_INPUT_START_INDEX + idx
        delay_ms = int(round(event.segment.start_sec * MILLISECONDS_PER_SECOND))
        sfx_label = f"[sfx_{idx}]"
        chains.append(f"[{input_idx}:a:0]adelay={delay_ms}|{delay_ms}{sfx_label}")
        sfx_labels.append(sfx_label)
        sfx_paths.append(f"{normalized_sfx_dir}/{event.category.value.lower()}.wav")

    mix_inputs = ["[voice_mix]", "[game_att]", "[bgm_ducked]", *sfx_labels]
    total_mix_inputs = BASE_AUDIO_MIX_INPUTS + len(sfx_labels)
    chains.append(
        f"{''.join(mix_inputs)}amix=inputs={total_mix_inputs}:"
        f"duration=first:normalize=0{gameplay_a_out}"
    )

    if has_ads:
        ad_expr = _build_ad_exclusion_expr(plan.ad_segments)
        chains.append(f"{gameplay_v_out}select='{ad_expr}',setpts=N/FRAME_RATE/TB[gameplay_v]")
        chains.append(f"{gameplay_a_out}aselect='{ad_expr}',asetpts=N/SR/TB[gameplay_a]")

    # 5. Strict chronological concatenation: hook (00:00) -> intro -> gameplay
    chains.append(
        "[hook_v][hook_a][intro_v][intro_a][gameplay_v][gameplay_a]concat=n=3:v=1:a=1[outv][outa]"
    )

    return FilterComplexSpec(
        filter_complex=";".join(chains),
        video_output_label="[outv]",
        audio_output_label="[outa]",
        sfx_input_paths=tuple(sfx_paths),
    )
