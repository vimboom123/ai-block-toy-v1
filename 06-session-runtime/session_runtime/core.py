from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

ENDPOINT_VERSION = "phase6_session_runtime_v1"
SOURCE_KIND_SESSION_RUNTIME = "session_runtime"
SESSION_SCOPE_PROCESS_MEMORY_STATEFUL = "process_memory_stateful"
SESSION_SCOPE_JSON_FILE_STATEFUL = "json_file_stateful"
SNAPSHOT_KIND_SESSION_STATE = "session_state"
RUNTIME_MODE_STATEFUL_SESSION = "stateful_session_runtime"
CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER = "active_task_pointer"
CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION = "latest_turn_in_session"
TURN_HISTORY_SCOPE_FULL_IN_MEMORY = "full_in_memory_session"
VALID_TASK_SIGNALS = {"auto", "keep_trying", "task_completed", "end_session"}
DEFAULT_COMPLETION_MARKERS = ("好了", "完成", "做完", "搞定")
DEFAULT_END_SESSION_MARKERS = ("结束", "不玩了", "停一下", "退出")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionRuntimeError(RuntimeError):
    """Base error for the Phase 6 session runtime."""


class SessionNotFoundError(SessionRuntimeError):
    """Raised when a session cannot be found."""


class SessionConflictError(SessionRuntimeError):
    """Raised when a session exists but cannot accept the requested mutation."""


class RequestValidationError(SessionRuntimeError):
    """Raised when the API payload is malformed."""


class SessionPersistence(Protocol):
    def load_sessions(self) -> dict[str, "SessionState"]: ...

    def save_sessions(self, sessions: Mapping[str, "SessionState"]) -> None: ...


@dataclass(frozen=True)
class AssistantTurnResult:
    prompt_version: str
    reply_text: str
    guidance_type: str
    next_expected_action: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AssistantTurnResult":
        return cls(
            prompt_version=str(payload.get("prompt_version") or ""),
            reply_text=str(payload.get("reply_text") or ""),
            guidance_type=str(payload.get("guidance_type") or ""),
            next_expected_action=str(payload.get("next_expected_action") or ""),
            error=str(payload.get("error")) if payload.get("error") is not None else None,
        )


class AssistantTurnResponder(Protocol):
    def generate_reply(
        self,
        session: "SessionState",
        current_task: "SessionTaskState",
        child_input_text: str,
        resolved_task_signal: str,
        upcoming_task: "SessionTaskState | None",
    ) -> AssistantTurnResult: ...


