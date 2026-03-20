#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SESSION_RUNTIME_ROOT_DIR = ROOT_DIR.parent / "session"
if str(SESSION_RUNTIME_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(SESSION_RUNTIME_ROOT_DIR))

PHASE5_ROOT_DIR = ROOT_DIR.parent / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from input_understanding import (  # noqa: E402
    CompletionPoint,
    MinimalInteractionGenerator,
    TaskContext,
    build_task_followup_question,
)
from input_understanding.interaction_provider import (  # noqa: E402
    DEFAULT_QWEN_BASE_URL,
    DEFAULT_QWEN_MODEL,
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    OpenAICompatibleConfigError,
    OpenAICompatibleRequestError,
    _extract_json_object,
    _optional_string,
)
from phase6_bridge import Phase6BridgeError, Phase6SessionClient  # noqa: E402
from runtime_pipeline import run_phase7_turn_pipeline  # noqa: E402
from session_runtime.phase5_bridge import load_fire_station_task_blueprints  # noqa: E402
from runtime.env_loader import build_runtime_env  # noqa: E402
from voice_input import (  # noqa: E402
    AudioRecordingError,
    NoSpeechDetectedError,
    QwenRealtimeAsrError,
    QwenRealtimeAsrTranscriber,
    OneShotWavRecorder,
    SilenceAwareWavRecorder,
    WhisperCliError,
    WhisperCliTranscriber,
)
from voice_output import SpeechSynthesisError, synthesize_realtime_reply_audio, synthesize_reply_audio  # noqa: E402
from voice_realtime import EchoCancellationError, cancel_playback_echo, play_audio_file  # noqa: E402


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 0")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_completion_points(raw_specs: list[str]) -> tuple[CompletionPoint, ...]:
    return tuple(CompletionPoint.parse(raw_spec) for raw_spec in raw_specs)


