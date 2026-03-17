from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from .core import AssistantTurnResult, SessionRuntimeService
from .persistence import JsonSessionStore

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[2] / "05-dialog-runtime"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.ark_client import ArkClient, ArkConfig, ArkConfigError, ArkRequestError  # type: ignore[import-not-found]
from runtime.dialog_runtime import (  # type: ignore[import-not-found]
    DEFAULT_FIRE_STATION_SCENE_FILE,
    DEFAULT_FIRE_STATION_SMOKE_TASK_IDS,
)
from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]
from runtime.scene_loader import (  # type: ignore[import-not-found]
    SceneLoadError,
    get_candidate_task,
    load_scene_pack,
)

DEFAULT_SCENE_FILE = DEFAULT_FIRE_STATION_SCENE_FILE
DEFAULT_SESSION_TASK_IDS = DEFAULT_FIRE_STATION_SMOKE_TASK_IDS
TURN_PROMPT_VERSION = "phase6_fire_station_session_turn_v1"
ALLOWED_GUIDANCE_TYPES = {"observation", "decision", "action", "confirmation", "repair"}
GUIDANCE_TYPE_ALIASES = {"reflection": "confirmation"}
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

FIRE_STATION_AUTO_COMPLETE_HINTS: dict[str, tuple[str, ...]] = {
    "fs_002": ("内部", "外部", "消防站", "别的场景", "外面"),
    "fs_003": ("消防车", "直升机", "消防员"),
    "fs_004": ("大火", "小火", "左边", "右边", "床", "位置"),
}


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(candidate)
        if not match:
            raise
        loaded = json.loads(match.group(0))

    if not isinstance(loaded, dict):
        raise ValueError("Model response JSON must decode to an object.")
    return loaded


