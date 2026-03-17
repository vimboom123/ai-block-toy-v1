from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.ark_client import ArkClient, ArkConfig, ArkConfigError, ArkRequestError
from runtime.dialog_prompt_builder import PROMPT_VERSION, build_prompt_bundle
from runtime.env_loader import build_runtime_env
from runtime.scene_loader import SceneLoadError, get_candidate_task, load_scene_pack
from runtime.session_utils import (
    CURRENT_TASK_INDEX_SEMANTICS_LATEST_IN_SNAPSHOT,
    SESSION_SCOPE_REQUEST_SCOPED_SNAPSHOT,
    iso_now,
)

DEFAULT_FIRE_STATION_SCENE_FILE = "scenes/classic_world_fire_station.scene.yaml"
DEFAULT_FIRE_STATION_SMOKE_TASK_IDS = ("fs_002", "fs_003", "fs_004")
ALLOWED_GUIDANCE_TYPES = {"observation", "decision", "action", "confirmation", "repair"}
GUIDANCE_TYPE_ALIASES = {"reflection": "confirmation"}
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class DialogSmokeResult:
    scene_id: str
    prompt_version: str
    task_id: str
    reply_text: str
    guidance_type: str
    next_expected_action: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DialogSessionSnapshot:
    session_id: str
    session_scope: str
    is_persisted_session: bool
    source_kind: str
    scene_id: str
    generated_at: str
    updated_at: str
    current_task_index: int | None
    current_task_index_semantics: str
    task_count: int
    tasks: tuple[DialogSmokeResult, ...]

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_scope": self.session_scope,
            "is_persisted_session": self.is_persisted_session,
            "source_kind": self.source_kind,
            "scene_id": self.scene_id,
            "generated_at": self.generated_at,
            "updated_at": self.updated_at,
            "current_task_index": self.current_task_index,
            "current_task_index_semantics": self.current_task_index_semantics,
            "task_count": self.task_count,
        }

    def task_dicts(self) -> list[dict[str, Any]]:
        return [task.to_dict() for task in self.tasks]


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


class FireStationDialogRuntime:
    def __init__(
        self,
        root_dir: Path,
        scene_file: str = DEFAULT_FIRE_STATION_SCENE_FILE,
        dotenv_file: str = ".env.local",
    ):
        self.root_dir = root_dir
        self.scene_file = scene_file
        self.dotenv_file = dotenv_file
        self.scene_pack: dict[str, Any] | None = None
        self.scene_id = "classic_world_fire_station"
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

    def run_tasks(self, task_ids: list[str]) -> list[DialogSmokeResult]:
        return [self.run_task(task_id) for task_id in task_ids]

    def run_session_snapshot(self, task_ids: list[str]) -> DialogSessionSnapshot:
        generated_at = iso_now()
        results = tuple(self.run_tasks(task_ids))
        updated_at = iso_now()
        scene_id = next((result.scene_id for result in results if result.scene_id), self.scene_id)

        return DialogSessionSnapshot(
            session_id=f"ses_runtime_{uuid4().hex[:12]}",
            session_scope=SESSION_SCOPE_REQUEST_SCOPED_SNAPSHOT,
            is_persisted_session=False,
            source_kind="runtime",
            scene_id=scene_id,
            generated_at=generated_at,
            updated_at=updated_at,
            current_task_index=(len(results) - 1) if results else None,
            current_task_index_semantics=CURRENT_TASK_INDEX_SEMANTICS_LATEST_IN_SNAPSHOT,
            task_count=len(results),
            tasks=results,
        )

    def run_task(self, task_id: str) -> DialogSmokeResult:
        prompt_version = PROMPT_VERSION
        expected_child_action = ""

        if self.scene_pack is not None:
            try:
                expected_child_action = str(
                    get_candidate_task(self.scene_pack, task_id).get("expected_child_action", "")
                )
            except SceneLoadError:
                expected_child_action = ""

        if self.setup_error:
            return self._error_result(task_id, expected_child_action, self.setup_error)
        if self.scene_pack is None or self.client is None:
            return self._error_result(task_id, expected_child_action, "Dialog runtime failed to start.")

        try:
            prompt_bundle = build_prompt_bundle(self.scene_pack, task_id)
            prompt_version = prompt_bundle.prompt_version
            chat_result = self.client.create_chat_completion(
                [
                    {"role": "system", "content": prompt_bundle.system_prompt},
                    {
                        "role": "user",
                        "content": f"{prompt_bundle.context_prompt}\n\n{prompt_bundle.user_prompt}",
                    },
                ]
            )
            model_payload = _extract_json_object(chat_result.content_text)
            return DialogSmokeResult(
                scene_id=prompt_bundle.scene_id,
                prompt_version=prompt_bundle.prompt_version,
                task_id=prompt_bundle.task_id,
                reply_text=_string_value(model_payload.get("reply_text"), chat_result.content_text),
                guidance_type=_guidance_type(model_payload.get("guidance_type")),
                next_expected_action=_string_value(
                    model_payload.get("next_expected_action"),
                    prompt_bundle.expected_child_action,
                ),
                error=None,
            )
        except (SceneLoadError, ArkRequestError, ValueError, json.JSONDecodeError) as exc:
            return self._error_result(task_id, expected_child_action, str(exc), prompt_version)

    def _error_result(
        self,
        task_id: str,
        expected_child_action: str,
        message: str,
        prompt_version: str = PROMPT_VERSION,
    ) -> DialogSmokeResult:
        return DialogSmokeResult(
            scene_id=self.scene_id,
            prompt_version=prompt_version,
            task_id=task_id,
            reply_text="",
            guidance_type="runtime_error",
            next_expected_action=expected_child_action,
            error=message,
        )