def _default_output_path(prefix: str, turn_index: int, suffix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("/tmp") / f"{prefix}-{timestamp}-turn-{turn_index + 1}{suffix}"


def _resolve_turn_output_path(
    base_path: str | None,
    *,
    prefix: str,
    turn_index: int,
    default_suffix: str,
) -> Path:
    if base_path is None:
        return _default_output_path(prefix, turn_index, default_suffix)

    resolved_path = Path(base_path).expanduser()
    suffix = resolved_path.suffix or default_suffix
    stem = resolved_path.stem or prefix
    return resolved_path.with_name(f"{stem}-turn-{turn_index + 1}{suffix}").resolve()


@dataclass(frozen=True)
class PendingPlayback:
    thread: threading.Thread
    error_box: dict[str, str | None]
    audio_path: str


FIRE_STATION_STORY_OPENERS = (
    "消防站的大屏幕亮起来了。",
    "任务墙上跳出了一张新的火情图。",
    "指挥台前的场景板忽然亮了起来。",
)

FIRE_STATION_STORY_INCIDENTS = (
    {
        "location": "卧室床边",
        "detail": "床边冒出了一小团火苗",
        "label": "床边小火",
    },
    {
        "location": "厨房灶台旁",
        "detail": "灶台边烧起了一点火",
        "label": "厨房火情",
    },
    {
        "location": "商店门口",
        "detail": "门口的杂物着火了",
        "label": "门口火情",
    },
    {
        "location": "停车场旁边",
        "detail": "一辆车旁边冒出了火星",
        "label": "停车场火情",
    },
    {
        "location": "客厅沙发旁",
        "detail": "沙发边蹿起了一点火",
        "label": "客厅火情",
    },
    {
        "location": "楼道拐角",
        "detail": "拐角处有烟，火点不大",
        "label": "楼道火情",
    },
)

FIRE_STATION_TASK_COMPLETION_POINT_SPECS: dict[str, tuple[str, ...]] = {
    "fs_001": ("背景可动:背景,可动,能动,会动,墙上,画在墙上,固定,不能动,不动,死的,画上,贴在墙上",),
    "fs_002": ("接警地点:内部,外部,消防站,别的场景,外面,外头,外边,里面,里头,屋里,家里",),
    "fs_003": ("集合出动:消防员,消防车,直升机,飞机,集合,出动,先出发,先走,先去",),
    "fs_004": (
        "火源大小:大火,小火,中火,火很大,火不大,火大,火小,火中等,中等火,大伙,大活,小伙,小活,中伙,中活",
        "火源位置:左边,右边,床,床边,床头,位置,那边",
    ),
    "fs_005": ("救援执行:去救火,灭火,扑火,扑灭,灭掉,救援,出发,处理,到了,赶去,赶过去",),
    "fs_006": ("回站总结:总结,刚才,刚刚,先,然后,最后,回站,归队,我知道了,复盘",),
}


def _load_fire_station_task_blueprints() -> tuple[str, list[dict[str, Any]]]:
    return load_fire_station_task_blueprints()


def _ensure_story_opening_has_first_task_question(opening_text: str) -> str:
    normalized_opening = opening_text.strip()
    first_task_question = "你看看，哪些能动，哪些只是画在墙上的呀？"
    if normalized_opening.endswith(("？", "?")) and any(
        marker in normalized_opening for marker in ("能动", "不能动", "画在墙上", "固定")
    ):
        return normalized_opening
    return f"{normalized_opening.rstrip('。！？!?')}。{first_task_question}"


def _build_fallback_fire_station_story_context() -> tuple[str, str]:
    opener = random.choice(FIRE_STATION_STORY_OPENERS)
    incident = random.choice(FIRE_STATION_STORY_INCIDENTS)
    opening_text = _ensure_story_opening_has_first_task_question(
        f"{opener} 今天火情在{incident['location']}，"
        f"{incident['detail']}。先看看场景里哪些能动，哪些只是画在墙上的。"
    )
    scene_context = (
        f"火情：{incident['label']}。"
        f"地点：{incident['location']}。"
        f"情况：{incident['detail']}。"
        "任务顺序：先区分场景里能动和固定的元素，再判断火情来自哪里，再决定谁出动。"
    )
    return opening_text, scene_context


def _align_story_to_first_task(opening_text: str, scene_context: str) -> tuple[str, str]:
    aligned_opening = opening_text.strip()
    if not any(marker in aligned_opening for marker in ("能动", "不能动", "画在墙上", "固定")):
        aligned_opening = f"{aligned_opening.rstrip('。')}。先看看场景里哪些能动，哪些只是画在墙上的。"
    aligned_opening = _ensure_story_opening_has_first_task_question(aligned_opening)

    aligned_context = scene_context.strip()
    if "任务顺序" not in aligned_context:
        aligned_context = (
            f"{aligned_context.rstrip('。')}。"
            "任务顺序：先区分场景里能动和固定的元素，再判断火情来自哪里，再决定谁出动。"
    )
    return aligned_opening, aligned_context


def _coerce_story_scene_context(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("。")
    if isinstance(value, dict):
        ordered_keys = (
            ("fire_type", "火情"),
            ("fire_location", "地点"),
            ("fire_situation", "情况"),
            ("task_sequence", "任务顺序"),
            ("task_order", "任务顺序"),
        )
        parts: list[str] = []
        consumed_keys: set[str] = set()
        for key, label in ordered_keys:
            raw_item = value.get(key)
            if raw_item in (None, "", [], {}):
                continue
            consumed_keys.add(key)
            if isinstance(raw_item, list):
                rendered_item = " -> ".join(str(item).strip().rstrip("。") for item in raw_item if str(item).strip())
            else:
                rendered_item = str(raw_item).strip().rstrip("。")
            if rendered_item:
                parts.append(f"{label}：{rendered_item}")
        for key, raw_item in value.items():
            if key in consumed_keys or raw_item in (None, "", [], {}):
                continue
            if isinstance(raw_item, list):
                rendered_item = " -> ".join(str(item).strip().rstrip("。") for item in raw_item if str(item).strip())
            else:
                rendered_item = str(raw_item).strip().rstrip("。")
            if rendered_item:
                parts.append(f"{key}：{rendered_item}")
        if parts:
            return "。".join(parts).rstrip("。") + "。"
    if isinstance(value, list):
        rendered = "、".join(str(item).strip().rstrip("。") for item in value if str(item).strip())
        if rendered:
            return rendered
    return None


def _try_build_ai_fire_station_story_context() -> tuple[tuple[str, str] | None, str | None]:
    try:
        env = build_runtime_env(PHASE5_ROOT_DIR / ".env.local")
        client = OpenAICompatibleClient(
            OpenAICompatibleConfig.from_env(
                env,
                provider_label="Qwen story generator",
                api_key_env_keys=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
                model_env_keys=("QWEN_STORY_MODEL", "DASHSCOPE_STORY_MODEL", "QWEN_MODEL", "DASHSCOPE_MODEL"),
                base_url_env_keys=("QWEN_BASE_URL", "DASHSCOPE_BASE_URL"),
                request_url_env_keys=("QWEN_REQUEST_URL", "DASHSCOPE_REQUEST_URL", "DASHSCOPE_CHAT_COMPLETIONS_URL"),
                timeout_env_keys=("QWEN_STORY_TIMEOUT_SECONDS", "DASHSCOPE_STORY_TIMEOUT_SECONDS"),
                max_tokens_env_keys=(),
                temperature_env_keys=(),
                default_base_url=DEFAULT_QWEN_BASE_URL,
                default_model="qwen-turbo",
                default_timeout_seconds=4.0,
                default_max_tokens=None,
                default_temperature=0.9,
            )
        )
        payload = _extract_json_object(
            client.create_chat_completion(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是儿童消防站玩具的故事导演。"
                            "每次生成一个不同的火情开场。"
                            "只输出 JSON，字段必须是 "
                            '{"opening_text":"...","scene_context":"..."}。'
                            "opening_text 只负责铺开火情场景，不要提前让孩子接警、判断、出动。"
                            "开场要和第一步“场景识别”贴上：先让孩子看清哪些能动、哪些只是画在墙上的。"
                            "opening_text 最后必须用一句明确问句收住，直接问孩子哪些能动、哪些只是画在墙上的。"
                            "可以是一小段自然故事，不限制字数，但不要啰嗦。"
                            "scene_context 给后续 AI 用，要包含火情、地点、情况、完整任务顺序。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "scene_id": "classic_world_fire_station",
                                "tasks": ["场景识别", "接警判断", "集合出动", "火源判断", "救援执行", "回站总结"],
                                "style": "playful_companion",
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            ).content_text
        )
    except (OpenAICompatibleConfigError, OpenAICompatibleRequestError, ValueError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    opening_text = (
        _optional_string(payload.get("opening_text"))
        or _optional_string(payload.get("opening"))
        or _optional_string(payload.get("story"))
    )
    scene_context = _coerce_story_scene_context(
        payload.get("scene_context")
        if "scene_context" in payload
        else payload.get("scene") or payload.get("context")
    )
    if opening_text is None or scene_context is None:
        return None, "story payload missing opening_text or scene_context"
    return _align_story_to_first_task(opening_text, scene_context), None


def _build_ai_fire_station_story_context() -> tuple[str, str] | None:
    story, _ = _try_build_ai_fire_station_story_context()
    return story


def _build_fire_station_story_context(provider_mode: str) -> tuple[str, str]:
    if provider_mode in {"qwen", "auto"}:
        ai_story, story_error = _try_build_ai_fire_station_story_context()
        if ai_story is not None:
            return ai_story
        if story_error:
            print(f"[story] qwen fallback: {story_error}", file=sys.stderr)
    return _build_fallback_fire_station_story_context()


def _build_completion_points(task_id: str) -> tuple[CompletionPoint, ...]:
    specs = FIRE_STATION_TASK_COMPLETION_POINT_SPECS.get(task_id, ())
    return parse_completion_points(list(specs))


def _build_task_context_from_payload(
    task_payload: dict[str, Any],
    *,
    scene_context: str | None,
    scene_style: str,
    fallback_task: TaskContext | None = None,
) -> TaskContext:
    task_id = str(task_payload.get("task_id") or (fallback_task.task_id if fallback_task else "task_demo"))
    task_name = str(
        task_payload.get("name")
        or (fallback_task.task_name if fallback_task else "当前任务")
    )
    task_goal = str(
        task_payload.get("goal")
        or (fallback_task.task_goal if fallback_task else "继续完成这一轮任务。")
    )
    expected_child_action = str(
        task_payload.get("expected_child_action")
        or (fallback_task.expected_child_action if fallback_task else "继续回应当前任务。")
    )
    completion_points = _build_completion_points(task_id)
    if not completion_points and fallback_task is not None:
        completion_points = fallback_task.completion_points

    return TaskContext(
        task_id=task_id,
        task_name=task_name,
        task_goal=task_goal,
        expected_child_action=expected_child_action,
        completion_points=completion_points,
        completion_match_mode=(
            fallback_task.completion_match_mode if fallback_task is not None else "any"
        ),
        scene_context=scene_context if scene_context is not None else (
            fallback_task.scene_context if fallback_task is not None else None
        ),
        scene_style=scene_style if scene_style else (
            fallback_task.scene_style if fallback_task is not None else "playful_companion"
        ),
    )


def _build_task_context(args: argparse.Namespace) -> TaskContext:
    return TaskContext(
        task_id=args.task_id,
        task_name=args.task_name,
        task_goal=args.task_goal,
        expected_child_action=args.expected_child_action,
        completion_points=parse_completion_points(args.completion_point),
        completion_match_mode=args.completion_match_mode,
        scene_context=args.scene_context,
        scene_style=args.scene_style,
    )


def _build_ordered_task_contexts(
    task_blueprints: list[dict[str, Any]],
    *,
    task_ids: tuple[str, ...],
    scene_context: str | None,
    scene_style: str,
) -> tuple[TaskContext, ...]:
    allowed_task_ids = {task_id for task_id in task_ids if task_id}
    contexts: list[TaskContext] = []
    for task_payload in task_blueprints:
        task_id = str(task_payload.get("task_id") or "").strip()
        if allowed_task_ids and task_id not in allowed_task_ids:
            continue
        contexts.append(
            _build_task_context_from_payload(
                task_payload,
                scene_context=scene_context,
                scene_style=scene_style,
            )
        )
    return tuple(contexts)


def _find_next_task_hint(
    current_task: TaskContext,
    ordered_task_contexts: tuple[TaskContext, ...],
) -> TaskContext | None:
    for index, task_context in enumerate(ordered_task_contexts):
        if task_context.task_id != current_task.task_id:
            continue
        if index + 1 >= len(ordered_task_contexts):
            return None
        return ordered_task_contexts[index + 1]
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a looped Phase 7 voice session with overlapped mic capture and playback, "
            "then optionally sync each turn to Phase 6."
        ),
    )
    parser.add_argument(
        "--record-seconds",
        type=positive_float,
        default=20.0,
        help="Per-turn max mic listen time in seconds. Realtime mode may stop earlier on silence.",
    )
    parser.add_argument(
        "--max-turns",
        type=positive_int,
        default=8,
        help="Safety cap for the number of voice turns to process. Defaults to 8.",
    )
    parser.add_argument("--task-id", default="task_demo", help="Current task id.")
    parser.add_argument("--task-name", default="当前任务", help="Current task name.")
    parser.add_argument("--task-goal", required=True, help="Current task goal.")
    parser.add_argument(
        "--expected-child-action",
        required=True,
        help="What the child is expected to say or do for this task.",
    )
    parser.add_argument(
        "--completion-point",
        action="append",
        default=[],
        help="Completion point spec. Format: 'label:kw1,kw2' or plain 'keyword'. Repeatable.",
    )
    parser.add_argument(
        "--completion-match-mode",
        choices=("any", "all"),
        default="any",
        help="Whether any completion point is enough, or all points are required.",
    )
    parser.add_argument("--scene-context", default=None, help="Optional short scene context.")
    parser.add_argument(
        "--scene-style",
        default="playful_companion",
        help="Optional scene style label used by the interaction generator.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("realtime", "legacy"),
        default="realtime",
        help="Voice runtime mode. realtime uses Qwen realtime WebSocket ASR/TTS; legacy keeps whisper + HTTP TTS.",
    )
    parser.add_argument(
        "--interaction-provider",
        choices=("qwen", "minimax", "ark_doubao", "template", "auto"),
        default="qwen",
        help="Natural reply provider. Default is qwen; you can also switch to minimax, ark_doubao, template, or auto.",
    )
    parser.add_argument(
        "--echo-cancel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Try to subtract the previous assistant reply from the next mic recording. "
            "Keep this on if you are using speakers without headphones."
        ),
    )
    parser.add_argument(
        "--provider-fast-timeout-seconds",
        type=non_negative_float,
        default=1.8,
        help="Fast-path timeout for task_completed / end_session provider attempts. Set to 0 to disable the timeout.",
    )
    parser.add_argument(
        "--provider-keep-trying-timeout-seconds",
        type=non_negative_float,
        default=2.0,
        help="First provider timeout for keep_trying. Set to 0 to disable the timeout.",
    )
    parser.add_argument(
        "--provider-keep-trying-retry-timeout-seconds",
        type=non_negative_float,
        default=MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS,
        help="Optional second provider timeout for keep_trying retry. Set to 0 to disable the retry.",
    )
    parser.add_argument(
        "--whisper-command",
        default="whisper",
        help="Whisper CLI command or absolute path.",
    )
    parser.add_argument(
        "--record-output-file",
        default=None,
        help="Optional base wav path for the per-turn recordings.",
    )
    parser.add_argument(
        "--record-sample-rate",
        type=positive_int,
        default=OneShotWavRecorder.DEFAULT_SAMPLE_RATE,
        help="Sample rate for one-shot mic recording.",
    )
    parser.add_argument(
        "--record-channels",
        type=positive_int,
        default=OneShotWavRecorder.DEFAULT_CHANNELS,
        help="Channel count for one-shot mic recording.",
    )
    parser.add_argument(
        "--record-device",
        default=None,
        help="Optional sounddevice input device name or index.",
    )
    parser.add_argument(
        "--whisper-model",
        default=WhisperCliTranscriber.DEFAULT_MODEL,
        help="Whisper model name. Default uses the locally cached large-v3-turbo path on this machine.",
    )
    parser.add_argument(
        "--whisper-language",
        default="zh",
        help="Language hint for legacy whisper, and also the fallback ASR language when --realtime-asr-language is not set.",
    )
    parser.add_argument(
        "--realtime-asr-model",
        default=None,
        help="Optional Qwen realtime ASR model override when --runtime-mode realtime.",
    )
    parser.add_argument(
        "--realtime-asr-language",
        default=None,
        help="Optional Qwen realtime ASR language override when --runtime-mode realtime.",
    )
    parser.add_argument(
        "--realtime-asr-timeout-seconds",
        type=positive_float,
        default=None,
        help="Optional timeout for Qwen realtime ASR websocket calls.",
    )
    parser.add_argument(
        "--whisper-device",
        default="cpu",
        help="Inference device passed to whisper CLI.",
    )
    parser.add_argument(
        "--whisper-task",
        choices=("transcribe", "translate"),
        default="transcribe",
        help="Whisper task mode.",
    )
    parser.add_argument(
        "--whisper-threads",
        type=int,
        default=None,
        help="Optional CPU thread count for whisper CLI.",
    )
    parser.add_argument("--session-id", default=None, help="Optional Phase 6 session id.")
    parser.add_argument(
        "--phase6-api-base",
        default=None,
        help="Optional Phase 6 API base, for example http://127.0.0.1:4183/api/session-runtime",
    )
    parser.add_argument(
        "--submit-phase6",
        action="store_true",
        help="If set, create or reuse a Phase 6 session and submit each turn to it.",
    )
    parser.add_argument(
        "--phase6-task-id",
        action="append",
        dest="phase6_task_ids",
        default=[],
        help=(
            "Optional task ids used when auto-creating a Phase 6 session. "
            "Defaults to the full Fire Station scene task list when omitted."
        ),
    )
    parser.add_argument(
        "--tts-provider",
        choices=("auto", "qwen", "say", "none"),
        default="auto",
        help="Reply audio provider. Default auto tries Qwen TTS first, then macOS say fallback.",
    )
    parser.add_argument(
        "--tts-output-file",
        default=None,
        help="Optional base output audio path for the per-turn reply TTS files.",
    )
    parser.add_argument(
        "--no-playback",
        action="store_true",
        help="Do not play synthesized reply audio out loud.",
    )
    parser.add_argument(
        "--playback-device",
        default=None,
        help="Optional sounddevice playback device for synthesized reply audio.",
    )
    parser.add_argument(
        "--playback-gain",
        type=positive_float,
        default=0.4,
        help="Optional playback gain for synthesized reply audio. Lower values reduce speaker leakage.",
    )
    parser.add_argument(
        "--tts-timeout-seconds",
        type=positive_float,
        default=None,
        help="Optional timeout for Qwen TTS requests.",
    )
    parser.add_argument(
        "--qwen-tts-model",
        default=None,
        help="Optional Qwen TTS model override. Otherwise read env or use the provider default.",
    )
    parser.add_argument(
        "--qwen-tts-voice",
        default=None,
        help="Optional Qwen TTS voice override. Otherwise read env or use the provider default.",
    )
    parser.add_argument(
        "--qwen-tts-format",
        default=None,
        help="Optional Qwen TTS audio format override, for example wav or mp3.",
    )
    parser.add_argument(
        "--say-voice",
        default=None,
        help="Optional macOS say voice name used by --tts-provider say or auto fallback.",
    )
    parser.add_argument(
        "--qwen-max-tokens",
        type=positive_int,
        default=None,
        help="Optional Qwen max_tokens override. Omit it to let the provider decide and send no explicit cap.",
    )
    return parser


