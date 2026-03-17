from __future__ import annotations

from pathlib import Path

from session_runtime.core import (
    CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER,
    CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION,
    ENDPOINT_VERSION,
    RUNTIME_MODE_STATEFUL_SESSION,
    SESSION_SCOPE_JSON_FILE_STATEFUL,
    SNAPSHOT_KIND_SESSION_STATE,
    AssistantTurnResult,
    SessionRuntimeService,
)
from session_runtime.persistence import JsonSessionStore


class FakeResponder:
    def generate_reply(
        self,
        session,
        current_task,
        child_input_text: str,
        resolved_task_signal: str,
        upcoming_task,
    ) -> AssistantTurnResult:
        next_expected_action = current_task.expected_child_action
        if resolved_task_signal == "task_completed" and upcoming_task is not None:
            next_expected_action = upcoming_task.expected_child_action
        elif resolved_task_signal == "end_session":
            next_expected_action = "结束本轮"

        return AssistantTurnResult(
            prompt_version="fake_v1",
            reply_text=f"reply for {current_task.task_id}",
            guidance_type="action",
            next_expected_action=next_expected_action,
            error=None,
        )


TASK_BLUEPRINTS = [
    {
        "task_id": "fs_002",
        "name": "接警判断",
        "goal": "确认求助来自哪里",
        "expected_child_action": "说出内部火警或外部场景火警",
    },
    {
        "task_id": "fs_003",
        "name": "集合出动",
        "goal": "决定谁先出发",
        "expected_child_action": "完成角色和载具选择",
    },
]


def build_service(store_file: Path | None = None) -> SessionRuntimeService:
    return SessionRuntimeService(
        scene_id="classic_world_fire_station",
        task_blueprints=TASK_BLUEPRINTS,
        responder=FakeResponder(),
        auto_complete_keywords={
            "fs_002": ("内部", "外部"),
            "fs_003": ("消防车", "直升机"),
        },
        persistence=JsonSessionStore(store_file) if store_file else None,
    )


def test_create_session_bootstraps_first_task() -> None:
    service = build_service()

    snapshot = service.create_session()

    assert snapshot["ok"] is True
    assert snapshot["api_version"] == ENDPOINT_VERSION
    assert snapshot["snapshot_kind"] == SNAPSHOT_KIND_SESSION_STATE
    assert snapshot["session"]["lifecycle_state"] == "bootstrapped"
    assert snapshot["session"]["status"] == "active"
    assert snapshot["session"]["current_task_id"] == "fs_002"
    assert snapshot["session"]["current_task_index_semantics"] == CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER
    assert snapshot["session"]["current_turn_index_semantics"] == CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION
    assert snapshot["session"]["runtime_mode"] == RUNTIME_MODE_STATEFUL_SESSION
    assert snapshot["current_task"]["status"] == "active"
    assert snapshot["current_turn"] is None
    assert snapshot["tasks"][1]["status"] == "pending"
    assert snapshot["meta"]["runtime_mode"] == RUNTIME_MODE_STATEFUL_SESSION
    assert snapshot["meta"]["supports"]["submit_turn"] is True


def test_submit_turn_can_stay_on_current_task() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="我还在看",
        task_signal="keep_trying",
    )

    assert snapshot["session"]["lifecycle_state"] == "in_progress"
    assert snapshot["session"]["turn_count"] == 1
    assert snapshot["session"]["current_task_id"] == "fs_002"
    assert snapshot["current_turn"]["task_id"] == "fs_002"
    assert snapshot["current_turn"]["task_index"] == 0
    assert snapshot["current_turn"]["task_name"] == "接警判断"
    assert snapshot["current_turn"]["resolved_task_signal"] == "keep_trying"
    assert snapshot["current_turn"]["task_progress"] == "stayed_on_task"
    assert snapshot["current_turn"]["signal"]["requested"] == "keep_trying"
    assert snapshot["current_turn"]["signal"]["resolved"] == "keep_trying"
    assert snapshot["current_turn"]["outcome"]["session_status_after"] == "active"
    assert snapshot["session"]["current_turn_id"] == snapshot["current_turn"]["turn_id"]
    assert snapshot["current_task"]["turn_count"] == 1
    assert snapshot["current_task"]["last_turn_id"] == snapshot["current_turn"]["turn_id"]
    assert snapshot["current_task"]["last_turn_index"] == snapshot["current_turn"]["turn_index"]
    assert snapshot["viewer_context"]["latest_turn_id"] == snapshot["current_turn"]["turn_id"]
    assert snapshot["viewer_context"]["latest_turn_task_id"] == "fs_002"


def test_create_session_can_override_task_ids() -> None:
    service = build_service()

    snapshot = service.create_session(task_ids=["fs_003"])

    assert snapshot["session"]["task_count"] == 1
    assert snapshot["session"]["current_task_id"] == "fs_003"
    assert [task["task_id"] for task in snapshot["tasks"]] == ["fs_003"]


def test_submit_turn_can_advance_and_end_after_last_task() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    first_snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="外部着火了",
        task_signal="auto",
    )

    assert first_snapshot["session"]["current_task_id"] == "fs_003"
    assert first_snapshot["tasks"][0]["status"] == "completed"
    assert first_snapshot["tasks"][1]["status"] == "active"
    assert first_snapshot["current_turn"]["task_progress"] == "advanced_to_next_task"

    second_snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="消防车先去",
        task_signal="auto",
    )

    assert second_snapshot["session"]["status"] == "ended"
    assert second_snapshot["session"]["lifecycle_state"] == "ended"
    assert second_snapshot["session"]["current_task_id"] is None
    assert second_snapshot["session"]["completed_task_count"] == 2
    assert second_snapshot["session"]["ended_reason"] == "completed_all_tasks"
    assert second_snapshot["current_turn"]["task_progress"] == "ended_session"
    assert second_snapshot["current_turn"]["outcome"]["session_lifecycle_state_after"] == "ended"


def test_submit_turn_can_end_session_explicitly() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="先停一下",
        task_signal="end_session",
    )

    assert snapshot["session"]["status"] == "ended"
    assert snapshot["session"]["lifecycle_state"] == "ended"
    assert snapshot["session"]["ended_reason"] == "explicit_end_session"
    assert snapshot["current_turn"]["signal"]["resolved"] == "end_session"
    assert snapshot["current_turn"]["outcome"]["session_status_after"] == "ended"


def test_json_persistence_survives_service_restart(tmp_path: Path) -> None:
    store_file = tmp_path / "session-store.json"
    first_service = build_service(store_file=store_file)

    first_snapshot = first_service.create_session()
    session_id = first_snapshot["session"]["session_id"]

    first_service.submit_turn(
        session_id=session_id,
        child_input_text="外部着火了",
        task_signal="auto",
    )

    reloaded_service = build_service(store_file=store_file)
    reloaded_snapshot = reloaded_service.get_session_snapshot(session_id)

    assert reloaded_snapshot["session"]["session_id"] == session_id
    assert reloaded_snapshot["session"]["is_persisted_session"] is True
    assert reloaded_snapshot["session"]["session_scope"] == SESSION_SCOPE_JSON_FILE_STATEFUL
    assert reloaded_snapshot["session"]["turn_count"] == 1
    assert reloaded_snapshot["session"]["current_task_id"] == "fs_003"
    assert reloaded_snapshot["meta"]["supports"]["persisted_session"] is True
    assert "最小 JSON 持久化" in reloaded_snapshot["meta"]["disclaimer"]
