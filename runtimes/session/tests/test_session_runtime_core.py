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

ASSISTANT_SUMMARY_TASK_BLUEPRINTS = [
    {
        "task_id": "fs_005",
        "name": "救援执行",
        "goal": "把消防车和消防员摆到位完成救援",
        "expected_child_action": "动手把消防车和消防员摆过去",
    },
    {
        "task_id": "fs_006",
        "name": "系统回站总结",
        "goal": "由系统自动总结这次消防任务",
        "expected_child_action": "听系统把这次消防故事收尾讲完",
        "assistant_led_summary": True,
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


def build_assistant_summary_service() -> SessionRuntimeService:
    return SessionRuntimeService(
        scene_id="classic_world_fire_station",
        task_blueprints=ASSISTANT_SUMMARY_TASK_BLUEPRINTS,
        responder=FakeResponder(),
        auto_complete_keywords={
            "fs_005": ("消防车", "消防员", "摆过去", "救援"),
        },
    )


def test_create_session_bootstraps_first_task() -> None:
    service = build_service()

    snapshot = service.create_session()

    assert snapshot["ok"] is True
    assert snapshot["api_version"] == ENDPOINT_VERSION
    assert snapshot["snapshot_kind"] == SNAPSHOT_KIND_SESSION_STATE
    assert snapshot["session"]["lifecycle_state"] == "bootstrapped"
    assert snapshot["session"]["status"] == "active"
    assert snapshot["session"]["state_machine_version"] == "ai_block_toy_state_machine_v1"
    assert snapshot["session"]["current_state"] == "warming_up"
    assert snapshot["session"]["public_stage"] == "warming_up"
    assert snapshot["session"]["public_stage_text"] == "正在热身进入状态"
    assert snapshot["session"]["bootstrap_state_path"] == ["session_bootstrap", "warming_up"]
    assert snapshot["session"]["current_task_id"] == "fs_002"
    assert snapshot["session"]["current_task_index_semantics"] == CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER
    assert snapshot["session"]["current_turn_index_semantics"] == CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION
    assert snapshot["session"]["runtime_mode"] == RUNTIME_MODE_STATEFUL_SESSION
    assert snapshot["current_task"]["status"] == "active"
    assert snapshot["current_turn"] is None
    assert snapshot["tasks"][1]["status"] == "pending"
    assert snapshot["meta"]["runtime_mode"] == RUNTIME_MODE_STATEFUL_SESSION
    assert snapshot["meta"]["supports"]["submit_turn"] is True
    assert snapshot["meta"]["supports"]["session_live_view"] is True
    assert snapshot["session_live_view"]["header"]["public_stage"] == "warming_up"
    assert snapshot["home_snapshot_view"]["active_session"]["public_stage"] == "warming_up"


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
    assert snapshot["current_turn"]["state_machine"]["state_after"] == "give_hint"
    assert snapshot["current_turn"]["state_machine"]["public_stage_after"] == "receiving_hint"
    assert snapshot["session"]["public_stage"] == "receiving_hint"
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
    assert second_snapshot["session"]["ended_reason"] == "completed"
    assert second_snapshot["session"]["public_stage"] == "ended"
    assert second_snapshot["current_turn"]["state_machine"]["state_path"][-2:] == ["cooling_down", "ended"]
    assert second_snapshot["current_turn"]["task_progress"] == "ended_session"
    assert second_snapshot["current_turn"]["outcome"]["session_lifecycle_state_after"] == "ended"


def test_submit_turn_can_auto_finish_assistant_led_summary_stage() -> None:
    service = build_assistant_summary_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="消防车和消防员都摆过去了",
        task_signal="auto",
    )

    assert snapshot["session"]["status"] == "ended"
    assert snapshot["session"]["ended_reason"] == "completed"
    assert snapshot["session"]["current_task_id"] is None
    assert snapshot["session"]["completed_task_count"] == 2
    assert snapshot["tasks"][1]["assistant_led_summary"] is True
    assert snapshot["tasks"][1]["status"] == "completed"
    assert snapshot["tasks"][1]["result_code"] == "assistant_summary"
    assert snapshot["current_turn"]["assistant_reply"]["next_expected_action"] == "结束本轮"
    assert snapshot["current_turn"]["task_progress"] == "ended_session"


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
    assert snapshot["session"]["ended_reason"] == "child_quit"
    assert snapshot["session"]["public_stage"] == "ended"
    assert snapshot["current_turn"]["signal"]["resolved"] == "end_session"
    assert snapshot["current_turn"]["outcome"]["session_status_after"] == "ended"


def test_submit_turn_can_use_assistant_reply_override() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="我还在看",
        task_signal="keep_trying",
        assistant_reply_override=AssistantTurnResult(
            prompt_version="phase7_voice_runtime_v1",
            reply_text="我们先把这一步看清楚，再说说火情是在里面还是外面呀？",
            guidance_type="action",
            next_expected_action="说出内部火警或外部场景火警",
            error=None,
        ),
    )

    assert snapshot["current_turn"]["assistant_reply"]["prompt_version"] == "phase7_voice_runtime_v1"
    assert snapshot["current_turn"]["assistant_reply"]["reply_text"] == "我们先把这一步看清楚，再说说火情是在里面还是外面呀？"
    assert snapshot["current_turn"]["assistant_reply"]["guidance_type"] == "action"


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
    assert "live 状态机字段" in reloaded_snapshot["meta"]["disclaimer"]