def _build_transcriber(args: argparse.Namespace) -> Any:
    if args.runtime_mode == "realtime":
        return QwenRealtimeAsrTranscriber(
            model=args.realtime_asr_model,
            language=(
                args.realtime_asr_language
                if args.realtime_asr_language is not None
                else (args.whisper_language or None)
            ),
            request_timeout_seconds=args.realtime_asr_timeout_seconds,
        )

    return WhisperCliTranscriber(
        command=args.whisper_command,
        model=args.whisper_model,
        language=args.whisper_language or None,
        device=args.whisper_device,
        task=args.whisper_task,
        threads=args.whisper_threads,
    )


def _start_playback_thread(
    audio_path: str | Path,
    *,
    device: str | int | None,
    gain: float,
) -> PendingPlayback:
    error_box = {"error": None}
    resolved_audio_path = Path(audio_path).expanduser().resolve()

    def _worker() -> None:
        try:
            play_audio_file(resolved_audio_path, device=device, gain=gain)
        except Exception as exc:  # pragma: no cover - playback errors depend on local host audio devices.
            error_box["error"] = str(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return PendingPlayback(thread=thread, error_box=error_box, audio_path=str(resolved_audio_path))


def _wait_for_playback(pending_playback: PendingPlayback | None) -> str | None:
    if pending_playback is None:
        return None
    pending_playback.thread.join()
    return pending_playback.error_box["error"]


def _build_tts_output_path(args: argparse.Namespace, turn_index: int) -> str | None:
    if args.tts_output_file is None:
        return None
    return str(
        _resolve_turn_output_path(
            args.tts_output_file,
            prefix="ai-block-toy-phase7-tts",
            turn_index=turn_index,
            default_suffix=".wav",
        )
    )


def _build_record_output_path(args: argparse.Namespace, turn_index: int) -> Path:
    return _resolve_turn_output_path(
        args.record_output_file,
        prefix="ai-block-toy-phase7-session-recording",
        turn_index=turn_index,
        default_suffix=".wav",
    )


def _build_opening_output_path(args: argparse.Namespace) -> str | None:
    if args.tts_output_file is None:
        return None
    resolved_path = Path(args.tts_output_file).expanduser()
    suffix = resolved_path.suffix or ".wav"
    stem = resolved_path.stem or "ai-block-toy-phase7-opening"
    return str(resolved_path.with_name(f"{stem}-opening{suffix}").resolve())


def _build_session_memory_summary(turn_payload: dict[str, Any]) -> str:
    child_input_text = str(turn_payload.get("child_input_text") or "").strip()
    signal_resolution = turn_payload.get("signal_resolution") or {}
    interaction_generation = turn_payload.get("interaction_generation") or {}
    current_task = turn_payload.get("current_task") or {}
    next_current_task = turn_payload.get("next_current_task") or {}

    task_name = str(current_task.get("task_name") or current_task.get("name") or "").strip()
    next_task_name = str(
        next_current_task.get("task_name")
        or next_current_task.get("name")
        or ""
    ).strip()
    task_signal = str(signal_resolution.get("task_signal") or "").strip()
    reply_text = str(interaction_generation.get("reply_text") or "").strip()
    recent_turn_summary = str(
        (turn_payload.get("interaction_context") or {}).get("recent_turn_summary") or ""
    ).strip()

    parts: list[str] = []
    if task_name:
        parts.append(f"任务：{task_name}")
    if child_input_text:
        parts.append(f"孩子说「{child_input_text}」")
    if reply_text:
        parts.append(f"我们回「{reply_text}」")
    if task_signal:
        parts.append(f"信号：{task_signal}")
    if recent_turn_summary:
        parts.append(f"摘要：{recent_turn_summary}")
    if next_task_name and next_task_name != task_name:
        parts.append(f"下一步：{next_task_name}")
    return "；".join(parts) if parts else "上一轮没有可记录的对话。"


def _normalize_reply_lead(reply_text: str) -> str:
    normalized = " ".join(reply_text.split()).strip()
    if not normalized:
        return ""
    normalized = normalized.replace("？", "。").replace("?", "。")
    parts = [part.strip("。 ") for part in normalized.split("。") if part.strip("。 ")]
    if not parts:
        return normalized.strip("。 ")
    return parts[0][:32].rstrip("，,：: ")


def _build_phase6_terminal_reply(*, current_task: TaskContext, base_reply: str) -> str:
    if current_task.task_id == "fs_006":
        return "说得真清楚，这次我们先找到线索、再让角色动起来、最后把任务处理好了，今天顺利完成啦。"

    lead = _normalize_reply_lead(base_reply)
    if not lead:
        lead = "这轮任务完成啦"
    return f"{lead.rstrip('。！？!?')}。今天这轮顺利完成啦。"


def _has_completed_assistant_led_summary(phase6_response: dict[str, Any] | None) -> bool:
    if not isinstance(phase6_response, dict):
        return False
    session_payload = phase6_response.get("session")
    if not isinstance(session_payload, dict):
        return False
    if str(session_payload.get("status") or "").strip() not in {"ended", "aborted"}:
        return False
    tasks_payload = phase6_response.get("tasks")
    if not isinstance(tasks_payload, list):
        return False
    return any(
        isinstance(task_payload, dict)
        and bool(task_payload.get("assistant_led_summary"))
        and str(task_payload.get("status") or "").strip() == "completed"
        for task_payload in tasks_payload
    )


def _build_assistant_led_terminal_reply(
    *,
    phase6_response: dict[str, Any] | None,
    base_reply: str,
) -> str:
    if not isinstance(phase6_response, dict):
        return ""
    session_payload = phase6_response.get("session")
    story_title = str(session_payload.get("story_title") or "").strip() if isinstance(session_payload, dict) else ""
    tasks_payload = phase6_response.get("tasks")
    if not isinstance(tasks_payload, list):
        return ""
    completed_tasks = [
        task_payload
        for task_payload in tasks_payload
        if isinstance(task_payload, dict)
        and str(task_payload.get("status") or "").strip() == "completed"
        and not bool(task_payload.get("assistant_led_summary"))
    ]
    task_by_id = {
        str(task_payload.get("task_id") or "").strip(): task_payload
        for task_payload in completed_tasks
        if str(task_payload.get("task_id") or "").strip()
    }

    def render_entities(task_id: str, fallback: str, *, limit: int = 2) -> str:
        task_payload = task_by_id.get(task_id)
        if not isinstance(task_payload, dict):
            return fallback
        entities = [
            str(item).strip()
            for item in (task_payload.get("selected_entities") or ())
            if str(item).strip()
        ][:limit]
        return "和".join(entities) if entities else fallback

    lead = _normalize_reply_lead(base_reply)
    lead_text = f"{lead.rstrip('。！？!?')}。" if lead else ""
    story_prefix = f"今天这轮《{story_title}》里，" if story_title else "今天这轮里，"
    return (
        f"{lead_text}{story_prefix}"
        f"先顺着{render_entities('fs_001', '警报和火情线索')}找到这次警情，"
        f"再让{render_entities('fs_003', '消防小队')}动起来准备出动，"
        f"最后把{render_entities('fs_005', '这场火情')}摆到位处理好。"
        "消防站这轮顺利收尾啦。"
    )


def _compose_phase6_guided_reply(
    *,
    base_reply: str,
    current_task: TaskContext,
    next_current_task: TaskContext,
    phase6_response: dict[str, Any] | None,
) -> str:
    session_payload = phase6_response.get("session") if isinstance(phase6_response, dict) else None
    if not isinstance(session_payload, dict):
        return base_reply
    session_status = str(session_payload.get("status") or "").strip()
    if session_status in {"ended", "aborted"}:
        if _has_completed_assistant_led_summary(phase6_response):
            summary_reply = _build_assistant_led_terminal_reply(
                phase6_response=phase6_response,
                base_reply=base_reply,
            )
            if summary_reply:
                return summary_reply
        return _build_phase6_terminal_reply(current_task=current_task, base_reply=base_reply)
    if next_current_task.task_id == current_task.task_id:
        return base_reply

    followup_question = build_task_followup_question(
        next_current_task,
        seed=f"{current_task.task_id}:{next_current_task.task_id}:phase6-advance",
    )
    lead = _normalize_reply_lead(base_reply) or "这一步完成啦"
    if followup_question in lead:
        return lead
    return f"{lead}。{followup_question}"


def _synthesize_reply_audio(
    *,
    args: argparse.Namespace,
    reply_text: str,
    output_path: str | None,
) -> Any | None:
    if args.tts_provider == "none":
        return None

    if args.runtime_mode == "realtime":
        return synthesize_realtime_reply_audio(
            text=reply_text,
            provider_mode=args.tts_provider,
            output_path=output_path,
            qwen_model=args.qwen_tts_model,
            qwen_voice=args.qwen_tts_voice,
            qwen_audio_format=args.qwen_tts_format,
            tts_timeout_seconds=args.tts_timeout_seconds,
            say_voice=args.say_voice,
        )

    return synthesize_reply_audio(
        text=reply_text,
        provider_mode=args.tts_provider,
        output_path=output_path,
        qwen_model=args.qwen_tts_model,
        qwen_voice=args.qwen_tts_voice,
        qwen_audio_format=args.qwen_tts_format,
        tts_timeout_seconds=args.tts_timeout_seconds,
        say_voice=args.say_voice,
    )


def _record_turn(
    recorder: OneShotWavRecorder,
    *,
    args: argparse.Namespace,
    turn_index: int,
) -> Any:
    record_output_path = _build_record_output_path(args, turn_index)
    print(
        f"[voice-session] turn {turn_index + 1}: recording {args.record_seconds:.2f}s -> {record_output_path}",
        file=sys.stderr,
    )
    return recorder.record(
        seconds=args.record_seconds,
        output_path=record_output_path,
    )


def _run_turn(
    *,
    args: argparse.Namespace,
    current_task: TaskContext,
    ordered_task_contexts: tuple[TaskContext, ...],
    recorded_clip: Any | None,
    audio_path: str | Path,
    playback_reference_audio_path: str | Path | None,
    session_memory_summary: str | None,
    transcriber: Any,
    session_id: str | None,
    turn_index: int,
    phase6_client: Phase6SessionClient | None,
) -> tuple[dict[str, Any], PendingPlayback | None, TaskContext]:
    prepared_audio_path = Path(audio_path).expanduser().resolve()
    audio_preparation: dict[str, Any] | None = None
    # P5: only attempt echo cancel when there actually was a previous TTS playback to cancel
    if args.echo_cancel and playback_reference_audio_path is not None:
        try:
            echo_cancel_result = cancel_playback_echo(
                prepared_audio_path,
                playback_reference_audio_path,
            )
            prepared_audio_path = Path(echo_cancel_result.processed_audio_path).expanduser().resolve()
            audio_preparation = {
                "ok": True,
                **echo_cancel_result.to_dict(),
            }
        except EchoCancellationError as exc:
            audio_preparation = {
                "ok": False,
                "raw_audio_path": str(prepared_audio_path),
                "reference_audio_path": str(Path(playback_reference_audio_path).expanduser().resolve()),
                "error": str(exc),
            }

    try:
        audio_transcription = transcriber.transcribe(prepared_audio_path)
    except NoSpeechDetectedError as exc:
        # B2: bubble up a distinct exception so the caller can retry recording cleanly
        raise exc
    except (WhisperCliError, QwenRealtimeAsrError) as exc:
        raise RuntimeError(f"audio_transcription failed: {exc}") from exc

    next_task_hint = _find_next_task_hint(current_task, ordered_task_contexts)
    bridge_package = run_phase7_turn_pipeline(
        child_input_text=audio_transcription.transcript,
        current_task=current_task,
        interaction_provider=args.interaction_provider,
        provider_fast_timeout_seconds=args.provider_fast_timeout_seconds,
        provider_keep_trying_timeout_seconds=args.provider_keep_trying_timeout_seconds,
        provider_keep_trying_retry_timeout_seconds=args.provider_keep_trying_retry_timeout_seconds,
        session_memory_summary=session_memory_summary,
        session_id=session_id,
        next_task_hint=next_task_hint,
    )
    reply_text = bridge_package.interaction_generation.reply_text
    generation_provider_name = getattr(bridge_package.interaction_generation, "provider_name", None)

    output_payload: dict[str, Any] = {
        "runtime_mode": args.runtime_mode,
        "voice_session_turn_index": turn_index,
        "audio_transcription": audio_transcription.to_dict(),
        **bridge_package.to_dict(),
    }
    output_payload["session_memory_summary"] = session_memory_summary
    if recorded_clip is not None:
        output_payload["audio_recording"] = recorded_clip.to_dict()
    if audio_preparation is not None:
        output_payload["audio_preparation"] = audio_preparation

    if phase6_client is not None and session_id is not None:
        try:
            phase6_response = phase6_client.submit_turn(
                session_id=session_id,
                payload=bridge_package.phase6_turn_payload,
            )
            output_payload["phase6_submit"] = {
                "ok": True,
                "api_base": args.phase6_api_base,
                "response": phase6_response,
            }
        except Phase6BridgeError as exc:
            output_payload["phase6_submit"] = {
                "ok": False,
                "api_base": args.phase6_api_base,
                "error": str(exc),
            }
            raise RuntimeError(f"phase6_submit failed: {exc}") from exc

    next_current_task = current_task
    if isinstance(output_payload.get("phase6_submit"), dict):
        phase6_response = output_payload["phase6_submit"].get("response")
        if isinstance(phase6_response, dict):
            next_current_task = _build_task_context_from_payload(
                phase6_response.get("current_task") or {},
                scene_context=current_task.scene_context,
                scene_style=current_task.scene_style,
                fallback_task=current_task,
            )
            if generation_provider_name != "qwen_unified":
                reply_text = _compose_phase6_guided_reply(
                    base_reply=reply_text,
                    current_task=current_task,
                    next_current_task=next_current_task,
                    phase6_response=phase6_response,
                )

    output_payload["next_current_task"] = next_current_task.to_dict()
    output_payload["interaction_generation"]["reply_text"] = reply_text

    playback = None
    if args.tts_provider != "none":
        try:
            reply_ready_at = time.monotonic()
            tts_output_path = _build_tts_output_path(args, turn_index)
            synthesized_speech = _synthesize_reply_audio(
                args=args,
                reply_text=reply_text,
                output_path=tts_output_path,
            )
            if synthesized_speech is not None:
                output_payload["tts_output"] = {
                    "ok": True,
                    **synthesized_speech.to_dict(),
                }
                if not args.no_playback:
                    print(
                        f"  [播报间隔] {(time.monotonic() - reply_ready_at) * 1000:.0f}ms -> 开始播放",
                        file=sys.stderr,
                    )
                    playback = _start_playback_thread(
                        synthesized_speech.audio_path,
                        device=args.playback_device,
                        gain=args.playback_gain,
                    )
        except SpeechSynthesisError as exc:
            output_payload["tts_output"] = {
                "ok": False,
                "requested_provider": args.tts_provider,
                "text": reply_text,
                "error": str(exc),
            }
            raise RuntimeError(f"tts failed: {exc}") from exc

    return output_payload, playback, next_current_task


def _should_stop_turn_loop(
    *,
    turn_payload: dict[str, Any],
    phase6_submit_enabled: bool,
) -> str | None:
    signal_resolution = turn_payload.get("signal_resolution") or {}
    task_signal = str(signal_resolution.get("task_signal") or "")
    if task_signal == "end_session":
        return "end_session"

    if phase6_submit_enabled:
        phase6_submit = turn_payload.get("phase6_submit") or {}
        phase6_response = phase6_submit.get("response") if isinstance(phase6_submit, dict) else None
        if isinstance(phase6_response, dict):
            session = phase6_response.get("session") or {}
            session_status = str(session.get("status") or "")
            if session_status in {"ended", "aborted"}:
                return session_status
        return None

    if task_signal == "task_completed":
        return "task_completed"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.submit_phase6 and not args.phase6_api_base:
        parser.error("--submit-phase6 requires --phase6-api-base")

    if args.qwen_max_tokens is not None:
        os.environ["QWEN_MAX_TOKENS"] = str(args.qwen_max_tokens)
        os.environ["DASHSCOPE_MAX_TOKENS"] = str(args.qwen_max_tokens)
    else:
        os.environ.pop("QWEN_MAX_TOKENS", None)
        os.environ.pop("DASHSCOPE_MAX_TOKENS", None)

    scene_id, session_task_blueprints = _load_fire_station_task_blueprints()
    session_task_ids = tuple(
        str(task.get("task_id") or "")
        for task in session_task_blueprints
        if str(task.get("task_id") or "")
    )
    opening_text, session_scene_context = _build_fire_station_story_context(args.interaction_provider)
    fallback_task = _build_task_context(args)
    recorder_cls = SilenceAwareWavRecorder if args.runtime_mode == "realtime" else OneShotWavRecorder
    recorder = recorder_cls(
        sample_rate=args.record_sample_rate,
        channels=args.record_channels,
        device=args.record_device,
    )
    transcriber = _build_transcriber(args)

    phase6_client = Phase6SessionClient(args.phase6_api_base) if args.submit_phase6 else None
    session_id = args.session_id
    phase6_bootstrap: dict[str, Any] | None = None
    if phase6_client is not None:
        try:
            if session_id:
                phase6_bootstrap = phase6_client.get_session_snapshot(session_id)
            else:
                created_snapshot = phase6_client.create_session(
                    task_ids=args.phase6_task_ids or list(session_task_ids),
                )
                phase6_bootstrap = created_snapshot
                session_id = str(created_snapshot.get("session", {}).get("session_id") or "")
                if not session_id:
                    raise RuntimeError("Phase 6 session bootstrap did not return a session_id")
                print(
                    f"[phase6] created session {session_id}",
                    file=sys.stderr,
                )
        except Phase6BridgeError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "phase6_bootstrap",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    if phase6_bootstrap is not None and isinstance(phase6_bootstrap.get("current_task"), dict):
        current_task = _build_task_context_from_payload(
            phase6_bootstrap.get("current_task") or {},
            scene_context=session_scene_context,
            scene_style=args.scene_style,
            fallback_task=fallback_task,
        )
    else:
        current_task = _build_task_context_from_payload(
            {
                "task_id": fallback_task.task_id,
                "name": fallback_task.task_name,
                "goal": fallback_task.task_goal,
                "expected_child_action": fallback_task.expected_child_action,
            },
            scene_context=session_scene_context,
            scene_style=args.scene_style,
            fallback_task=fallback_task,
        )

    ordered_task_contexts = _build_ordered_task_contexts(
        session_task_blueprints,
        task_ids=tuple(args.phase6_task_ids or list(session_task_ids)),
        scene_context=session_scene_context,
        scene_style=args.scene_style,
    )

    opening_payload: dict[str, Any] = {
        "text": opening_text,
        "scene_id": scene_id,
        "scene_context": session_scene_context,
    }
    print(
        f"[voice-session] opening story for {scene_id}: {opening_text}",
        file=sys.stderr,
    )
    try:
        opening_output_path = _build_opening_output_path(args)
        synthesized_opening = _synthesize_reply_audio(
            args=args,
            reply_text=opening_text,
            output_path=opening_output_path,
        )
        if synthesized_opening is not None:
            opening_payload["tts_output"] = {
                "ok": True,
                **synthesized_opening.to_dict(),
            }
            if not args.no_playback:
                opening_ready_at = time.monotonic()
                opening_playback = _start_playback_thread(
                    synthesized_opening.audio_path,
                    device=args.playback_device,
                    gain=args.playback_gain,
                )
                print(
                    f"  [播报间隔] {(time.monotonic() - opening_ready_at) * 1000:.0f}ms -> 开始播放",
                    file=sys.stderr,
                )
                playback_error = _wait_for_playback(opening_playback)
                opening_payload["tts_output"]["playback_ok"] = playback_error is None
                if playback_error is not None:
                    opening_payload["tts_output"]["playback_error"] = playback_error
        else:
            opening_payload["tts_output"] = {
                "ok": True,
                "requested_provider": args.tts_provider,
                "text": opening_text,
                "skipped": True,
            }
    except SpeechSynthesisError as exc:
        opening_payload["tts_output"] = {
            "ok": False,
            "requested_provider": args.tts_provider,
            "text": opening_text,
            "error": str(exc),
        }
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "session_opening_tts",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )

    turns: list[dict[str, Any]] = []
    exit_code = 0
    stop_reason = "max_turns_reached"
    current_playback: PendingPlayback | None = None
    previous_playback_audio_path: str | Path | None = None
    session_memory_summary: str | None = None

    try:
        recorded_clip = _record_turn(recorder, args=args, turn_index=0)
    except AudioRecordingError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "audio_recording",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    NO_SPEECH_MAX_RETRIES = 2
    turn_index = 0
    while turn_index < args.max_turns:
        # B2: retry recording + ASR up to NO_SPEECH_MAX_RETRIES times on no-speech
        no_speech_attempts = 0
        while True:
            try:
                turn_payload, current_playback, next_current_task = _run_turn(
                    args=args,
                    current_task=current_task,
                    ordered_task_contexts=ordered_task_contexts,
                    recorded_clip=recorded_clip,
                    audio_path=recorded_clip.audio_path,
                    playback_reference_audio_path=previous_playback_audio_path,
                    session_memory_summary=session_memory_summary,
                    transcriber=transcriber,
                    session_id=session_id,
                    turn_index=turn_index,
                    phase6_client=phase6_client,
                )
                break  # success — exit no-speech retry loop
            except NoSpeechDetectedError:
                no_speech_attempts += 1
                print(
                    f"[voice-session] no speech detected (attempt {no_speech_attempts}/{NO_SPEECH_MAX_RETRIES}), re-recording...",
                    file=sys.stderr,
                )
                if no_speech_attempts > NO_SPEECH_MAX_RETRIES:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "stage": "voice_turn",
                                "turn_index": turn_index,
                                "error": f"no speech detected after {NO_SPEECH_MAX_RETRIES} retries",
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 1
                try:
                    recorded_clip = _record_turn(recorder, args=args, turn_index=turn_index)
                except AudioRecordingError as rec_exc:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "stage": "audio_recording",
                                "turn_index": turn_index,
                                "error": str(rec_exc),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 1
            except RuntimeError as exc:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "stage": "voice_turn",
                            "turn_index": turn_index,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 1

        turns.append(turn_payload)
        session_memory_summary = _build_session_memory_summary(turn_payload)
        print(
            json.dumps(
                {
                    "turn_index": turn_index,
                    "child_input_text": turn_payload["child_input_text"],
                    "task_signal": turn_payload["signal_resolution"]["task_signal"],
                    "reply_text": turn_payload["interaction_generation"]["reply_text"],
                    "session_id": session_id,
                    "phase6_status": (
                        turn_payload.get("phase6_submit", {})
                        .get("response", {})
                        .get("session", {})
                        .get("status")
                        if isinstance(turn_payload.get("phase6_submit"), dict)
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )

        current_task = next_current_task
        stop_reason = _should_stop_turn_loop(
            turn_payload=turn_payload,
            phase6_submit_enabled=phase6_client is not None,
        )
        if stop_reason is not None:
            pending_playback = current_playback
            if pending_playback is not None:
                previous_playback_audio_path = pending_playback.audio_path
                playback_error = _wait_for_playback(pending_playback)
                if playback_error is not None:
                    turn_payload.setdefault("tts_output", {})
                    turn_payload["tts_output"]["playback_ok"] = False
                    turn_payload["tts_output"]["playback_error"] = playback_error
                    exit_code = 1
                else:
                    turn_payload.setdefault("tts_output", {})
                    turn_payload["tts_output"]["playback_ok"] = True
            break

        # B3: wait for TTS to finish BEFORE starting the next recording so the
        # microphone does not capture the AI's voice as part of the child's input
        if current_playback is not None:
            previous_playback_audio_path = current_playback.audio_path
            playback_error = _wait_for_playback(current_playback)
            current_playback = None
            if playback_error is not None:
                turn_payload.setdefault("tts_output", {})
                turn_payload["tts_output"]["playback_ok"] = False
                turn_payload["tts_output"]["playback_error"] = playback_error
                exit_code = 1
            else:
                turn_payload.setdefault("tts_output", {})
                turn_payload["tts_output"]["playback_ok"] = True

        try:
            next_recorded_clip = _record_turn(recorder, args=args, turn_index=turn_index + 1)
        except AudioRecordingError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "audio_recording",
                        "turn_index": turn_index + 1,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        recorded_clip = next_recorded_clip
        turn_index += 1

    final_payload = {
        "runtime_mode": args.runtime_mode,
        "session_id": session_id,
        "phase6_bootstrap": phase6_bootstrap,
        "stopped_reason": stop_reason,
        "turn_count": len(turns),
        "turns": turns,
        "session_memory_summary": session_memory_summary,
    }
    if turns:
        final_payload["latest_turn"] = turns[-1]
    final_payload["session_opening"] = opening_payload

    print(json.dumps(final_payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
