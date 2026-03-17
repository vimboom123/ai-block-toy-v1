from __future__ import annotations

import json
from functools import partial
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

from session_runtime.core import AssistantTurnResult, SessionRuntimeService
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