def test_keep_trying_turn_carries_state_machine_interpretation() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="我在看消防车",
        task_signal="keep_trying",
        assistant_reply_override=AssistantTurnResult(
            prompt_version="phase7_voice_runtime_v1",
            reply_text="先看看这次火情是在里面还是外面呀？",
            guidance_type="action",
            next_expected_action="说出内部火警或外部场景火警",
            error=None,
        ),
        interpretation=None,
    )

    assert snapshot["current_turn"]["state_machine"]["state_path"][:3] == [
        "warming_up",
        "task_dispatch",
        "await_answer",
    ]
    assert snapshot["session_live_view"]["header"]["public_stage"] == "receiving_hint"


def test_parent_takeover_can_resume_and_finish_task() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    for _ in range(5):
        paused_snapshot = service.submit_turn(
            session_id=session_id,
            child_input_text="我还在看",
            task_signal="keep_trying",
        )

    assert paused_snapshot["session"]["status"] == "paused"
    assert paused_snapshot["current_turn"]["state_machine"]["state_after"] == "parent_interrupt_hold"

    resumed_snapshot = service.resume_session(session_id)

    assert resumed_snapshot["session"]["status"] == "active"
    assert resumed_snapshot["session"]["current_state"] == "await_answer"
    assert resumed_snapshot["session"]["public_stage"] == "doing_task"

    finished_snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="外部着火了",
        task_signal="auto",
    )

    assert finished_snapshot["session"]["current_task_id"] == "fs_003"
    assert finished_snapshot["session"]["status"] == "active"


def test_frustration_markers_escalate_to_parent_takeover_instead_of_off_topic() -> None:
    from session_runtime.state_machine import TurnInterpretation

    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    for _ in range(4):
        paused_snapshot = service.submit_turn(
            session_id=session_id,
            child_input_text="不知道。",
            task_signal="keep_trying",
            interpretation=TurnInterpretation(
                reason="孩子明确表示不知道，处于当前任务卡点",
                confidence=0.95,
                engagement_state="engaged",
                interaction_mode="warm_redirect",
                emotion_tone="warm",
                redirect_strength="soft",
            ),
        )

    assert paused_snapshot["session"]["status"] == "paused"
    assert paused_snapshot["session"]["current_state"] == "parent_interrupt_hold"
    assert paused_snapshot["session"]["help_level_current"] == "parent_takeover"
    assert paused_snapshot["current_task"]["attempt_count"] == 4
    assert paused_snapshot["session"]["off_topic_count"] == 0


def test_parent_can_end_paused_session() -> None:
    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    for _ in range(5):
        paused_snapshot = service.submit_turn(
            session_id=session_id,
            child_input_text="我还在看",
            task_signal="keep_trying",
        )

    assert paused_snapshot["session"]["status"] == "paused"

    ended_snapshot = service.terminate_session(
        session_id,
        end_reason="parent_interrupted",
    )

    assert ended_snapshot["session"]["status"] == "ended"
    assert ended_snapshot["session"]["ended_reason"] == "parent_interrupted"
    assert ended_snapshot["session"]["current_state"] == "ended"


def test_submit_turn_can_trigger_safety_stop_from_interpretation() -> None:
    from session_runtime.state_machine import TurnInterpretation

    service = build_service()
    session_id = service.create_session()["session"]["session_id"]

    snapshot = service.submit_turn(
        session_id=session_id,
        child_input_text="这个太危险了",
        task_signal="keep_trying",
        interpretation=TurnInterpretation(
            reason="孩子提到了危险动作",
            safety_triggered=True,
            safety_reason="检测到危险动作，需要立即停止",
        ),
    )

    assert snapshot["session"]["status"] == "aborted"
    assert snapshot["session"]["ended_reason"] == "safety_stop"
    assert snapshot["session"]["current_state"] == "ended"
    assert snapshot["session"]["public_stage"] == "ended"
    assert snapshot["current_task"] is None
    assert snapshot["current_turn"]["state_machine"]["state_path"][-3:] == [
        "safety_hold",
        "abort_cleanup",
        "ended",
    ]
    assert snapshot["current_turn"]["interpretation"]["safety_triggered"] is True
    assert snapshot["session_live_view"]["parent_action"]["need_parent_intervention"] is True