@dataclass
class SessionTaskState:
    task_index: int
    task_id: str
    name: str
    goal: str
    expected_child_action: str
    status: str
    activated_at: str | None
    completed_at: str | None
    turn_count: int = 0
    last_turn_id: str | None = None
    last_turn_index: int | None = None
    last_turn_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["progress"] = {
            "is_current_task": self.status == "active",
            "is_completed": self.status == "completed",
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SessionTaskState":
        return cls(
            task_index=int(payload.get("task_index") or 0),
            task_id=str(payload.get("task_id") or ""),
            name=str(payload.get("name") or ""),
            goal=str(payload.get("goal") or ""),
            expected_child_action=str(payload.get("expected_child_action") or ""),
            status=str(payload.get("status") or "pending"),
            activated_at=str(payload.get("activated_at")) if payload.get("activated_at") is not None else None,
            completed_at=str(payload.get("completed_at")) if payload.get("completed_at") is not None else None,
            turn_count=int(payload.get("turn_count") or 0),
            last_turn_id=str(payload.get("last_turn_id")) if payload.get("last_turn_id") is not None else None,
            last_turn_index=int(payload.get("last_turn_index")) if payload.get("last_turn_index") is not None else None,
            last_turn_at=str(payload.get("last_turn_at")) if payload.get("last_turn_at") is not None else None,
        )


@dataclass
class SessionTurnState:
    turn_id: str
    turn_index: int
    task_id: str
    task_index: int
    task_name: str
    child_input_text: str
    requested_task_signal: str
    resolved_task_signal: str
    task_progress: str
    assistant_reply: AssistantTurnResult
    created_at: str
    task_status_before: str
    task_status_after: str
    session_status_after: str
    session_lifecycle_state_after: str
    current_task_id_after: str | None
    current_task_index_after: int | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assistant_reply"] = self.assistant_reply.to_dict()
        payload["task"] = {
            "task_id": self.task_id,
            "task_index": self.task_index,
            "task_name": self.task_name,
        }
        payload["signal"] = {
            "requested": self.requested_task_signal,
            "resolved": self.resolved_task_signal,
        }
        payload["outcome"] = {
            "task_progress": self.task_progress,
            "task_status_before": self.task_status_before,
            "task_status_after": self.task_status_after,
            "session_status_after": self.session_status_after,
            "session_lifecycle_state_after": self.session_lifecycle_state_after,
            "current_task_id_after": self.current_task_id_after,
            "current_task_index_after": self.current_task_index_after,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SessionTurnState":
        assistant_reply = payload.get("assistant_reply")
        return cls(
            turn_id=str(payload.get("turn_id") or ""),
            turn_index=int(payload.get("turn_index") or 0),
            task_id=str(payload.get("task_id") or ""),
            task_index=int(payload.get("task_index") or 0),
            task_name=str(payload.get("task_name") or ""),
            child_input_text=str(payload.get("child_input_text") or ""),
            requested_task_signal=str(payload.get("requested_task_signal") or "auto"),
            resolved_task_signal=str(payload.get("resolved_task_signal") or "auto"),
            task_progress=str(payload.get("task_progress") or "stayed_on_task"),
            assistant_reply=AssistantTurnResult.from_dict(
                assistant_reply if isinstance(assistant_reply, Mapping) else {}
            ),
            created_at=str(payload.get("created_at") or ""),
            task_status_before=str(payload.get("task_status_before") or ""),
            task_status_after=str(payload.get("task_status_after") or ""),
            session_status_after=str(payload.get("session_status_after") or ""),
            session_lifecycle_state_after=str(payload.get("session_lifecycle_state_after") or ""),
            current_task_id_after=str(payload.get("current_task_id_after")) if payload.get("current_task_id_after") is not None else None,
            current_task_index_after=int(payload.get("current_task_index_after")) if payload.get("current_task_index_after") is not None else None,
        )


@dataclass
class SessionState:
    session_id: str
    scene_id: str
    source_kind: str
    session_scope: str
    is_persisted_session: bool
    lifecycle_state: str
    status: str
    started_at: str
    updated_at: str
    ended_at: str | None
    ended_reason: str | None
    current_task_index: int | None
    current_task_id: str | None
    current_turn_index: int | None
    turn_count: int
    task_count: int
    completed_task_count: int
    tasks: list[SessionTaskState] = field(default_factory=list)
    turns: list[SessionTurnState] = field(default_factory=list)

    def current_task(self) -> SessionTaskState | None:
        if self.current_task_index is None:
            return None
        if self.current_task_index < 0 or self.current_task_index >= len(self.tasks):
            return None
        return self.tasks[self.current_task_index]

    def current_turn(self) -> SessionTurnState | None:
        if self.current_turn_index is None:
            return None
        if self.current_turn_index < 0 or self.current_turn_index >= len(self.turns):
            return None
        return self.turns[self.current_turn_index]

    def to_session_dict(self) -> dict[str, Any]:
        current_turn = self.current_turn()
        return {
            "session_id": self.session_id,
            "scene_id": self.scene_id,
            "source_kind": self.source_kind,
            "session_scope": self.session_scope,
            "runtime_mode": RUNTIME_MODE_STATEFUL_SESSION,
            "is_persisted_session": self.is_persisted_session,
            "lifecycle_state": self.lifecycle_state,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "ended_reason": self.ended_reason,
            "current_task_index": self.current_task_index,
            "current_task_id": self.current_task_id,
            "current_task_index_semantics": CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER,
            "current_turn_index": self.current_turn_index,
            "current_turn_id": current_turn.turn_id if current_turn is not None else None,
            "current_turn_index_semantics": CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION,
            "last_turn_at": current_turn.created_at if current_turn is not None else None,
            "turn_count": self.turn_count,
            "task_count": self.task_count,
            "completed_task_count": self.completed_task_count,
            "remaining_task_count": max(self.task_count - self.completed_task_count, 0),
        }

    def to_persisted_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SessionState":
        raw_tasks = payload.get("tasks") or []
        raw_turns = payload.get("turns") or []
        return cls(
            session_id=str(payload.get("session_id") or ""),
            scene_id=str(payload.get("scene_id") or ""),
            source_kind=str(payload.get("source_kind") or SOURCE_KIND_SESSION_RUNTIME),
            session_scope=str(payload.get("session_scope") or SESSION_SCOPE_PROCESS_MEMORY_STATEFUL),
            is_persisted_session=bool(payload.get("is_persisted_session")),
            lifecycle_state=str(payload.get("lifecycle_state") or "bootstrapped"),
            status=str(payload.get("status") or "active"),
            started_at=str(payload.get("started_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            ended_at=str(payload.get("ended_at")) if payload.get("ended_at") is not None else None,
            ended_reason=str(payload.get("ended_reason")) if payload.get("ended_reason") is not None else None,
            current_task_index=int(payload.get("current_task_index")) if payload.get("current_task_index") is not None else None,
            current_task_id=str(payload.get("current_task_id")) if payload.get("current_task_id") is not None else None,
            current_turn_index=int(payload.get("current_turn_index")) if payload.get("current_turn_index") is not None else None,
            turn_count=int(payload.get("turn_count") or 0),
            task_count=int(payload.get("task_count") or len(raw_tasks)),
            completed_task_count=int(payload.get("completed_task_count") or 0),
            tasks=[
                SessionTaskState.from_dict(task)
                for task in raw_tasks
                if isinstance(task, Mapping)
            ],
            turns=[
                SessionTurnState.from_dict(turn)
                for turn in raw_turns
                if isinstance(turn, Mapping)
            ],
        )


class SessionRuntimeService:
    def __init__(
        self,
        scene_id: str,
        task_blueprints: Sequence[Mapping[str, Any]],
        responder: AssistantTurnResponder,
        auto_complete_keywords: Mapping[str, Sequence[str]] | None = None,
        default_task_ids: Sequence[str] | None = None,
        persistence: SessionPersistence | None = None,
    ):
        if not task_blueprints:
            raise ValueError("task_blueprints must contain at least one task")

        self.scene_id = scene_id
        self.task_blueprints = [dict(task) for task in task_blueprints]
        self.responder = responder
        self.default_task_ids = tuple(default_task_ids) if default_task_ids is not None else None
        self.persistence = persistence
        self.auto_complete_keywords = {
            task_id: tuple(values)
            for task_id, values in (auto_complete_keywords or {}).items()
        }
        self._lock = Lock()
        self._sessions = dict(self.persistence.load_sessions()) if self.persistence else {}

    def create_session(self, task_ids: Sequence[str] | None = None) -> dict[str, Any]:
        with self._lock:
            selected_blueprints = self._resolve_task_blueprints(
                task_ids if task_ids is not None else self.default_task_ids
            )
            started_at = iso_now()
            tasks = self._bootstrap_tasks(selected_blueprints, started_at)
            first_task = tasks[0]
            session = SessionState(
                session_id=f"ses_phase6_{uuid4().hex[:12]}",
                scene_id=self.scene_id,
                source_kind=SOURCE_KIND_SESSION_RUNTIME,
                session_scope=(
                    SESSION_SCOPE_JSON_FILE_STATEFUL
                    if self.persistence is not None
                    else SESSION_SCOPE_PROCESS_MEMORY_STATEFUL
                ),
                is_persisted_session=self.persistence is not None,
                lifecycle_state="bootstrapped",
                status="active",
                started_at=started_at,
                updated_at=started_at,
                ended_at=None,
                ended_reason=None,
                current_task_index=0,
                current_task_id=first_task.task_id,
                current_turn_index=None,
                turn_count=0,
                task_count=len(tasks),
                completed_task_count=0,
                tasks=tasks,
                turns=[],
            )
            self._sessions[session.session_id] = session
            self._persist_sessions_locked()
            return self._snapshot_for_session(session)

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_for_session(self._get_session(session_id))

    def submit_turn(
        self,
        session_id: str,
        child_input_text: str,
        task_signal: str = "auto",
    ) -> dict[str, Any]:
        if not isinstance(child_input_text, str):
            raise RequestValidationError("child_input_text must be a string")
        if not isinstance(task_signal, str):
            raise RequestValidationError("task_signal must be a string")

        normalized_signal = task_signal.strip() or "auto"
        if normalized_signal not in VALID_TASK_SIGNALS:
            allowed = ", ".join(sorted(VALID_TASK_SIGNALS))
            raise RequestValidationError(f"task_signal must be one of: {allowed}")

        with self._lock:
            session = self._get_session(session_id)
            if session.status != "active":
                raise SessionConflictError(f"Session {session_id} is already ended.")

            current_task = session.current_task()
            if current_task is None:
                raise SessionConflictError(f"Session {session_id} does not have an active task.")

            resolved_signal = self._resolve_task_signal(
                current_task=current_task,
                child_input_text=child_input_text,
                requested_task_signal=normalized_signal,
            )
            upcoming_task = self._preview_upcoming_task(session, resolved_signal)
            assistant_reply = self.responder.generate_reply(
                session=session,
                current_task=current_task,
                child_input_text=child_input_text,
                resolved_task_signal=resolved_signal,
                upcoming_task=upcoming_task,
            )

            task_progress = self._resolve_task_progress(
                session=session,
                resolved_task_signal=resolved_signal,
            )
            created_at = iso_now()
            task_status_before = current_task.status
            current_task.turn_count += 1
            current_task.last_turn_at = created_at
            turn = SessionTurnState(
                turn_id=f"turn_{uuid4().hex[:12]}",
                turn_index=session.turn_count,
                task_id=current_task.task_id,
                task_index=current_task.task_index,
                task_name=current_task.name,
                child_input_text=child_input_text,
                requested_task_signal=normalized_signal,
                resolved_task_signal=resolved_signal,
                task_progress=task_progress,
                assistant_reply=self._normalize_assistant_reply(
                    assistant_reply=assistant_reply,
                    current_task=current_task,
                    resolved_task_signal=resolved_signal,
                    upcoming_task=upcoming_task,
                ),
                created_at=created_at,
                task_status_before=task_status_before,
                task_status_after=task_status_before,
                session_status_after=session.status,
                session_lifecycle_state_after=session.lifecycle_state,
                current_task_id_after=session.current_task_id,
                current_task_index_after=session.current_task_index,
            )
            current_task.last_turn_id = turn.turn_id
            current_task.last_turn_index = turn.turn_index

            session.turns.append(turn)
            session.turn_count += 1
            session.current_turn_index = turn.turn_index
            session.updated_at = created_at
            if session.lifecycle_state == "bootstrapped":
                session.lifecycle_state = "in_progress"

            if resolved_signal == "task_completed":
                current_task.status = "completed"
                current_task.completed_at = created_at
                session.completed_task_count += 1
                self._advance_after_task_completion(session, created_at)
            elif resolved_signal == "end_session":
                session.status = "ended"
                session.lifecycle_state = "ended"
                session.ended_at = created_at
                session.ended_reason = "explicit_end_session"

            turn.task_status_after = current_task.status
            turn.session_status_after = session.status
            turn.session_lifecycle_state_after = session.lifecycle_state
            turn.current_task_id_after = session.current_task_id
            turn.current_task_index_after = session.current_task_index
            self._persist_sessions_locked()

            return self._snapshot_for_session(session)

    def _resolve_task_blueprints(
        self,
        task_ids: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        if not task_ids:
            return [dict(task) for task in self.task_blueprints]

        blueprint_by_id = {
            str(task.get("task_id")): dict(task)
            for task in self.task_blueprints
        }
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_task_id in task_ids:
            task_id = str(raw_task_id).strip()
            if not task_id or task_id in seen:
                continue
            if task_id not in blueprint_by_id:
                available = ", ".join(sorted(blueprint_by_id))
                raise RequestValidationError(
                    f"Unknown task_id '{task_id}'. Available tasks: {available}"
                )
            selected.append(dict(blueprint_by_id[task_id]))
            seen.add(task_id)

        if not selected:
            raise RequestValidationError("task_ids must contain at least one valid task_id")
        return selected

    def _bootstrap_tasks(
        self,
        selected_blueprints: Sequence[Mapping[str, Any]],
        started_at: str,
    ) -> list[SessionTaskState]:
        tasks: list[SessionTaskState] = []
        for task_index, blueprint in enumerate(selected_blueprints):
            tasks.append(
                SessionTaskState(
                    task_index=task_index,
                    task_id=str(blueprint.get("task_id") or ""),
                    name=str(blueprint.get("name") or ""),
                    goal=str(blueprint.get("goal") or ""),
                    expected_child_action=str(blueprint.get("expected_child_action") or ""),
                    status="active" if task_index == 0 else "pending",
                    activated_at=started_at if task_index == 0 else None,
                    completed_at=None,
                    turn_count=0,
                    last_turn_index=None,
                )
            )
        return tasks

    def _get_session(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")
        return session

    def _resolve_task_signal(
        self,
        current_task: SessionTaskState,
        child_input_text: str,
        requested_task_signal: str,
    ) -> str:
        if requested_task_signal != "auto":
            return requested_task_signal

        normalized_text = child_input_text.strip()
        if not normalized_text:
            return "keep_trying"
        if any(marker in normalized_text for marker in DEFAULT_END_SESSION_MARKERS):
            return "end_session"

        task_keywords = self.auto_complete_keywords.get(current_task.task_id, ())
        if any(keyword in normalized_text for keyword in task_keywords):
            return "task_completed"
        if any(marker in normalized_text for marker in DEFAULT_COMPLETION_MARKERS):
            return "task_completed"
        return "keep_trying"

    def _preview_upcoming_task(
        self,
        session: SessionState,
        resolved_task_signal: str,
    ) -> SessionTaskState | None:
        if resolved_task_signal != "task_completed":
            return None
        current_task_index = session.current_task_index
        if current_task_index is None:
            return None
        next_index = current_task_index + 1
        if next_index >= len(session.tasks):
            return None

        next_task = session.tasks[next_index]
        return SessionTaskState(
            task_index=next_task.task_index,
            task_id=next_task.task_id,
            name=next_task.name,
            goal=next_task.goal,
            expected_child_action=next_task.expected_child_action,
            status="active",
            activated_at=next_task.activated_at,
            completed_at=next_task.completed_at,
            turn_count=next_task.turn_count,
        )

    def _resolve_task_progress(
        self,
        session: SessionState,
        resolved_task_signal: str,
    ) -> str:
        if resolved_task_signal == "end_session":
            return "ended_session"
        if resolved_task_signal != "task_completed":
            return "stayed_on_task"

        current_task_index = session.current_task_index
        if current_task_index is None or current_task_index >= len(session.tasks) - 1:
            return "ended_session"
        return "advanced_to_next_task"

    def _normalize_assistant_reply(
        self,
        assistant_reply: AssistantTurnResult,
        current_task: SessionTaskState,
        resolved_task_signal: str,
        upcoming_task: SessionTaskState | None,
    ) -> AssistantTurnResult:
        fallback_next_action = current_task.expected_child_action
        if resolved_task_signal == "task_completed":
            fallback_next_action = (
                upcoming_task.expected_child_action if upcoming_task else "结束本轮"
            )
        elif resolved_task_signal == "end_session":
            fallback_next_action = "结束本轮"

        next_expected_action = assistant_reply.next_expected_action.strip() or fallback_next_action
        return AssistantTurnResult(
            prompt_version=assistant_reply.prompt_version,
            reply_text=assistant_reply.reply_text,
            guidance_type=assistant_reply.guidance_type,
            next_expected_action=next_expected_action,
            error=assistant_reply.error,
        )

    def _advance_after_task_completion(self, session: SessionState, completed_at: str) -> None:
        current_task_index = session.current_task_index
        if current_task_index is None:
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = completed_at
            session.ended_reason = "no_active_task"
            session.current_task_id = None
            return

        next_index = current_task_index + 1
        if next_index >= len(session.tasks):
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = completed_at
            session.ended_reason = "completed_all_tasks"
            session.current_task_index = None
            session.current_task_id = None
            return

        next_task = session.tasks[next_index]
        next_task.status = "active"
        next_task.activated_at = completed_at
        session.current_task_index = next_index
        session.current_task_id = next_task.task_id

    def _persist_sessions_locked(self) -> None:
        if self.persistence is None:
            return
        self.persistence.save_sessions(self._sessions)

    def _snapshot_for_session(self, session: SessionState) -> dict[str, Any]:
        current_task = session.current_task()
        current_turn = session.current_turn()
        is_persisted_session = session.is_persisted_session
        disclaimer = (
            "当前 Phase 6 session runtime 已做最小 JSON 持久化。"
            "服务重启后 session 还能继续取回。"
            "task_signal=auto 还是极薄 heuristic，不等于正式 child understanding。"
            if is_persisted_session
            else (
                "当前 Phase 6 session runtime 只做进程内 stateful session。"
                "task_signal=auto 还是极薄 heuristic，不等于正式 child understanding。"
            )
        )
        return {
            "ok": True,
            "api_version": ENDPOINT_VERSION,
            "snapshot_kind": SNAPSHOT_KIND_SESSION_STATE,
            "session": session.to_session_dict(),
            "current_task": current_task.to_dict() if current_task else None,
            "current_turn": current_turn.to_dict() if current_turn else None,
            "tasks": [task.to_dict() for task in session.tasks],
            "turns": [turn.to_dict() for turn in session.turns],
            "viewer_context": {
                "landing_task_id": session.current_task_id,
                "landing_task_index": session.current_task_index,
                "latest_turn_id": current_turn.turn_id if current_turn else None,
                "latest_turn_index": current_turn.turn_index if current_turn else None,
                "latest_turn_task_id": current_turn.task_id if current_turn else None,
                "turn_panel_mode": "viewed_task_latest_turn",
                "browse_task_turn_rule": (
                    "When browsing a non-current task, show that task's latest turn when it exists; "
                    "otherwise explain how it relates to session.current_turn."
                ),
            },
            "meta": {
                "endpoint_version": ENDPOINT_VERSION,
                "api_version": ENDPOINT_VERSION,
                "snapshot_kind": SNAPSHOT_KIND_SESSION_STATE,
                "source_kind": session.source_kind,
                "source_name": "/api/session-runtime",
                "session_scope": session.session_scope,
                "runtime_mode": RUNTIME_MODE_STATEFUL_SESSION,
                "is_persisted_session": is_persisted_session,
                "current_task_index_semantics": CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER,
                "current_turn_index_semantics": CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION,
                "task_signal_mode": "auto_or_explicit",
                "available_task_signals": sorted(VALID_TASK_SIGNALS),
                "auto_task_signal_is_heuristic": True,
                "turn_history_scope": TURN_HISTORY_SCOPE_FULL_IN_MEMORY,
                "supports": {
                    "create_session": True,
                    "get_session": True,
                    "submit_turn": True,
                    "persisted_session": is_persisted_session,
                },
                "generated_at": iso_now(),
                "disclaimer": disclaimer,
            },
        }
