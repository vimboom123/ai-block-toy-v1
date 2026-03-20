from __future__ import annotations

import argparse
import json
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from urllib.parse import urlparse

from .core import (
    ENDPOINT_VERSION,
    AssistantTurnResult,
    RequestValidationError,
    SessionConflictError,
    SessionNotFoundError,
    iso_now,
)
from .language_profile import SessionLanguageProfileService
from .phase5_bridge import (
    DEFAULT_SCENE_FILE,
    DEFAULT_SESSION_TASK_IDS,
    build_default_runtime_service,
)
from .state_machine import STATE_MACHINE_VERSION, TurnInterpretation

BASE_API_PATH = "/api/session-runtime"
SESSIONS_PATH = f"{BASE_API_PATH}/sessions"
SESSION_SUMMARIES_PATH = f"{BASE_API_PATH}/session-summaries"
HEALTH_PATH = "/api/health"
LANGUAGE_PROFILE_PATH = f"{BASE_API_PATH}/language-profile"
DEFAULT_UI_DIR = Path(__file__).resolve().parents[3] / "apps" / "parent-view"
DEFAULT_STORE_FILE = Path(__file__).resolve().parents[1] / "state" / "session-runtime-store.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the Phase 6 session runtime JSON API."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4183,
        help="Bind port. Defaults to 4183.",
    )
    parser.add_argument(
        "--scene-file",
        default=DEFAULT_SCENE_FILE,
        help="Scene pack path relative to runtimes/dialog.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Repeat to override the default task ids for new sessions.",
    )
    parser.add_argument(
        "--ui-dir",
        default=str(DEFAULT_UI_DIR),
        help="Static UI directory to serve from /. Defaults to ../apps/parent-view.",
    )
    parser.add_argument(
        "--store-file",
        default=str(DEFAULT_STORE_FILE),
        help="JSON file used for minimal session persistence.",
    )
    parser.add_argument(
        "--memory-only",
        action="store_true",
        help="Disable JSON persistence and keep sessions in process memory only.",
    )
    return parser.parse_args()


class SessionRuntimeApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[SimpleHTTPRequestHandler],
        scene_file: str,
        default_task_ids: tuple[str, ...],
        ui_dir: Path,
        store_file: Path | None = None,
        service: Any | None = None,
    ):
        super().__init__(server_address, request_handler_class)
        self.scene_file = scene_file
        self.default_task_ids = default_task_ids
        self.ui_dir = ui_dir
        self.store_file = store_file
        self.service = service or build_default_runtime_service(
            scene_file=scene_file,
            task_ids=default_task_ids,
            store_file=store_file,
        )
        self.language_profile_service = SessionLanguageProfileService()


