from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.dialog_runtime import (
    DEFAULT_FIRE_STATION_SCENE_FILE,
    DEFAULT_FIRE_STATION_SMOKE_TASK_IDS,
    FireStationDialogRuntime,
)
from runtime.session_utils import iso_now

API_PATH = "/api/fire-station/runtime"
HEALTH_PATH = "/api/health"
RUNTIME_DISCLAIMER = (
    "当前页面已接到真实 Fire Station runtime endpoint；这页仍只是按 task 列表压出的最小摘要，"
    "不是真正的 live view 或正式报告投影。当前也还没有结果缓存，每次请求都会真跑 runtime/LLM。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve apps/parent-view and expose a minimal Fire Station runtime JSON API."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4173,
        help="Bind port. Defaults to 4173.",
    )
    parser.add_argument(
        "--scene-file",
        default=DEFAULT_FIRE_STATION_SCENE_FILE,
        help="Scene pack path relative to the runtimes/dialog folder.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Repeat to override the default task ids served by the runtime API.",
    )
    return parser.parse_args()
def _resolved_task_ids(query_task_ids: list[str] | None, default_task_ids: tuple[str, ...]) -> list[str]:
    if not query_task_ids:
        return list(default_task_ids)

    task_ids: list[str] = []
    seen: set[str] = set()
    for raw_task_id in query_task_ids:
        task_id = raw_task_id.strip()
        if not task_id or task_id in seen:
            continue
        task_ids.append(task_id)
        seen.add(task_id)

    return task_ids or list(default_task_ids)


class RuntimeUiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        # Avoid reverse-DNS lookup in HTTPServer.server_bind(); it can hang on this machine.
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[SimpleHTTPRequestHandler],
        runtime_root_dir: Path,
        ui_dir: Path,
        scene_file: str,
        default_task_ids: tuple[str, ...],
    ):
        super().__init__(server_address, request_handler_class)
        self.runtime_root_dir = runtime_root_dir
        self.ui_dir = ui_dir
        self.scene_file = scene_file
        self.default_task_ids = default_task_ids


class RuntimeUiRequestHandler(SimpleHTTPRequestHandler):
    server: RuntimeUiServer

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == API_PATH:
            self._serve_runtime_payload(parse_qs(parsed.query))
            return

        if parsed.path == HEALTH_PATH:
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "generated_at": iso_now(),
                    "api_path": API_PATH,
                    "ui_dir": str(self.server.ui_dir),
                    "scene_file": self.server.scene_file,
                },
            )
            return

        self.path = "/index.html" if parsed.path in {"", "/"} else parsed.path
        super().do_GET()

    def _serve_runtime_payload(self, query: dict[str, list[str]]) -> None:
        task_ids = _resolved_task_ids(query.get("task_id"), self.server.default_task_ids)
        runtime = FireStationDialogRuntime(self.server.runtime_root_dir, self.server.scene_file)
        snapshot = runtime.run_session_snapshot(task_ids)
        results = snapshot.task_dicts()
        session = snapshot.to_session_dict()

        payload = {
            "session": session,
            "meta": {
                "source_kind": session["source_kind"],
                "source_name": API_PATH,
                "summary_mode": "live_snapshot",
                "disclaimer": RUNTIME_DISCLAIMER,
                "session_scope": session["session_scope"],
                "is_persisted_session": session["is_persisted_session"],
                "current_task_index_semantics": session["current_task_index_semantics"],
                "has_result_cache": False,
                "request_mode": "live_llm_per_request",
                "session_id": session["session_id"],
                "scene_id": session["scene_id"],
                "generated_at": session["generated_at"],
                "updated_at": session["updated_at"],
                "current_task_index": session["current_task_index"],
                "task_count": session["task_count"],
                "has_error": any(result["error"] is not None for result in results),
            },
            "tasks": results,
        }

        self._send_json(HTTPStatus.OK, payload)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    args = parse_args()
    runtime_root_dir = Path(__file__).resolve().parents[1]
    project_root_dir = runtime_root_dir.parent.parent
    ui_dir = project_root_dir / "apps" / "parent-view"

    if not ui_dir.is_dir():
        raise SystemExit(f"apps/parent-view directory not found: {ui_dir}")

    task_ids = _resolved_task_ids(args.task_ids, DEFAULT_FIRE_STATION_SMOKE_TASK_IDS)
    handler = partial(RuntimeUiRequestHandler, directory=str(ui_dir))
    server = RuntimeUiServer(
        (args.host, args.port),
        handler,
        runtime_root_dir=runtime_root_dir,
        ui_dir=ui_dir,
        scene_file=args.scene_file,
        default_task_ids=tuple(task_ids),
    )

    print(f"UI:  http://{args.host}:{args.port}/")
    print(f"API: http://{args.host}:{args.port}{API_PATH}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
