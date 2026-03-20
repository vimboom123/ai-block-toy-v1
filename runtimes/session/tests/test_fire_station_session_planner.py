from __future__ import annotations

from session_runtime.core import AssistantTurnResult, SessionRuntimeService
from session_runtime.fire_station_session_planner import (
    DEFAULT_STAGE_ORDER,
    FireStationSessionPlanner,
    GeneratedSessionPlan,
)


class FakeResponder:
    def generate_reply(
        self,
        session,
        current_task,
        child_input_text: str,
        resolved_task_signal: str,
        upcoming_task,
    ) -> AssistantTurnResult:
        return AssistantTurnResult(
            prompt_version="fake_v1",
            reply_text=current_task.goal,
            guidance_type="action",
            next_expected_action=current_task.expected_child_action,
            error=None,
        )


def _default_blueprints() -> list[dict[str, object]]:
    return [
        {
            "task_id": task_id,
            "name": stage_name,
            "goal": f"{stage_name}目标",
            "expected_child_action": f"{stage_name}动作",
        }
        for task_id, stage_name in DEFAULT_STAGE_ORDER
    ]


def test_fallback_planner_returns_story_and_stage_assets() -> None:
    planner = FireStationSessionPlanner(provider_mode="template")

    plan = planner.build_plan(
        session_id="ses_phase6_testseed",
        scene_id="classic_world_fire_station",
        requested_task_ids=None,
        default_task_blueprints=_default_blueprints(),
    )

    assert plan.generation_source == "deterministic_fallback"
    assert plan.story_title
    assert plan.story_context
    assert [task["task_id"] for task in plan.task_blueprints] == [
        task_id for task_id, _ in DEFAULT_STAGE_ORDER
    ]
    first_task = plan.task_blueprints[0]
    assert first_task["story_beat"]
    assert first_task["selected_entities"]
    assert first_task["completion_points"]
    assert first_task["completion_points"][0]["keywords"]
    final_task = plan.task_blueprints[-1]
    assert final_task["task_id"] == "fs_006"
    assert final_task["assistant_led_summary"] is True
    assert "听系统" in final_task["expected_child_action"]


def test_fallback_planner_can_generate_non_fire_station_story() -> None:
    planner = FireStationSessionPlanner(provider_mode="template")

    plan = planner.build_plan(
        session_id="e",
        scene_id="classic_world_fire_station",
        requested_task_ids=None,
        default_task_blueprints=_default_blueprints(),
    )

    assert "医药箱接应任务" in plan.story_title
    assert "医药箱" in plan.task_blueprints[2]["selected_entities"] or "医药箱" in plan.task_blueprints[4]["goal"]
    assert "医药箱" in plan.task_blueprints[4]["goal"]
    assert "灭火" not in plan.task_blueprints[4]["expected_child_action"]


class StaticPlanner:
    def build_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        requested_task_ids,
        default_task_blueprints,
    ) -> GeneratedSessionPlan:
        del session_id, scene_id, requested_task_ids, default_task_blueprints
        return GeneratedSessionPlan(
            story_title="铃声后的出动任务",
            story_context="先听铃声，再判断位置，再动手出动消防车。",
            generation_source="test_planner",
            task_blueprints=(
                {
                    "task_id": "fs_001",
                    "name": "先找到火警线索",
                    "goal": "先找到最早出现的警报和火点。",
                    "expected_child_action": "指出是谁先提醒大家、哪里先出事。",
                    "story_beat": "铃铛响起后，孩子先要找到是谁在提醒、哪里最该先看。",
                    "selected_entities": ["铃铛", "小火", "消防指挥中心"],
                    "selected_background_elements": ["墙上的挂钟"],
                    "completion_points": [
                        {"label": "第一条警情线索", "keywords": ["铃铛", "小火", "亮了"]},
                    ],
                },
            ),
        )


class ExplodingPlanner:
    def build_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        requested_task_ids,
        default_task_blueprints,
    ) -> GeneratedSessionPlan:
        del session_id, scene_id, requested_task_ids, default_task_blueprints
        raise RuntimeError("planner exploded")

    def build_fallback_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        selected_task_ids,
        default_task_blueprints,
    ) -> GeneratedSessionPlan:
        del session_id, scene_id, selected_task_ids, default_task_blueprints
        return GeneratedSessionPlan(
            story_title="消防铃声响起",
            story_context="虽然规划器异常，但仍然退回到动态消防站故事计划。",
            generation_source="planner_exception_fallback",
            task_blueprints=(
                {
                    "task_id": "fs_001",
                    "name": "先找到火警线索",
                    "goal": "先找到哪边最早响起来、亮起来或着火。",
                    "expected_child_action": "指出警报和火点。",
                    "story_beat": "警报响起后，先找到铃声、火点和最值得先看的地方。",
                    "selected_entities": ["铃铛", "小火"],
                    "selected_background_elements": ["墙上的挂钟"],
                    "completion_points": [
                        {"label": "第一条警情线索", "keywords": ["铃铛", "小火", "警报"]},
                    ],
                },
            ),
        )


def test_service_create_session_includes_dynamic_story_metadata() -> None:
    service = SessionRuntimeService(
        scene_id="classic_world_fire_station",
        task_blueprints=_default_blueprints()[:1],
        responder=FakeResponder(),
        task_blueprint_planner=StaticPlanner(),
    )

    snapshot = service.create_session()

    assert snapshot["session"]["story_title"] == "铃声后的出动任务"
    assert snapshot["session"]["story_context"] == "先听铃声，再判断位置，再动手出动消防车。"
    assert snapshot["session"]["plan_generation_source"] == "test_planner"
    assert snapshot["current_task"]["story_beat"] == "铃铛响起后，孩子先要找到是谁在提醒、哪里最该先看。"
    assert snapshot["current_task"]["selected_entities"] == ["铃铛", "小火", "消防指挥中心"]
    assert snapshot["current_task"]["selected_background_elements"] == ["墙上的挂钟"]
    assert snapshot["current_task"]["completion_points"][0]["label"] == "第一条警情线索"


def test_service_create_session_uses_planner_fallback_when_build_plan_raises() -> None:
    service = SessionRuntimeService(
        scene_id="classic_world_fire_station",
        task_blueprints=_default_blueprints()[:1],
        responder=FakeResponder(),
        task_blueprint_planner=ExplodingPlanner(),
    )

    snapshot = service.create_session()

    assert snapshot["session"]["story_title"] == "消防铃声响起"
    assert snapshot["session"]["story_context"] == "虽然规划器异常，但仍然退回到动态消防站故事计划。"
    assert snapshot["session"]["plan_generation_source"] == "planner_exception_fallback"
    assert snapshot["current_task"]["story_beat"] == "警报响起后，先找到铃声、火点和最值得先看的地方。"
    assert snapshot["current_task"]["selected_entities"] == ["铃铛", "小火"]
