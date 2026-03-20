from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Mapping

from .core import SessionState, iso_now

STORE_VERSION = 1


class JsonSessionStore:
    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path).expanduser().resolve()

    def load_sessions(self) -> dict[str, SessionState]:
        if not self.file_path.is_file():
            return {}

        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Session store must decode to an object.")

        raw_sessions = payload.get("sessions")
        if raw_sessions is None:
            return {}
        if not isinstance(raw_sessions, list):
            raise ValueError("Session store 'sessions' must be an array.")

        sessions: dict[str, SessionState] = {}
        for raw_session in raw_sessions:
            if not isinstance(raw_session, Mapping):
                raise ValueError("Each persisted session must be an object.")
            session = SessionState.from_dict(raw_session)
            sessions[session.session_id] = session
        return sessions

    def save_sessions(self, sessions: Mapping[str, SessionState]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "saved_at": iso_now(),
            "session_count": len(sessions),
            "sessions": [
                session.to_persisted_dict()
                for session in sorted(sessions.values(), key=lambda value: value.started_at)
            ],
        }

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.file_path.parent),
            delete=False,
            suffix=".tmp",
        ) as temp_file:
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)

        temp_path.replace(self.file_path)
