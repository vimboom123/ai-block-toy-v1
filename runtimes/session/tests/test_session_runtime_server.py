from __future__ import annotations

import json
from functools import partial
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from session_runtime.core import AssistantTurnResult, SessionRuntimeService
from session_runtime.phase5_bridge import DEFAULT_SESSION_TASK_IDS
from session_runtime.server import (
    SessionRuntimeApiServer,
    SessionRuntimeRequestHandler,
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
            reply_text=f"reply for {current_task.task_id}",
            guidance_type="action",
            next_expected_action=current_task.expected_child_action,
            error=None,
        )


TASK_BLUEPRINTS = [
    {
        "task_id": "fs_002",
        "name": "接警判断",
        "goal": "确认求助来自哪里",
        "expected_child_action": "说出内部火警或外部场景火警",
    }
]


def build_service() -> SessionRuntimeService:
    return SessionRuntimeService(
        scene_id="classic_world_fire_station",
        task_blueprints=TASK_BLUEPRINTS,
        responder=FakeResponder(),
        auto_complete_keywords={"fs_002": ("内部", "外部")},
    )


def request_json(
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
    raw_body: str | None = None,
) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    body = None
    if raw_body is not None:
        body = raw_body
        headers["Content-Type"] = "application/json"
    elif payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"

    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, data


def run_server(tmp_path: Path) -> tuple[SessionRuntimeApiServer, Thread]:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("ok", encoding="utf-8")

    handler = partial(SessionRuntimeRequestHandler, directory=str(ui_dir))
    server = SessionRuntimeApiServer(
        ("127.0.0.1", 0),
        handler,
        scene_file="test-scene.json",
        default_task_ids=("fs_002",),
        ui_dir=ui_dir,
        service=build_service(),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def shutdown_server(server: SessionRuntimeApiServer, thread: Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_server_returns_not_found_for_unknown_session(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        status, payload = request_json(
            server.server_port,
            "GET",
            "/api/session-runtime/sessions/ses_missing",
        )
    finally:
        shutdown_server(server, thread)

    assert status == 404
    assert payload["error_code"] == "session_not_found"


def test_server_returns_bad_request_for_invalid_json(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        status, payload = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            raw_body="{bad json",
        )
    finally:
        shutdown_server(server, thread)

    assert status == 400
    assert payload["error_code"] == "bad_request"


def test_server_returns_conflict_for_ended_session_turn_submit(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={},
        )
        session_id = created["session"]["session_id"]

        request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/turns",
            payload={"child_input_text": "先停一下", "task_signal": "end_session"},
        )
        status, payload = request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/turns",
            payload={"child_input_text": "再来一次", "task_signal": "keep_trying"},
        )
    finally:
        shutdown_server(server, thread)

    assert status == 409
    assert payload["error_code"] == "session_conflict"


def test_server_returns_not_found_for_unknown_path(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        status, payload = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/nope",
            payload={},
        )
    finally:
        shutdown_server(server, thread)

    assert status == 404
    assert payload["error_code"] == "not_found"


def test_server_submit_turn_accepts_phase7_reply_override(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={},
        )
        session_id = created["session"]["session_id"]
        status, payload = request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/turns",
            payload={
                "child_input_text": "我还在看",
                "task_signal": "keep_trying",
                "assistant_reply_text": "先别急，我们先判断火情是在里面还是外面呀？",
                "assistant_guidance_type": "action",
                "assistant_prompt_version": "phase7_voice_runtime_v1",
                "signal_reason": "孩子还没回答接警地点",
                "signal_confidence": 0.73,
                "engagement_state": "engaged",
                "interaction_mode": "warm_redirect",
            },
        )
    finally:
        shutdown_server(server, thread)

    assert status == 200
    assert payload["current_turn"]["assistant_reply"]["prompt_version"] == "phase7_voice_runtime_v1"
    assert payload["current_turn"]["assistant_reply"]["reply_text"] == "先别急，我们先判断火情是在里面还是外面呀？"
    assert payload["current_turn"]["assistant_reply"]["guidance_type"] == "action"
    assert payload["current_turn"]["interpretation"]["reason"] == "孩子还没回答接警地点"
    assert payload["current_turn"]["interpretation"]["interaction_mode"] == "warm_redirect"


def test_server_health_reports_full_default_task_ids(tmp_path: Path) -> None:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("ok", encoding="utf-8")

    handler = partial(SessionRuntimeRequestHandler, directory=str(ui_dir))
    server = SessionRuntimeApiServer(
        ("127.0.0.1", 0),
        handler,
        scene_file="test-scene.json",
        default_task_ids=DEFAULT_SESSION_TASK_IDS,
        ui_dir=ui_dir,
        service=build_service(),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={"task_ids": ["fs_002"]},
        )
        status, payload = request_json(
            server.server_port,
            "GET",
            "/api/health",
        )
    finally:
        shutdown_server(server, thread)

    assert status == 200
    assert payload["state_machine_version"] == "ai_block_toy_state_machine_v1"
    assert payload["default_task_ids"] == list(DEFAULT_SESSION_TASK_IDS)
    assert payload["session_count"] == 1
    assert payload["active_session_count"] == 1
    assert payload["latest_session_id"] == created["session"]["session_id"]
    assert payload["latest_active_session_id"] == created["session"]["session_id"]


def test_server_can_resume_paused_session(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={},
        )
        session_id = created["session"]["session_id"]
        for _ in range(5):
            request_json(
                server.server_port,
                "POST",
                f"/api/session-runtime/sessions/{session_id}/turns",
                payload={"child_input_text": "我还在看", "task_signal": "keep_trying"},
            )

        status, payload = request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/resume",
            payload={},
        )
    finally:
        shutdown_server(server, thread)

    assert status == 200
    assert payload["session"]["status"] == "active"
    assert payload["session"]["current_state"] == "await_answer"


def test_server_can_end_paused_session_with_parent_reason(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={},
        )
        session_id = created["session"]["session_id"]
        for _ in range(5):
            request_json(
                server.server_port,
                "POST",
                f"/api/session-runtime/sessions/{session_id}/turns",
                payload={"child_input_text": "我还在看", "task_signal": "keep_trying"},
            )

        status, payload = request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/end",
            payload={"end_reason": "parent_interrupted"},
        )
    finally:
        shutdown_server(server, thread)

    assert status == 200
    assert payload["session"]["status"] == "ended"
    assert payload["session"]["ended_reason"] == "parent_interrupted"


def test_server_submit_turn_can_trigger_safety_stop(tmp_path: Path) -> None:
    server, thread = run_server(tmp_path)
    try:
        _, created = request_json(
            server.server_port,
            "POST",
            "/api/session-runtime/sessions",
            payload={},
        )
        session_id = created["session"]["session_id"]
        status, payload = request_json(
            server.server_port,
            "POST",
            f"/api/session-runtime/sessions/{session_id}/turns",
            payload={
                "child_input_text": "这个太危险了",
                "task_signal": "keep_trying",
                "safety_triggered": True,
                "safety_reason": "检测到危险动作，需要立即停止",
            },
        )
    finally:
        shutdown_server(server, thread)

    assert status == 200
    assert payload["session"]["status"] == "aborted"
    assert payload["session"]["ended_reason"] == "safety_stop"
    assert payload["current_turn"]["interpretation"]["safety_triggered"] is True
    assert payload["current_turn"]["state_machine"]["state_path"][-3:] == [
        "safety_hold",
        "abort_cleanup",
        "ended",
    ]
