#!/usr/bin/env python3
"""Fast voice session — HTTP fast path with optional realtime TTS streaming.

Modeled exactly after the robot project (洗浴机器人语音播报):
  record (VAD) → ASR (HTTP) → LLM (HTTP) → TTS (HTTP) → play (blocking) → repeat
  with optional Phase 6 submission for full Fire Station flow.
  `--stream-tts` switches only the TTS leg to Qwen realtime WebSocket playback.
  When `--submit-phase6` is enabled, streaming TTS is the default unless you pass `--no-stream-tts`.

ASR: Qwen3-ASR-Flash via OpenAI-compatible chat/completions (base64 audio, single POST)
TTS: DashScope HTTP TTS (single POST)
LLM: Qwen/Ark via existing interaction provider

Usage:
  python scripts/run_voice_fast.py --task-goal "帮助小朋友完成消防站任务"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SESSION_RUNTIME_ROOT_DIR = ROOT_DIR.parent / "session"
if str(SESSION_RUNTIME_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(SESSION_RUNTIME_ROOT_DIR))

PHASE5_ROOT_DIR = ROOT_DIR.parent / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from input_understanding import CompletionPoint, TaskContext, build_task_followup_question  # noqa: E402
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
from runtime.env_loader import build_runtime_env  # noqa: E402
from runtime_pipeline import run_phase7_turn_pipeline  # noqa: E402
from session_runtime.phase5_bridge import load_fire_station_task_blueprints  # noqa: E402
from voice_input import AudioRecordingError, SilenceAwareWavRecorder  # noqa: E402
from voice_output import (  # noqa: E402
    SpeechSynthesisError,
    synthesize_and_play_realtime_reply_audio,
    synthesize_reply_audio,
)

from urllib import error, request  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ASR_MODEL = "qwen3-asr-flash"
API_KEY_ENV_KEYS = ("QWEN_API_KEY", "DASHSCOPE_API_KEY", "QWEN_RT_API_KEY", "DASHSCOPE_RT_API_KEY")

FAST_KEEP_TRYING_TIMEOUT = 1.2
FAST_KEEP_TRYING_RETRY_TIMEOUT = 0.0
FAST_PATH_TIMEOUT = 1.8
NO_SPEECH_MAX_RETRIES = 1

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


def _format_keep_trying_budget(first_timeout_seconds: float, retry_timeout_seconds: float) -> str:
    if first_timeout_seconds <= 0:
        return "unlimited"
    if retry_timeout_seconds > 0:
        return f"{first_timeout_seconds}+{retry_timeout_seconds}s"
    return f"{first_timeout_seconds}s"


def _load_fire_station_task_blueprints() -> tuple[str, list[dict[str, object]]]:
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
    raw_specs = FIRE_STATION_TASK_COMPLETION_POINT_SPECS.get(task_id, ())
    return tuple(CompletionPoint.parse(raw_spec) for raw_spec in raw_specs)


def _build_task_context_from_payload(
    task_payload: dict[str, object],
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


def _build_ordered_task_contexts(
    task_blueprints: list[dict[str, object]],
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


def _build_session_memory_summary(turn_payload: dict[str, object]) -> str:
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


def _has_completed_assistant_led_summary(phase6_response: dict[str, object] | None) -> bool:
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
    phase6_response: dict[str, object] | None,
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
    phase6_response: dict[str, object] | None,
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
    lead = _normalize_reply_lead(base_reply)
    if not lead:
        assistant_reply = phase6_response.get("current_turn") if isinstance(phase6_response, dict) else None
        if isinstance(assistant_reply, dict):
            assistant_payload = assistant_reply.get("assistant_reply")
            if isinstance(assistant_payload, dict):
                lead = _normalize_reply_lead(str(assistant_payload.get("reply_text") or ""))
    if not lead:
        lead = "这一步完成啦"
    if followup_question in lead:
        return lead
    return f"{lead}。{followup_question}"


def _should_stop_turn_loop(*, turn_payload: dict[str, object], phase6_submit_enabled: bool) -> str | None:
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


# ---------------------------------------------------------------------------
# HTTP ASR — single POST, base64 audio in chat message, no WebSocket
# ---------------------------------------------------------------------------

def _get_api_key(env: dict[str, str]) -> str:
    for key in API_KEY_ENV_KEYS:
        val = (env.get(key) or "").strip()
        if val:
            return val
    raise RuntimeError(f"Missing API key. Set one of: {', '.join(API_KEY_ENV_KEYS)}")


def _http_asr(audio_path: Path, *, api_key: str) -> str:
    """Transcribe audio via Qwen3-ASR-Flash chat completions API (single HTTP POST)."""
    audio_bytes = audio_path.read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

    # Determine MIME type from suffix
    suffix = audio_path.suffix.lower()
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}.get(
        suffix.lstrip("."), "audio/wav"
    )

    payload = {
        "model": ASR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:{mime};base64,{audio_b64}",
                        },
                    }
                ],
            }
        ],
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = request.Request(
        DASHSCOPE_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ASR HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"ASR connection failed: {exc.reason}") from exc

    # Extract transcript from OpenAI-compatible response
    choices = result.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if text:
            return text

    raise RuntimeError(f"ASR empty response: {json.dumps(result, ensure_ascii=False)[:300]}")


# ---------------------------------------------------------------------------
# Blocking playback — like the robot: sd.play() + time.sleep()
# ---------------------------------------------------------------------------

def _play_blocking(audio_path: Path, *, gain: float = 0.6) -> None:
    data, sr = sf.read(str(audio_path))
    if gain != 1.0:
        data = data * gain
    duration = len(data) / sr
    print(f"  [播放] {duration:.1f}s", file=sys.stderr)
    sd.stop()
    sd.play(data.astype(np.float32), sr)
    sd.wait()
    sd.stop()


def _speak(
    *,
    provider_mode: str,
    text: str,
    gain: float,
    no_playback: bool,
    qwen_voice: str | None,
    playback_device: str | int | None,
    stream_tts: bool,
    reply_ready_at: float | None,
) -> None:
    if no_playback or provider_mode == "none" or not text:
        return
    try:
        t0 = time.monotonic()
        if stream_tts:
            synth = synthesize_and_play_realtime_reply_audio(
                text=text,
                provider_mode=provider_mode,
                qwen_voice=qwen_voice,
                playback_device=playback_device,
                playback_gain=gain,
                reply_ready_at=reply_ready_at,
            )
        else:
            synth = synthesize_reply_audio(
                text=text,
                provider_mode=provider_mode,
                qwen_voice=qwen_voice,
            )
        if synth is None:
            return
        ms = (time.monotonic() - t0) * 1000
        print(f"  [TTS] {ms:.0f}ms -> {synth.provider_name}", file=sys.stderr)
        if not stream_tts:
            if reply_ready_at is not None:
                print(
                    f"  [播报间隔] {(time.monotonic() - reply_ready_at) * 1000:.0f}ms -> 开始播放",
                    file=sys.stderr,
                )
            _play_blocking(Path(synth.audio_path), gain=gain)
    except SpeechSynthesisError as exc:
        print(f"  [TTS失败] {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fast voice session — HTTP fast path with optional realtime TTS streaming")
    p.add_argument("--task-id", default="task_demo")
    p.add_argument("--task-name", default="当前任务")
    p.add_argument("--task-goal", required=True)
    p.add_argument("--expected-child-action", default="继续回应当前任务。")
    p.add_argument("--scene-context", default=None)
    p.add_argument("--scene-style", default="playful_companion")
    p.add_argument("--max-turns", type=int, default=12)
    p.add_argument("--record-seconds", type=float, default=20.0)
    p.add_argument("--record-device", default=None)
    p.add_argument(
        "--listen-min-speech-seconds",
        type=positive_float,
        default=0.16,
        help="Minimum speech duration before silence can end a turn early.",
    )
    p.add_argument(
        "--listen-silence-seconds",
        type=positive_float,
        default=1.0,
        help="Trailing silence needed to end a turn early.",
    )
    p.add_argument(
        "--interaction-provider", default="qwen",
        choices=("qwen", "minimax", "ark_doubao", "template", "auto"),
    )
    p.add_argument(
        "--provider-fast-timeout-seconds",
        type=non_negative_float,
        default=FAST_PATH_TIMEOUT,
        help="Fast-path timeout for task_completed / end_session. Set to 0 to disable the timeout.",
    )
    p.add_argument(
        "--provider-keep-trying-timeout-seconds",
        type=non_negative_float,
        default=FAST_KEEP_TRYING_TIMEOUT,
        help="Keep-trying timeout. Set to 0 to disable the timeout.",
    )
    p.add_argument(
        "--provider-keep-trying-retry-timeout-seconds",
        type=non_negative_float,
        default=FAST_KEEP_TRYING_RETRY_TIMEOUT,
        help="Optional keep-trying retry timeout. Set to 0 to disable the retry.",
    )
    p.add_argument(
        "--tts-provider",
        default="auto",
        choices=("auto", "qwen", "say", "none"),
        help="Reply audio provider. auto tries Qwen first, then macOS say fallback.",
    )
    p.add_argument("--tts-voice", default=None)
    p.add_argument("--playback-gain", type=float, default=0.6)
    p.add_argument("--no-playback", action="store_true")
    p.add_argument(
        "--stream-tts",
        dest="stream_tts",
        action="store_true",
        help="Use Qwen realtime TTS and stream playback as audio chunks arrive.",
    )
    p.add_argument(
        "--no-stream-tts",
        dest="stream_tts",
        action="store_false",
        help="Disable streaming TTS and wait for the full audio file before playback.",
    )
    p.set_defaults(stream_tts=None)
    p.add_argument("--session-id", default=None, help="Optional Phase 6 session id.")
    p.add_argument(
        "--phase6-api-base",
        default=None,
        help="Optional Phase 6 API base, for example http://127.0.0.1:4183/api/session-runtime",
    )
    p.add_argument(
        "--submit-phase6",
        action="store_true",
        help="If set, create or reuse a Phase 6 session and submit each turn to it.",
    )
    p.add_argument(
        "--phase6-task-id",
        action="append",
        dest="phase6_task_ids",
        default=[],
        help=(
            "Optional task ids used when auto-creating a Phase 6 session. "
            "Defaults to the full Fire Station scene task list when omitted."
        ),
    )
    p.add_argument(
        "--qwen-max-tokens",
        type=positive_int,
        default=None,
        help="Optional Qwen max_tokens override. Omit it to let the provider decide and send no explicit cap.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    env = build_runtime_env(PHASE5_ROOT_DIR / ".env.local")
    api_key = _get_api_key(env)
    if args.qwen_max_tokens is not None:
        os.environ["QWEN_MAX_TOKENS"] = str(args.qwen_max_tokens)
        os.environ["DASHSCOPE_MAX_TOKENS"] = str(args.qwen_max_tokens)
    else:
        os.environ.pop("QWEN_MAX_TOKENS", None)
        os.environ.pop("DASHSCOPE_MAX_TOKENS", None)
    effective_stream_tts = args.stream_tts if args.stream_tts is not None else bool(args.submit_phase6)

    if args.submit_phase6 and not args.phase6_api_base:
        raise SystemExit("--submit-phase6 requires --phase6-api-base")

    effective_tts_provider = args.tts_provider
    phase6_client = Phase6SessionClient(args.phase6_api_base) if args.submit_phase6 else None
    session_id = args.session_id
    phase6_bootstrap: dict[str, object] | None = None
    opening_text = "好了，开始吧。"
    session_scene_context: str | None = None
    scene_id = "fast_session"
    session_task_ids: tuple[str, ...] = ()
    session_task_blueprints: list[dict[str, object]] = []

    if phase6_client is not None:
        scene_id, session_task_blueprints = _load_fire_station_task_blueprints()
        session_task_ids = tuple(
            str(task.get("task_id") or "")
            for task in session_task_blueprints
            if str(task.get("task_id") or "")
        )
        opening_text, session_scene_context = _build_fire_station_story_context(args.interaction_provider)
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
                print(f"[phase6] created session {session_id}", file=sys.stderr)
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

    fallback_task = TaskContext(
        task_id=args.task_id,
        task_name=args.task_name,
        task_goal=args.task_goal,
        expected_child_action=args.expected_child_action,
        completion_points=(),
        completion_match_mode="any",
        scene_context=session_scene_context if session_scene_context is not None else args.scene_context,
        scene_style=args.scene_style,
    )
    if phase6_bootstrap is not None and isinstance(phase6_bootstrap.get("current_task"), dict):
        current_task = _build_task_context_from_payload(
            phase6_bootstrap.get("current_task") or {},
            scene_context=session_scene_context,
            scene_style=args.scene_style,
            fallback_task=fallback_task,
        )
    else:
        current_task = fallback_task

    ordered_task_contexts = (
        _build_ordered_task_contexts(
            session_task_blueprints,
            task_ids=tuple(args.phase6_task_ids or list(session_task_ids)),
            scene_context=session_scene_context,
            scene_style=args.scene_style,
        )
        if session_task_blueprints
        else ()
    )

    recorder = SilenceAwareWavRecorder(
        device=args.record_device,
        min_speech_seconds=args.listen_min_speech_seconds,
        silence_seconds=args.listen_silence_seconds,
    )

    print("=" * 50, file=sys.stderr)
    print("快速语音会话 — HTTP快链 + 可选流式TTS", file=sys.stderr)
    print(f"  ASR: {ASR_MODEL} (HTTP chat/completions, base64上传)", file=sys.stderr)
    if args.tts_provider == "auto":
        print("  TTS: auto -> qwen (fallback say)", file=sys.stderr)
    else:
        print(f"  TTS: {effective_tts_provider} (HTTP helper)", file=sys.stderr)
    if effective_stream_tts:
        print("  TTS流式: on -> qwen realtime streaming playback", file=sys.stderr)
    print(
        f"  LLM: {args.interaction_provider} (timeout {_format_keep_trying_budget(args.provider_keep_trying_timeout_seconds, args.provider_keep_trying_retry_timeout_seconds)})",
        file=sys.stderr,
    )
    if phase6_client is not None:
        print(f"  Phase6: submit enabled (Fire Station full flow, scene {scene_id})", file=sys.stderr)
    print(f"  无回声消除 / {'边播边出' if effective_stream_tts else '阻塞播放'}", file=sys.stderr)
    print("=" * 50, file=sys.stderr)

    _speak(
        provider_mode=effective_tts_provider,
        text=opening_text,
        gain=args.playback_gain,
        no_playback=args.no_playback,
        qwen_voice=args.tts_voice,
        playback_device=args.record_device,
        stream_tts=effective_stream_tts,
        reply_ready_at=time.monotonic(),
    )

    session_memory: str | None = None
    turns: list[dict[str, object]] = []

    for turn_index in range(args.max_turns):
        print(f"\n--- 第 {turn_index + 1} 轮 ---", file=sys.stderr)

        # ---- 1. RECORD (VAD) ----
        wav_path = Path("/tmp") / f"fast-voice-turn-{turn_index + 1}.wav"
        print("  [录音] 请说话...", file=sys.stderr)
        try:
            clip = recorder.record(seconds=args.record_seconds, output_path=wav_path)
        except AudioRecordingError as exc:
            print(f"  [录音失败] {exc}", file=sys.stderr)
            continue
        print(f"  [录音] {clip.duration_seconds:.1f}s", file=sys.stderr)

        # ---- 2. ASR (HTTP POST) ----
        transcript = None
        for attempt in range(1 + NO_SPEECH_MAX_RETRIES):
            t0 = time.monotonic()
            try:
                transcript = _http_asr(Path(clip.audio_path), api_key=api_key)
                ms = (time.monotonic() - t0) * 1000
                print(f"  [ASR] {ms:.0f}ms -> \"{transcript}\"", file=sys.stderr)
                break
            except RuntimeError as exc:
                err_str = str(exc)
                if "empty" in err_str.lower() and attempt < NO_SPEECH_MAX_RETRIES:
                    print(f"  [ASR] 没听到 (尝试 {attempt + 1})", file=sys.stderr)
                    print("  [录音] 再说一次...", file=sys.stderr)
                    try:
                        clip = recorder.record(seconds=args.record_seconds, output_path=wav_path)
                    except AudioRecordingError:
                        break
                else:
                    print(f"  [ASR失败] {exc}", file=sys.stderr)
                    break

        if not transcript:
            continue

        # Exit keywords
        if any(w in transcript for w in ("退出", "再见", "结束对话", "拜拜")):
            _speak(
                provider_mode=effective_tts_provider,
                text="好的，再见！",
                gain=args.playback_gain,
                no_playback=args.no_playback,
                qwen_voice=args.tts_voice,
                playback_device=args.record_device,
                stream_tts=effective_stream_tts,
                reply_ready_at=time.monotonic(),
            )
            print("  [会话结束]", file=sys.stderr)
            break

        # ---- 3. LLM ----
        t0 = time.monotonic()
        next_task_hint = _find_next_task_hint(current_task, ordered_task_contexts)
        try:
                bridge = run_phase7_turn_pipeline(
                    child_input_text=transcript,
                    current_task=current_task,
                    interaction_provider=args.interaction_provider,
                    provider_keep_trying_timeout_seconds=args.provider_keep_trying_timeout_seconds,
                    provider_keep_trying_retry_timeout_seconds=args.provider_keep_trying_retry_timeout_seconds,
                    provider_fast_timeout_seconds=args.provider_fast_timeout_seconds,
                    session_memory_summary=session_memory,
                    next_task_hint=next_task_hint,
                )
        except Exception as exc:
            print(f"  [LLM失败] {exc}", file=sys.stderr)
            continue
        ms = (time.monotonic() - t0) * 1000
        reply = bridge.interaction_generation.reply_text
        generation_provider_name = getattr(bridge.interaction_generation, "provider_name", None)
        signal = bridge.signal_resolution.task_signal
        print(f"  [LLM] {ms:.0f}ms signal={signal}", file=sys.stderr)

        turn_payload = bridge.to_dict()
        turn_payload["child_input_text"] = transcript
        turn_payload["current_task"] = current_task.to_dict()
        turn_payload["session_memory_summary"] = session_memory
        turn_payload["session_id"] = session_id

        next_current_task = current_task
        if phase6_client is not None and session_id is not None:
            try:
                phase6_response = phase6_client.submit_turn(
                    session_id=session_id,
                    payload=bridge.phase6_turn_payload,
                )
                turn_payload["phase6_submit"] = {
                    "ok": True,
                    "api_base": args.phase6_api_base,
                    "response": phase6_response,
                }
                if isinstance(phase6_response, dict):
                    next_current_task = _build_task_context_from_payload(
                        phase6_response.get("current_task") or {},
                        scene_context=session_scene_context,
                        scene_style=args.scene_style,
                        fallback_task=current_task,
                    )
                    if generation_provider_name != "qwen_unified":
                        reply = _compose_phase6_guided_reply(
                            base_reply=reply,
                            current_task=current_task,
                            next_current_task=next_current_task,
                            phase6_response=phase6_response,
                        )
                    turn_payload["next_current_task"] = next_current_task.to_dict()
            except Phase6BridgeError as exc:
                turn_payload["phase6_submit"] = {
                    "ok": False,
                    "api_base": args.phase6_api_base,
                    "error": str(exc),
                }
                print(f"  [Phase6失败] {exc}", file=sys.stderr)
                return 1
        else:
            turn_payload["next_current_task"] = current_task.to_dict()

        turn_payload["interaction_generation"]["reply_text"] = reply
        session_memory = _build_session_memory_summary(turn_payload)
        turns.append(turn_payload)
        print(f"  [回复] {reply}", file=sys.stderr)

        # ---- 4. TTS + PLAY (blocking) ----
        _speak(
            provider_mode=effective_tts_provider,
            text=reply,
            gain=args.playback_gain,
            no_playback=args.no_playback,
            qwen_voice=args.tts_voice,
            playback_device=args.record_device,
            stream_tts=effective_stream_tts,
            reply_ready_at=time.monotonic(),
        )

        # Structured output
        print(json.dumps({
            "turn": turn_index + 1,
            "child": transcript,
            "signal": signal,
            "reply": reply,
            "session_id": session_id,
            "task_id": current_task.task_id,
            "session_memory_summary": session_memory,
            **({"phase6_submit": turn_payload.get("phase6_submit")} if "phase6_submit" in turn_payload else {}),
        }, ensure_ascii=False))

        stop_reason = _should_stop_turn_loop(
            turn_payload=turn_payload,
            phase6_submit_enabled=phase6_client is not None,
        )
        current_task = next_current_task
        if stop_reason is not None:
            print(f"  [信号停止] {stop_reason}", file=sys.stderr)
            break

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[voice] 已中断。", file=sys.stderr)
        raise SystemExit(130)
