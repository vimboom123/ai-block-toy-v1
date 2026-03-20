from __future__ import annotations

import json
import socket
from typing import Any, Sequence
from urllib import error, request

from .payloads import Phase6TurnPayload


class Phase6BridgeError(RuntimeError):
    """Raised when the optional Phase 6 HTTP bridge fails."""


class Phase6SessionClient:
    def __init__(self, api_base: str, *, timeout_seconds: float = 30.0):
        normalized_base = api_base.rstrip("/")
        if not normalized_base:
            raise ValueError("api_base must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        self.api_base = normalized_base
        self.timeout_seconds = float(timeout_seconds)

    def create_session(self, task_ids: Sequence[str] | None = None) -> dict[str, Any]:
        url = f"{self.api_base}/sessions"
        body_payload: dict[str, Any] = {}
        if task_ids is not None:
            body_payload["task_ids"] = [str(task_id) for task_id in task_ids]
        return self._request_json(url, method="POST", body=body_payload)

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        url = f"{self.api_base}/sessions/{session_id}"
        return self._request_json(url, method="GET")

    def submit_turn(self, session_id: str, payload: Phase6TurnPayload) -> dict:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")

        url = f"{self.api_base}/sessions/{session_id}/turns"
        return self._request_json(url, method="POST", body=payload.to_dict())

    def _request_json(self, url: str, *, method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        encoded_body = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if encoded_body is not None else {}
        req = request.Request(url, data=encoded_body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload_text = response.read().decode(charset)
        except (socket.timeout, TimeoutError) as exc:
            raise Phase6BridgeError(
                f"Phase 6 bridge request timed out: {exc or 'timed out'}"
            ) from exc
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise Phase6BridgeError(f"Phase 6 bridge HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise Phase6BridgeError(f"Phase 6 bridge request failed: {exc.reason}") from exc

        try:
            decoded = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise Phase6BridgeError("Phase 6 bridge returned non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise Phase6BridgeError("Phase 6 bridge returned unexpected payload shape")
        return decoded
