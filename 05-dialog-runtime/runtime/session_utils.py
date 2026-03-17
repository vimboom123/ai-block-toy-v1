from __future__ import annotations

from datetime import datetime, timezone

CURRENT_TASK_INDEX_SEMANTICS_LATEST_IN_SNAPSHOT = "latest_in_snapshot"
SESSION_SCOPE_REQUEST_SCOPED_SNAPSHOT = "request_scoped_snapshot"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