def _string_value(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _guidance_type(value: Any, fallback: str = "action") -> str:
    raw_type = _string_value(value, fallback)
    normalized = GUIDANCE_TYPE_ALIASES.get(raw_type, raw_type)
    if normalized in ALLOWED_GUIDANCE_TYPES:
        return normalized

    fallback_type = GUIDANCE_TYPE_ALIASES.get(fallback, fallback)
    return fallback_type if fallback_type in ALLOWED_GUIDANCE_TYPES else "action"


def load_fire_station_task_blueprints(
    scene_file: str = DEFAULT_SCENE_FILE,
    task_ids: Sequence[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    scene_path = PHASE5_ROOT_DIR / scene_file
    scene_pack = load_scene_pack(scene_path)
    scene_id = str(scene_pack.get("scene_id") or "classic_world_fire_station")
    candidate_tasks = list(scene_pack.get("candidate_tasks") or [])
    selected_task_ids = (
        list(task_ids)
        if task_ids is not None
        else [str(task.get("task_id") or "") for task in candidate_tasks]
    )

    if not selected_task_ids:
        raise ValueError("task_ids must not be empty")

    selected: list[dict[str, Any]] = []
    for task_id in selected_task_ids:
        task = get_candidate_task(scene_pack, task_id)
        selected.append(
            {
                "task_id": str(task.get("task_id") or ""),
                "name": str(task.get("name") or ""),
                "goal": str(task.get("goal") or ""),
                "expected_child_action": str(task.get("expected_child_action") or ""),
            }
        )

    if not selected:
        available = ", ".join(str(task.get("task_id") or "") for task in candidate_tasks)
        raise ValueError(f"No task blueprints selected. Available: {available}")

    return scene_id, selected


class Phase5FireStationTurnResponder:
    def __init__(
        self,
        root_dir: Path = PHASE5_ROOT_DIR,
        scene_file: str = DEFAULT_SCENE_FILE,
        dotenv_file: str = ".env.local",
    ):
        self.root_dir = root_dir
        self.scene_file = scene_file
        self.dotenv_file = dotenv_file
        self.scene_id = "classic_world_fire_station"
        self.scene_pack: dict[str, Any] | None = None
        self.setup_error: str | None = None
        self.client: ArkClient | None = None

        scene_path = self.root_dir / self.scene_file
        try:
            self.scene_pack = load_scene_pack(scene_path)
            self.scene_id = str(self.scene_pack.get("scene_id") or self.scene_id)
        except SceneLoadError as exc:
            self.setup_error = str(exc)
            return

        try:
            runtime_env = build_runtime_env(self.root_dir / self.dotenv_file)
            ark_config = ArkConfig.from_env(runtime_env)
            self.client = ArkClient(ark_config)
        except (ArkConfigError, ValueError) as exc:
            self.setup_error = str(exc)

    def generate_reply(
        self,
        session: Any,
        current_task: Any,
        child_input_text: str,
        resolved_task_signal: str,
        upcoming_task: Any | None,
    ) -> AssistantTurnResult:
        if self.setup_error:
            return self._error_result(current_task.expected_child_action, self.setup_error)
        if self.scene_pack is None or self.client is None:
            return self._error_result(current_task.expected_child_action, "Session runtime failed to start.")

        try:
            task = get_candidate_task(self.scene_pack, current_task.task_id)
            next_task_line = (
                f"next_task: {upcoming_task.task_id} / {upcoming_task.name}"
                if upcoming_task is not None
                else "next_task: none"
            )
            completed_tasks = [
                task_state.name
                for task_state in session.tasks
                if task_state.status == "completed"
            ]
            completed_task_line = "、".join(completed_tasks) if completed_tasks else "none"

            recent_turns = session.turns[-2:]
            recent_turn_lines: list[str] = []
            for turn in recent_turns:
                recent_turn_lines.append(f"child: {turn.child_input_text}")
                recent_turn_lines.append(f"assistant: {turn.assistant_reply.reply_text}")
            if not recent_turn_lines:
                recent_turn_lines.append("recent_turns: none")

            system_prompt = "\n".join(
                [
                    "你是 AI 积木玩具的中文文本引导员，正在持续陪孩子完成一个真实 session。",
                    "这不是第一轮快照，而是 Phase 6 的多回合 session runtime。",
                    "你必须结合孩子刚说的话、当前 task 和 task_signal 来回复。",
                    "reply_text 最多两句，每句尽量不超过 18 个字。",
                    "next_expected_action 要短，尽量控制在 12 个字内。",
                    "如果 task_signal=task_completed，语气优先确认完成，再顺势引到下一个最小动作。",
                    "如果 task_signal=keep_trying，继续围绕当前 task 给单一步引导。",
                    "如果 task_signal=end_session，简短收尾，不再继续派发新任务。",
                    "你必须只输出一个 JSON 对象，不要加代码块，不要加额外说明。",
                    'JSON 字段固定为 reply_text, guidance_type, next_expected_action。',
                    "guidance_type 只能从 observation, decision, action, confirmation, repair 里选一个。",
                ]
            )

            context_prompt = "\n".join(
                [
                    f"scene_id: {self.scene_id}",
                    f"task_id: {current_task.task_id}",
                    f"task_name: {current_task.name}",
                    f"task_goal: {task.get('goal') or current_task.goal}",
                    f"expected_child_action: {current_task.expected_child_action}",
                    f"resolved_task_signal: {resolved_task_signal}",
                    f"turn_index: {session.turn_count}",
                    f"completed_tasks: {completed_task_line}",
                    next_task_line,
                    "recent_turns:",
                    *recent_turn_lines,
                ]
            )

            user_prompt = "\n".join(
                [
                    f"child_input_text: {child_input_text.strip() or '(empty)'}",
                    "请输出这一轮该怎么和孩子说。",
                    "要求：",
                    "1. 只回复当前这一轮，不讲长篇总结。",
                    "2. 如果孩子刚完成，就先确认，再给下一步。",
                    "3. 如果孩子还没完成，就继续引导当前任务，不要跳太远。",
                    "4. 输出严格 JSON。",
                ]
            )

            chat_result = self.client.create_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"{context_prompt}\n\n{user_prompt}"},
                ]
            )
            model_payload = _extract_json_object(chat_result.content_text)
            return AssistantTurnResult(
                prompt_version=TURN_PROMPT_VERSION,
                reply_text=_string_value(model_payload.get("reply_text"), chat_result.content_text),
                guidance_type=_guidance_type(model_payload.get("guidance_type")),
                next_expected_action=_string_value(
                    model_payload.get("next_expected_action"),
                    upcoming_task.expected_child_action
                    if resolved_task_signal == "task_completed" and upcoming_task is not None
                    else current_task.expected_child_action,
                ),
                error=None,
            )
        except (SceneLoadError, ArkRequestError, ValueError, json.JSONDecodeError) as exc:
            return self._error_result(current_task.expected_child_action, str(exc))

    def _error_result(self, next_expected_action: str, message: str) -> AssistantTurnResult:
        return AssistantTurnResult(
            prompt_version=TURN_PROMPT_VERSION,
            reply_text="",
            guidance_type="runtime_error",
            next_expected_action=next_expected_action,
            error=message,
        )


def build_default_runtime_service(
    scene_file: str = DEFAULT_SCENE_FILE,
    task_ids: Sequence[str] | None = None,
    store_file: str | Path | None = None,
) -> SessionRuntimeService:
    scene_id, task_blueprints = load_fire_station_task_blueprints(
        scene_file=scene_file,
        task_ids=None,
    )
    responder = Phase5FireStationTurnResponder(scene_file=scene_file)
    persistence = JsonSessionStore(store_file) if store_file else None
    return SessionRuntimeService(
        scene_id=scene_id,
        task_blueprints=task_blueprints,
        responder=responder,
        auto_complete_keywords=FIRE_STATION_AUTO_COMPLETE_HINTS,
        default_task_ids=task_ids or DEFAULT_SESSION_TASK_IDS,
        persistence=persistence,
    )