class SessionRuntimeRequestHandler(SimpleHTTPRequestHandler):
    server: SessionRuntimeApiServer

    def _reload_service_from_store(self) -> None:
        service = self.server.service
        persistence = getattr(service, "persistence", None)
        if persistence is None:
            return
        load_sessions = getattr(persistence, "load_sessions", None)
        if load_sessions is None:
            return
        service_lock = getattr(service, "_lock", None)
        if service_lock is None:
            return
        with service_lock:
            service._sessions = dict(load_sessions())

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if (
            parsed.path == HEALTH_PATH
            or parsed.path == SESSION_SUMMARIES_PATH
            or parsed.path == LANGUAGE_PROFILE_PATH
            or parsed.path.startswith(f"{SESSIONS_PATH}/")
        ):
            self._reload_service_from_store()
        if parsed.path == HEALTH_PATH:
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "api_version": ENDPOINT_VERSION,
                    "state_machine_version": STATE_MACHINE_VERSION,
                    "generated_at": iso_now(),
                    "api_root": BASE_API_PATH,
                    "scene_file": self.server.scene_file,
                    "default_task_ids": list(self.server.default_task_ids),
                    "session_count": self.server.service.session_count(),
                    "active_session_count": self.server.service.active_session_count(),
                    "latest_session_id": self.server.service.latest_session_id(),
                    "latest_active_session_id": self.server.service.latest_active_session_id(),
                    "ui_dir": str(self.server.ui_dir),
                    "store_file": str(self.server.store_file) if self.server.store_file else None,
                    "memory_only": self.server.store_file is None,
                },
            )
            return

        if parsed.path == SESSION_SUMMARIES_PATH:
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.server.service.list_recent_session_summaries(limit=5),
                    "generated_at": iso_now(),
                },
            )
            return

        if parsed.path == LANGUAGE_PROFILE_PATH:
            session_summaries = self.server.service.list_recent_session_summaries(limit=10)
            session_snapshots: list[dict[str, Any]] = []
            for summary in session_summaries:
                session_id = str(summary.get("session_id") or "").strip()
                if not session_id:
                    continue
                try:
                    session_snapshots.append(self.server.service.get_session_snapshot(session_id))
                except SessionNotFoundError:
                    continue
            snapshot = self.server.language_profile_service.build_snapshot(session_snapshots)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "profile": snapshot.to_dict(),
                    "generated_at": iso_now(),
                },
            )
            return

        if parsed.path.startswith(f"{SESSIONS_PATH}/"):
            session_id = parsed.path.removeprefix(f"{SESSIONS_PATH}/").strip("/")
            self._handle_session_get(session_id)
            return

        self.path = "/index.html" if parsed.path in {"", "/"} else parsed.path
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == SESSIONS_PATH:
            self._handle_session_create()
            return

        if parsed.path.startswith(f"{SESSIONS_PATH}/") and parsed.path.endswith("/resume"):
            session_id = parsed.path.removeprefix(f"{SESSIONS_PATH}/")
            session_id = session_id[: -len("/resume")].strip("/")
            self._handle_session_resume(session_id)
            return

        if parsed.path.startswith(f"{SESSIONS_PATH}/") and parsed.path.endswith("/end"):
            session_id = parsed.path.removeprefix(f"{SESSIONS_PATH}/")
            session_id = session_id[: -len("/end")].strip("/")
            self._handle_session_end(session_id)
            return

        if parsed.path.startswith(f"{SESSIONS_PATH}/") and parsed.path.endswith("/turns"):
            session_id = parsed.path.removeprefix(f"{SESSIONS_PATH}/")
            session_id = session_id[: -len("/turns")].strip("/")
            self._handle_turn_submit(session_id)
            return

        self._send_error_payload(
            HTTPStatus.NOT_FOUND,
            "not_found",
            f"Unknown path: {parsed.path}",
        )

    def _handle_session_create(self) -> None:
        try:
            payload = self._read_json_body(required=False)
            task_ids = payload.get("task_ids")
            if task_ids is not None and not isinstance(task_ids, list):
                raise RequestValidationError("task_ids must be an array when provided")

            snapshot = self.server.service.create_session(task_ids=task_ids)
            self._send_json(HTTPStatus.CREATED, snapshot)
        except RequestValidationError as exc:
            self._send_error_payload(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
        except Exception as exc:
            self._send_error_payload(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def _handle_session_get(self, session_id: str) -> None:
        try:
            snapshot = self.server.service.get_session_snapshot(session_id)
            self._send_json(HTTPStatus.OK, snapshot)
        except SessionNotFoundError as exc:
            self._send_error_payload(HTTPStatus.NOT_FOUND, "session_not_found", str(exc))
        except Exception as exc:
            self._send_error_payload(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def _handle_session_resume(self, session_id: str) -> None:
        try:
            snapshot = self.server.service.resume_session(session_id)
            self._send_json(HTTPStatus.OK, snapshot)
        except RequestValidationError as exc:
            self._send_error_payload(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
        except SessionNotFoundError as exc:
            self._send_error_payload(HTTPStatus.NOT_FOUND, "session_not_found", str(exc))
        except SessionConflictError as exc:
            self._send_error_payload(HTTPStatus.CONFLICT, "session_conflict", str(exc))
        except Exception as exc:
            self._send_error_payload(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def _handle_session_end(self, session_id: str) -> None:
        try:
            payload = self._read_json_body(required=False)
            end_reason = payload.get("end_reason") if isinstance(payload, dict) else None
            normalized_reason = (
                str(end_reason).strip()
                if isinstance(end_reason, str) and end_reason.strip()
                else "parent_interrupted"
            )
            snapshot = self.server.service.terminate_session(
                session_id,
                end_reason=normalized_reason,
            )
            self._send_json(HTTPStatus.OK, snapshot)
        except RequestValidationError as exc:
            self._send_error_payload(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
        except SessionNotFoundError as exc:
            self._send_error_payload(HTTPStatus.NOT_FOUND, "session_not_found", str(exc))
        except SessionConflictError as exc:
            self._send_error_payload(HTTPStatus.CONFLICT, "session_conflict", str(exc))
        except Exception as exc:
            self._send_error_payload(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    def _handle_turn_submit(self, session_id: str) -> None:
        try:
            payload = self._read_json_body(required=True)
            child_input_text = payload.get("child_input_text", "")
            task_signal = payload.get("task_signal", "auto")
            assistant_reply_override = self._assistant_reply_override_from_payload(payload)
            interpretation = self._interpretation_from_payload(payload)
            snapshot = self.server.service.submit_turn(
                session_id=session_id,
                child_input_text=child_input_text,
                task_signal=task_signal,
                assistant_reply_override=assistant_reply_override,
                interpretation=interpretation,
            )
            self._send_json(HTTPStatus.OK, snapshot)
        except RequestValidationError as exc:
            self._send_error_payload(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
        except SessionNotFoundError as exc:
            self._send_error_payload(HTTPStatus.NOT_FOUND, "session_not_found", str(exc))
        except SessionConflictError as exc:
            self._send_error_payload(HTTPStatus.CONFLICT, "session_conflict", str(exc))
        except Exception as exc:
            self._send_error_payload(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))

    @staticmethod
    def _assistant_reply_override_from_payload(payload: dict[str, Any]) -> AssistantTurnResult | None:
        reply_text = payload.get("assistant_reply_text")
        if not isinstance(reply_text, str) or not reply_text.strip():
            return None
        guidance_type = payload.get("assistant_guidance_type")
        prompt_version = payload.get("assistant_prompt_version")
        next_expected_action = payload.get("assistant_next_expected_action")
        return AssistantTurnResult(
            prompt_version=(
                str(prompt_version).strip()
                if isinstance(prompt_version, str) and prompt_version.strip()
                else "phase7_voice_runtime_v1"
            ),
            reply_text=reply_text.strip(),
            guidance_type=(
                str(guidance_type).strip()
                if isinstance(guidance_type, str) and guidance_type.strip()
                else "action"
            ),
            next_expected_action=(
                str(next_expected_action).strip()
                if isinstance(next_expected_action, str) and next_expected_action.strip()
                else ""
            ),
            error=None,
        )

    @staticmethod
    def _interpretation_from_payload(payload: dict[str, Any]) -> TurnInterpretation | None:
        interpretation_payload: dict[str, Any] = {}
        if isinstance(payload.get("signal_reason"), str):
            interpretation_payload["reason"] = payload.get("signal_reason")
        if isinstance(payload.get("signal_confidence"), (int, float)):
            interpretation_payload["confidence"] = payload.get("signal_confidence")
        if isinstance(payload.get("engagement_state"), str):
            interpretation_payload["engagement_state"] = payload.get("engagement_state")
        if "safety_triggered" in payload:
            interpretation_payload["safety_triggered"] = bool(payload.get("safety_triggered"))
        if isinstance(payload.get("safety_reason"), str):
            interpretation_payload["safety_reason"] = payload.get("safety_reason")
        if "partial_credit" in payload:
            interpretation_payload["partial_credit"] = bool(payload.get("partial_credit"))
        if isinstance(payload.get("matched_completion_points"), list):
            interpretation_payload["matched_completion_points"] = payload.get("matched_completion_points")
        if isinstance(payload.get("missing_completion_points"), list):
            interpretation_payload["missing_completion_points"] = payload.get("missing_completion_points")
        if isinstance(payload.get("interaction_mode"), str):
            interpretation_payload["interaction_mode"] = payload.get("interaction_mode")
        if isinstance(payload.get("emotion_tone"), str):
            interpretation_payload["emotion_tone"] = payload.get("emotion_tone")
        if isinstance(payload.get("redirect_strength"), str):
            interpretation_payload["redirect_strength"] = payload.get("redirect_strength")
        if isinstance(payload.get("followup_question"), str):
            interpretation_payload["followup_question"] = payload.get("followup_question")
        return TurnInterpretation.from_payload(interpretation_payload)

    def _read_json_body(self, required: bool) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            if required:
                raise RequestValidationError("JSON body is required")
            return {}

        raw_body = self.rfile.read(content_length).decode("utf-8")
        if not raw_body.strip():
            if required:
                raise RequestValidationError("JSON body is required")
            return {}

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RequestValidationError(f"Invalid JSON body: {exc}") from exc

        if not isinstance(payload, dict):
            raise RequestValidationError("JSON body must decode to an object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error_payload(
        self,
        status: HTTPStatus,
        error_code: str,
        message: str,
    ) -> None:
        self._send_json(
            status,
            {
                "ok": False,
                "api_version": ENDPOINT_VERSION,
                "error_code": error_code,
                "message": message,
                "generated_at": iso_now(),
            },
        )


def main() -> int:
    args = parse_args()
    task_ids = tuple(args.task_ids or DEFAULT_SESSION_TASK_IDS)
    ui_dir = Path(args.ui_dir).expanduser().resolve()
    store_file = None if args.memory_only else Path(args.store_file).expanduser().resolve()
    if not ui_dir.is_dir():
        raise SystemExit(f"UI directory not found: {ui_dir}")

    handler = partial(SessionRuntimeRequestHandler, directory=str(ui_dir))
    server = SessionRuntimeApiServer(
        (args.host, args.port),
        handler,
        scene_file=args.scene_file,
        default_task_ids=task_ids,
        ui_dir=ui_dir,
        store_file=store_file,
    )

    print(f"UI root:  http://{args.host}:{args.port}/")
    print(f"API root: http://{args.host}:{args.port}{BASE_API_PATH}")
    print(f"Health:   http://{args.host}:{args.port}{HEALTH_PATH}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
