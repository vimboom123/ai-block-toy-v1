from __future__ import annotations

from types import SimpleNamespace

from session_runtime.phase5_bridge import Phase5FireStationTurnResponder


def test_phase5_responder_skips_model_on_final_task_completion() -> None:
    responder = Phase5FireStationTurnResponder.__new__(Phase5FireStationTurnResponder)
    responder.setup_error = None
    responder.scene_pack = {}
    responder.client = object()
    responder.scene_id = "classic_world_fire_station"

    session = SimpleNamespace(tasks=[], turns=[], turn_count=6)
    current_task = SimpleNamespace(
        task_id="fs_006",
        name="回站总结",
        goal="复述刚才发生了什么",
        expected_child_action="用自己的话总结",
    )

    result = responder.generate_reply(
        session=session,
        current_task=current_task,
        child_input_text="刚刚我们先接警再救火。",
        resolved_task_signal="task_completed",
        upcoming_task=None,
    )

    assert result.prompt_version == "phase6_fire_station_session_turn_v1"
    assert result.guidance_type == "confirmation"
    assert result.next_expected_action == "结束本轮"
    assert result.error is None
    assert result.reply_text == "说得真清楚，这次消防任务顺利完成啦。"
