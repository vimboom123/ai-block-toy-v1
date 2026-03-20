from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .state_machine import (
    STATE_MACHINE_VERSION,
    TurnInterpretation,
    build_parent_action,
    build_parent_summary_short,
    collect_task_anchor_keywords,
    contains_frustration_marker,
    derive_display_status,
    max_help_level,
    next_help_level,
    public_stage_for_state,
    public_stage_text,
    should_treat_as_off_topic,
)

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
DEFAULT_CONFIRM_DONE_MARKERS = ("好了", "搭好了", "做好了", "完成了", "弄好了")
DEFAULT_CONFIRM_UNDONE_MARKERS = ("还没", "没有", "没好", "没搭好", "不会")


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
    story_beat: str | None = None
    selected_entities: tuple[str, ...] = ()
    selected_background_elements: tuple[str, ...] = ()
    completion_points: tuple[dict[str, Any], ...] = ()
    parent_label: str | None = None
    requires_self_report: bool = False
    assistant_led_summary: bool = False
    help_level_current: str = "none"
    help_level_peak: str = "none"
    attempt_count: int = 0
    max_attempts: int = 5
    result_code: str | None = None
    parent_note: str | None = None
    awaiting_child_confirmation: bool = False
    turn_count: int = 0
    last_turn_id: str | None = None
    last_turn_index: int | None = None
    last_turn_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_entities"] = list(self.selected_entities)
        payload["selected_background_elements"] = list(self.selected_background_elements)
        payload["completion_points"] = [dict(item) for item in self.completion_points]
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
            story_beat=str(payload.get("story_beat")) if payload.get("story_beat") is not None else None,
            selected_entities=tuple(
                str(item).strip()
                for item in (payload.get("selected_entities") or ())
                if str(item).strip()
            ),
            selected_background_elements=tuple(
                str(item).strip()
                for item in (payload.get("selected_background_elements") or ())
                if str(item).strip()
            ),
            completion_points=tuple(
                dict(item)
                for item in (payload.get("completion_points") or ())
                if isinstance(item, Mapping)
            ),
            status=str(payload.get("status") or "pending"),
            activated_at=str(payload.get("activated_at")) if payload.get("activated_at") is not None else None,
            completed_at=str(payload.get("completed_at")) if payload.get("completed_at") is not None else None,
            parent_label=str(payload.get("parent_label")) if payload.get("parent_label") is not None else None,
            requires_self_report=bool(payload.get("requires_self_report")),
            assistant_led_summary=bool(payload.get("assistant_led_summary")),
            help_level_current=str(payload.get("help_level_current") or "none"),
            help_level_peak=str(payload.get("help_level_peak") or "none"),
            attempt_count=int(payload.get("attempt_count") or 0),
            max_attempts=int(payload.get("max_attempts") or 5),
            result_code=str(payload.get("result_code")) if payload.get("result_code") is not None else None,
            parent_note=str(payload.get("parent_note")) if payload.get("parent_note") is not None else None,
            awaiting_child_confirmation=bool(payload.get("awaiting_child_confirmation")),
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
    state_before: str = "warming_up"
    state_after: str = "warming_up"
    state_path: tuple[str, ...] = ()
    public_stage_before: str = "warming_up"
    public_stage_after: str = "warming_up"
    help_level_before: str = "none"
    help_level_after: str = "none"
    interpretation: TurnInterpretation | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assistant_reply"] = self.assistant_reply.to_dict()
        if self.interpretation is not None:
            payload["interpretation"] = self.interpretation.to_dict()
        payload["task"] = {
            "task_id": self.task_id,
            "task_index": self.task_index,
            "task_name": self.task_name,
        }
        payload["state_machine"] = {
            "state_before": self.state_before,
            "state_after": self.state_after,
            "state_path": list(self.state_path),
            "public_stage_before": self.public_stage_before,
            "public_stage_after": self.public_stage_after,
            "help_level_before": self.help_level_before,
            "help_level_after": self.help_level_after,
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
            "state_after": self.state_after,
            "public_stage_after": self.public_stage_after,
            "help_level_after": self.help_level_after,
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
            state_before=str(payload.get("state_before") or "warming_up"),
            state_after=str(payload.get("state_after") or "warming_up"),
            state_path=tuple(payload.get("state_path") or ()),
            public_stage_before=str(payload.get("public_stage_before") or "warming_up"),
            public_stage_after=str(payload.get("public_stage_after") or "warming_up"),
            help_level_before=str(payload.get("help_level_before") or "none"),
            help_level_after=str(payload.get("help_level_after") or "none"),
            interpretation=TurnInterpretation.from_payload(
                payload.get("interpretation") if isinstance(payload.get("interpretation"), dict) else None
            ),
        )


@dataclass
class SessionState:
    session_id: str
    scene_id: str
    source_kind: str
    session_scope: str
    is_persisted_session: bool
    state_machine_version: str
    lifecycle_state: str
    status: str
    current_state: str
    public_stage: str
    help_level_current: str
    help_level_peak: str
    anchor_state: str | None
    reengage_count: int
    off_topic_count: int
    parent_summary_short: str | None
    story_title: str | None
    story_context: str | None
    plan_generation_source: str | None
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
    retry_count: int
    bootstrap_state_path: tuple[str, ...] = ("session_bootstrap", "warming_up")
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
            "state_machine_version": self.state_machine_version,
            "lifecycle_state": self.lifecycle_state,
            "status": self.status,
            "display_status": derive_display_status(
                status=self.status,
                public_stage=self.public_stage,
            ),
            "current_state": self.current_state,
            "public_stage": self.public_stage,
            "public_stage_text": public_stage_text(self.public_stage),
            "help_level_current": self.help_level_current,
            "help_level_peak": self.help_level_peak,
            "anchor_state": self.anchor_state,
            "reengage_count": self.reengage_count,
            "off_topic_count": self.off_topic_count,
            "parent_summary_short": self.parent_summary_short,
            "story_title": self.story_title,
            "story_context": self.story_context,
            "plan_generation_source": self.plan_generation_source,
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
            "retry_count": self.retry_count,
            "bootstrap_state_path": list(self.bootstrap_state_path),
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
            state_machine_version=str(payload.get("state_machine_version") or STATE_MACHINE_VERSION),
            lifecycle_state=str(payload.get("lifecycle_state") or "bootstrapped"),
            status=str(payload.get("status") or "active"),
            current_state=str(payload.get("current_state") or "warming_up"),
            public_stage=str(payload.get("public_stage") or "warming_up"),
            help_level_current=str(payload.get("help_level_current") or "none"),
            help_level_peak=str(payload.get("help_level_peak") or "none"),
            anchor_state=str(payload.get("anchor_state")) if payload.get("anchor_state") is not None else None,
            reengage_count=int(payload.get("reengage_count") or 0),
            off_topic_count=int(payload.get("off_topic_count") or 0),
            parent_summary_short=str(payload.get("parent_summary_short")) if payload.get("parent_summary_short") is not None else None,
            story_title=str(payload.get("story_title")) if payload.get("story_title") is not None else None,
            story_context=str(payload.get("story_context")) if payload.get("story_context") is not None else None,
            plan_generation_source=str(payload.get("plan_generation_source")) if payload.get("plan_generation_source") is not None else None,
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
            retry_count=int(payload.get("retry_count") or 0),
            bootstrap_state_path=tuple(payload.get("bootstrap_state_path") or ("session_bootstrap", "warming_up")),
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
        task_blueprint_planner: Any | None = None,
    ):
        if not task_blueprints:
            raise ValueError("task_blueprints must contain at least one task")

        self.scene_id = scene_id
        self.task_blueprints = [dict(task) for task in task_blueprints]
        self.responder = responder
        self.default_task_ids = tuple(default_task_ids) if default_task_ids is not None else None
        self.persistence = persistence
        self.task_blueprint_planner = task_blueprint_planner
        self.auto_complete_keywords = {
            task_id: tuple(values)
            for task_id, values in (auto_complete_keywords or {}).items()
        }
        self._lock = Lock()
        self._sessions = dict(self.persistence.load_sessions()) if self.persistence else {}

    def create_session(self, task_ids: Sequence[str] | None = None) -> dict[str, Any]:
        with self._lock:
            session_id = f"ses_phase6_{uuid4().hex[:12]}"
            requested_task_ids = task_ids if task_ids is not None else self.default_task_ids
            selected_blueprints = self._resolve_task_blueprints(requested_task_ids)
            story_title: str | None = None
            story_context: str | None = None
            plan_generation_source: str | None = None
            if self.task_blueprint_planner is not None:
                generated_plan = None
                fallback_builder = getattr(self.task_blueprint_planner, "build_fallback_plan", None)
                try:
                    generated_plan = self.task_blueprint_planner.build_plan(
                        session_id=session_id,
                        scene_id=self.scene_id,
                        requested_task_ids=requested_task_ids,
                        default_task_blueprints=selected_blueprints,
                    )
                except Exception:
                    if callable(fallback_builder):
                        try:
                            generated_plan = fallback_builder(
                                session_id=session_id,
                                scene_id=self.scene_id,
                                selected_task_ids=requested_task_ids
                                or tuple(
                                    str(task.get("task_id") or "").strip()
                                    for task in selected_blueprints
                                    if str(task.get("task_id") or "").strip()
                                ),
                                default_task_blueprints=selected_blueprints,
                            )
                        except Exception:
                            generated_plan = None
                if generated_plan is None and callable(fallback_builder):
                    try:
                        generated_plan = fallback_builder(
                            session_id=session_id,
                            scene_id=self.scene_id,
                            selected_task_ids=requested_task_ids
                            or tuple(
                                str(task.get("task_id") or "").strip()
                                for task in selected_blueprints
                                if str(task.get("task_id") or "").strip()
                            ),
                            default_task_blueprints=selected_blueprints,
                        )
                    except Exception:
                        generated_plan = None
                if generated_plan is not None:
                    selected_blueprints = [dict(task) for task in generated_plan.task_blueprints]
                    story_title = str(generated_plan.story_title or "").strip() or None
                    story_context = str(generated_plan.story_context or "").strip() or None
                    plan_generation_source = str(generated_plan.generation_source or "").strip() or None
            started_at = iso_now()
            tasks = self._bootstrap_tasks(selected_blueprints, started_at)
            first_task = tasks[0]
            session = SessionState(
                session_id=session_id,
                scene_id=self.scene_id,
                source_kind=SOURCE_KIND_SESSION_RUNTIME,
                session_scope=(
                    SESSION_SCOPE_JSON_FILE_STATEFUL
                    if self.persistence is not None
                    else SESSION_SCOPE_PROCESS_MEMORY_STATEFUL
                ),
                is_persisted_session=self.persistence is not None,
                state_machine_version=STATE_MACHINE_VERSION,
                lifecycle_state="bootstrapped",
                status="active",
                current_state="warming_up",
                public_stage=public_stage_for_state("warming_up"),
                help_level_current="none",
                help_level_peak="none",
                anchor_state=None,
                reengage_count=0,
                off_topic_count=0,
                parent_summary_short=None,
                story_title=story_title,
                story_context=story_context,
                plan_generation_source=plan_generation_source,
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
                retry_count=0,
                bootstrap_state_path=("session_bootstrap", "warming_up"),
                tasks=tasks,
                turns=[],
            )
            session.parent_summary_short = build_parent_summary_short(
                completed_task_count=session.completed_task_count,
                public_stage=session.public_stage,
                current_task_label=first_task.parent_label or first_task.name,
            )
            self._sessions[session.session_id] = session
            self._persist_sessions_locked()
            return self._snapshot_for_session(session)

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_for_session(self._get_session(session_id))

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def active_session_count(self) -> int:
        with self._lock:
            return sum(1 for session in self._sessions.values() if session.status == "active")

    def latest_session_id(self) -> str | None:
        with self._lock:
            latest_session = self._latest_session(active_only=False)
            return latest_session.session_id if latest_session is not None else None

    def latest_active_session_id(self) -> str | None:
        with self._lock:
            latest_session = self._latest_session(active_only=True)
            return latest_session.session_id if latest_session is not None else None

    def list_recent_session_summaries(self, *, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            sessions = sorted(
                self._sessions.values(),
                key=lambda session: (
                    session.updated_at or "",
                    session.started_at or "",
                    session.session_id,
                ),
                reverse=True,
            )
            summaries: list[dict[str, Any]] = []
            for session in sessions[: max(0, limit)]:
                last_turn = session.current_turn() or (session.turns[-1] if session.turns else None)
                last_child_input = last_turn.child_input_text if last_turn is not None else None
                recent_child_inputs = [
                    turn.child_input_text
                    for turn in session.turns[-3:]
                    if turn.child_input_text.strip()
                ]
                current_task = session.current_task()
                stuck_point = (
                    current_task.parent_label
                    or current_task.name
                    if current_task is not None and session.status != "ended"
                    else None
                )
                summaries.append(
                    {
                        "session_id": session.session_id,
                        "scene_id": session.scene_id,
                        "status": session.status,
                        "display_status": derive_display_status(
                            status=session.status,
                            public_stage=session.public_stage,
                        ),
                        "current_state": session.current_state,
                        "public_stage": session.public_stage,
                        "public_stage_text": public_stage_text(session.public_stage),
                        "started_at": session.started_at,
                        "updated_at": session.updated_at,
                        "ended_at": session.ended_at,
                        "ended_reason": session.ended_reason,
                        "turn_count": session.turn_count,
                        "task_count": session.task_count,
                        "completed_task_count": session.completed_task_count,
                        "retry_count": session.retry_count,
                        "help_level_peak": session.help_level_peak,
                        "parent_summary_short": session.parent_summary_short,
                        "story_title": session.story_title,
                        "story_context": session.story_context,
                        "plan_generation_source": session.plan_generation_source,
                        "current_task_id": session.current_task_id,
                        "current_task_name": current_task.name if current_task is not None else None,
                        "stuck_point": stuck_point,
                        "last_child_input_text": last_child_input,
                        "recent_child_inputs": recent_child_inputs,
                        "last_resolved_signal": last_turn.resolved_task_signal if last_turn is not None else None,
                    }
                )
            return summaries

    def resume_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            if session.status != "paused":
                raise SessionConflictError(f"Session {session_id} is not paused.")

            resumed_at = iso_now()
            resume_state = session.anchor_state or "await_answer"
            session.status = "active"
            session.current_state = resume_state
            session.public_stage = public_stage_for_state(
                resume_state,
                anchor_state=session.anchor_state,
            )
            session.updated_at = resumed_at
            self._refresh_session_summary(session)
            self._persist_sessions_locked()
            return self._snapshot_for_session(session)

    def terminate_session(
        self,
        session_id: str,
        *,
        end_reason: str = "parent_interrupted",
    ) -> dict[str, Any]:
        with self._lock:
            session = self._get_session(session_id)
            if session.status in {"ended", "aborted"}:
                raise SessionConflictError(f"Session {session_id} is already ended.")

            ended_at = iso_now()
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = ended_at
            session.ended_reason = end_reason
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            session.current_task_id = None
            session.current_task_index = None
            session.updated_at = ended_at
            self._refresh_session_summary(session)
            self._persist_sessions_locked()
            return self._snapshot_for_session(session)

    def submit_turn(
        self,
        session_id: str,
        child_input_text: str,
        task_signal: str = "auto",
        assistant_reply_override: AssistantTurnResult | None = None,
        interpretation: TurnInterpretation | None = None,
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
            if session.status == "paused":
                raise SessionConflictError(
                    f"Session {session_id} is paused and must be resumed before accepting new turns."
                )
            if session.status != "active":
                raise SessionConflictError(f"Session {session_id} is already ended.")

            current_task = session.current_task()
            if current_task is None:
                raise SessionConflictError(f"Session {session_id} does not have an active task.")

            created_at = iso_now()
            state_before = session.current_state
            public_stage_before = session.public_stage
            help_level_before = current_task.help_level_current

            state_path: list[str] = []
            self._append_state_path(state_path, session.current_state)
            self._advance_to_listening_state(session=session, state_path=state_path)
            self._append_state_path(state_path, "interpret_input")
            session.current_state = "interpret_input"
            session.public_stage = public_stage_for_state(session.current_state)

            resolved_signal = self._resolve_task_signal(
                current_task=current_task,
                child_input_text=child_input_text,
                requested_task_signal=normalized_signal,
            )
            upcoming_task = self._preview_upcoming_task(session, resolved_signal)
            assistant_reply = (
                assistant_reply_override
                if assistant_reply_override is not None
                else self.responder.generate_reply(
                    session=session,
                    current_task=current_task,
                    child_input_text=child_input_text,
                    resolved_task_signal=resolved_signal,
                    upcoming_task=upcoming_task,
                )
            )
            task_status_before = current_task.status
            current_task.turn_count += 1
            current_task.last_turn_at = created_at
            current_task.last_turn_id = f"turn_{uuid4().hex[:12]}"
            current_task.last_turn_index = session.turn_count

            task_progress = self._apply_turn_state_machine(
                session=session,
                current_task=current_task,
                child_input_text=child_input_text,
                resolved_signal=resolved_signal,
                interpretation=interpretation,
                state_path=state_path,
                created_at=created_at,
            )

            turn = SessionTurnState(
                turn_id=current_task.last_turn_id,
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
                state_before=state_before,
                state_after=session.current_state,
                state_path=tuple(state_path),
                public_stage_before=public_stage_before,
                public_stage_after=session.public_stage,
                help_level_before=help_level_before,
                help_level_after=current_task.help_level_current,
                interpretation=interpretation,
            )

            session.turns.append(turn)
            session.turn_count += 1
            session.current_turn_index = turn.turn_index
            session.updated_at = created_at
            if session.lifecycle_state == "bootstrapped":
                session.lifecycle_state = "in_progress"

            turn.task_status_after = current_task.status
            turn.session_status_after = session.status
            turn.session_lifecycle_state_after = session.lifecycle_state
            turn.current_task_id_after = session.current_task_id
            turn.current_task_index_after = session.current_task_index
            self._refresh_session_summary(session)
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
                    story_beat=str(blueprint.get("story_beat")).strip()
                    if blueprint.get("story_beat") is not None
                    else None,
                    selected_entities=tuple(
                        str(item).strip()
                        for item in (blueprint.get("selected_entities") or ())
                        if str(item).strip()
                    ),
                    selected_background_elements=tuple(
                        str(item).strip()
                        for item in (blueprint.get("selected_background_elements") or ())
                        if str(item).strip()
                    ),
                    completion_points=tuple(
                        dict(item)
                        for item in (blueprint.get("completion_points") or ())
                        if isinstance(item, Mapping)
                    ),
                    status="active" if task_index == 0 else "pending",
                    activated_at=started_at if task_index == 0 else None,
                    completed_at=None,
                    parent_label=str(blueprint.get("parent_label") or blueprint.get("name") or ""),
                    requires_self_report=bool(blueprint.get("requires_self_report")),
                    assistant_led_summary=bool(blueprint.get("assistant_led_summary")),
                    help_level_current="none",
                    help_level_peak="none",
                    attempt_count=0,
                    max_attempts=int(blueprint.get("max_attempts") or 5),
                    result_code=None,
                    parent_note="准备进入这一步" if task_index == 0 else "等待前一步完成",
                    awaiting_child_confirmation=False,
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

    def _latest_session(self, *, active_only: bool) -> SessionState | None:
        sessions = [
            session
            for session in self._sessions.values()
            if not active_only or session.status == "active"
        ]
        if not sessions:
            return None

        def sort_key(session: SessionState) -> tuple[str, str, str]:
            return (
                session.updated_at or "",
                session.started_at or "",
                session.session_id,
            )

        return max(sessions, key=sort_key)

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
        if current_task.awaiting_child_confirmation:
            if any(marker in normalized_text for marker in DEFAULT_CONFIRM_DONE_MARKERS):
                return "task_completed"
            if any(marker in normalized_text for marker in DEFAULT_CONFIRM_UNDONE_MARKERS):
                return "keep_trying"

        task_keywords = list(self.auto_complete_keywords.get(current_task.task_id, ()))
        if current_task.completion_points:
            for completion_point in current_task.completion_points:
                task_keywords.extend(
                    str(keyword).strip()
                    for keyword in (completion_point.get("keywords") or ())
                    if str(keyword).strip()
                )
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
            story_beat=next_task.story_beat,
            selected_entities=next_task.selected_entities,
            selected_background_elements=next_task.selected_background_elements,
            completion_points=next_task.completion_points,
            status="active",
            activated_at=next_task.activated_at,
            completed_at=next_task.completed_at,
            parent_label=next_task.parent_label,
            requires_self_report=next_task.requires_self_report,
            assistant_led_summary=next_task.assistant_led_summary,
            help_level_current=next_task.help_level_current,
            help_level_peak=next_task.help_level_peak,
            attempt_count=next_task.attempt_count,
            max_attempts=next_task.max_attempts,
            result_code=next_task.result_code,
            parent_note=next_task.parent_note,
            awaiting_child_confirmation=next_task.awaiting_child_confirmation,
            turn_count=next_task.turn_count,
            last_turn_id=next_task.last_turn_id,
            last_turn_index=next_task.last_turn_index,
            last_turn_at=next_task.last_turn_at,
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

    @staticmethod
    def _append_state_path(state_path: list[str], state: str | None) -> None:
        if state and (not state_path or state_path[-1] != state):
            state_path.append(state)

    def _advance_to_listening_state(self, *, session: SessionState, state_path: list[str]) -> None:
        if session.current_state == "warming_up":
            self._append_state_path(state_path, "task_dispatch")
            session.current_state = "task_dispatch"
            session.public_stage = public_stage_for_state(session.current_state)
        if session.current_state in {
            "task_dispatch",
            "give_hint",
            "guided_hint",
            "step_by_step_help",
            "demo_mode",
            "off_topic_repair",
            "reengagement",
            "next_task_ready",
            "celebrate_success",
        }:
            self._append_state_path(state_path, "await_answer")
            session.current_state = "await_answer"
            session.public_stage = public_stage_for_state(session.current_state)

    def _apply_turn_state_machine(
        self,
        *,
        session: SessionState,
        current_task: SessionTaskState,
        child_input_text: str,
        resolved_signal: str,
        interpretation: TurnInterpretation | None,
        state_path: list[str],
        created_at: str,
    ) -> str:
        session.help_level_current = current_task.help_level_current
        if interpretation is not None and interpretation.safety_triggered:
            current_task.parent_note = interpretation.safety_reason or "检测到需要立即停下来的情况"
            session.current_state = "safety_hold"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            session.current_state = "abort_cleanup"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            session.status = "aborted"
            session.lifecycle_state = "ended"
            session.ended_at = created_at
            session.ended_reason = "safety_stop"
            session.current_task_id = None
            session.current_task_index = None
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            return "ended_session"

        if resolved_signal == "end_session":
            current_task.parent_note = "孩子想先停一下"
            session.current_state = "abort_cleanup"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = created_at
            session.ended_reason = "child_quit"
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            session.current_task_id = None
            session.current_task_index = None
            self._append_state_path(state_path, session.current_state)
            return "ended_session"

        if resolved_signal == "task_completed":
            if current_task.requires_self_report and not current_task.awaiting_child_confirmation:
                current_task.awaiting_child_confirmation = True
                current_task.parent_note = "等待孩子口头确认已经完成"
                session.current_state = "self_report_confirm"
                session.public_stage = public_stage_for_state(session.current_state)
                self._append_state_path(state_path, session.current_state)
                return "stayed_on_task"

            current_task.awaiting_child_confirmation = False
            current_task.status = "completed"
            current_task.completed_at = created_at
            current_task.result_code = (
                "completed_with_hint"
                if current_task.help_level_peak != "none"
                else "correct"
            )
            current_task.parent_note = (
                "系统给过提示后任务完成"
                if current_task.help_level_peak != "none"
                else "孩子自己完成了这一步"
            )
            session.completed_task_count += 1
            session.current_state = "celebrate_success"
            session.public_stage = public_stage_for_state(session.current_state)
            session.anchor_state = None
            self._append_state_path(state_path, session.current_state)
            self._advance_after_task_completion(session, created_at, state_path=state_path)
            return (
                "ended_session"
                if session.status in {"ended", "aborted"}
                else "advanced_to_next_task"
            )

        if not child_input_text.strip():
            current_task.parent_note = "孩子暂时没有回应，系统在重新招呼"
            session.reengage_count += 1
            session.current_state = "reengagement"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            return "stayed_on_task"

        anchor_keywords = collect_task_anchor_keywords(
            task_name=current_task.name,
            task_goal=current_task.goal,
            expected_child_action=current_task.expected_child_action,
        )
        if should_treat_as_off_topic(
            child_input_text=child_input_text,
            interaction_mode=interpretation.interaction_mode if interpretation else None,
            engagement_state=interpretation.engagement_state if interpretation else None,
            partial_credit=interpretation.partial_credit if interpretation else False,
            matched_completion_points=interpretation.matched_completion_points if interpretation else (),
            task_anchor_keywords=anchor_keywords,
        ):
            session.off_topic_count += 1
            session.anchor_state = "await_answer"
            current_task.parent_note = "孩子有点跑题，系统正在把话题拉回当前任务"
            session.current_state = "off_topic_repair"
            session.public_stage = public_stage_for_state(session.current_state)
            self._append_state_path(state_path, session.current_state)
            return "stayed_on_task"

        current_task.attempt_count += 1
        session.retry_count += 1
        if (
            contains_frustration_marker(child_input_text)
            and current_task.help_level_current in {"step_by_step", "demo_mode"}
        ):
            next_level = "parent_takeover"
        else:
            next_level = next_help_level(current_task.help_level_current)
        current_task.help_level_current = next_level
        current_task.help_level_peak = max_help_level(current_task.help_level_peak, next_level)
        session.help_level_current = current_task.help_level_current
        session.help_level_peak = max_help_level(session.help_level_peak, current_task.help_level_peak)

        if next_level == "light_nudge":
            session.current_state = "give_hint"
            current_task.parent_note = "系统给了一个轻提示，继续试试"
        elif next_level == "guided_hint":
            session.current_state = "guided_hint"
            current_task.parent_note = "系统给了一个关键线索"
        elif next_level == "step_by_step":
            session.current_state = "step_by_step_help"
            current_task.parent_note = "系统开始一步一步带着做"
        elif next_level == "demo_mode":
            session.current_state = "demo_mode"
            current_task.parent_note = "系统先示范一下，再请孩子模仿"
        else:
            session.current_state = "parent_interrupt_hold"
            session.anchor_state = "await_answer"
            session.status = "paused"
            current_task.parent_note = "这一环节需要家长接手一下"
        session.public_stage = public_stage_for_state(
            session.current_state,
            anchor_state=session.anchor_state,
        )
        self._append_state_path(state_path, session.current_state)
        return "stayed_on_task"

    def _normalize_assistant_reply(
        self,
        assistant_reply: AssistantTurnResult,
        current_task: SessionTaskState,
        resolved_task_signal: str,
        upcoming_task: SessionTaskState | None,
    ) -> AssistantTurnResult:
        if resolved_task_signal == "task_completed" and upcoming_task is not None and upcoming_task.assistant_led_summary:
            next_expected_action = "结束本轮"
            return AssistantTurnResult(
                prompt_version=assistant_reply.prompt_version,
                reply_text=assistant_reply.reply_text,
                guidance_type=assistant_reply.guidance_type,
                next_expected_action=next_expected_action,
                error=assistant_reply.error,
            )

        fallback_next_action = current_task.expected_child_action
        if resolved_task_signal == "task_completed":
            fallback_next_action = (
                "结束本轮"
                if upcoming_task is not None and upcoming_task.assistant_led_summary
                else upcoming_task.expected_child_action if upcoming_task else "结束本轮"
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

    def _advance_after_task_completion(
        self,
        session: SessionState,
        completed_at: str,
        *,
        state_path: list[str] | None = None,
    ) -> None:
        current_task_index = session.current_task_index
        if current_task_index is None:
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = completed_at
            session.ended_reason = "completed"
            session.current_task_id = None
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            if state_path is not None:
                self._append_state_path(state_path, "ended")
            return

        next_index = current_task_index + 1
        if next_index >= len(session.tasks):
            session.current_state = "cooling_down"
            session.public_stage = public_stage_for_state(session.current_state)
            if state_path is not None:
                self._append_state_path(state_path, "cooling_down")
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = completed_at
            session.ended_reason = "completed"
            session.current_task_index = None
            session.current_task_id = None
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            if state_path is not None:
                self._append_state_path(state_path, "ended")
            return

        next_task = session.tasks[next_index]
        if next_task.assistant_led_summary:
            next_task.status = "completed"
            next_task.activated_at = completed_at
            next_task.completed_at = completed_at
            next_task.help_level_current = "none"
            next_task.help_level_peak = "none"
            next_task.awaiting_child_confirmation = False
            next_task.result_code = "assistant_summary"
            next_task.parent_note = "系统自动完成了回站总结"
            session.completed_task_count += 1
            session.help_level_current = "none"
            session.anchor_state = None
            session.current_state = "cooling_down"
            session.public_stage = public_stage_for_state(session.current_state)
            if state_path is not None:
                self._append_state_path(state_path, "cooling_down")
            session.status = "ended"
            session.lifecycle_state = "ended"
            session.ended_at = completed_at
            session.ended_reason = "completed"
            session.current_task_index = None
            session.current_task_id = None
            session.current_state = "ended"
            session.public_stage = public_stage_for_state(session.current_state)
            if state_path is not None:
                self._append_state_path(state_path, "ended")
            return

        next_task.status = "active"
        next_task.activated_at = completed_at
        next_task.help_level_current = "none"
        next_task.awaiting_child_confirmation = False
        next_task.parent_note = "已经切到这一步，等待孩子回应"
        session.current_task_index = next_index
        session.current_task_id = next_task.task_id
        session.help_level_current = "none"
        session.anchor_state = None
        session.current_state = "next_task_ready"
        session.public_stage = public_stage_for_state(session.current_state)
        if state_path is not None:
            self._append_state_path(state_path, "next_task_ready")
            self._append_state_path(state_path, "task_dispatch")
        session.current_state = "task_dispatch"
        session.public_stage = public_stage_for_state(session.current_state)

    def _persist_sessions_locked(self) -> None:
        if self.persistence is None:
            return
        self.persistence.save_sessions(self._sessions)

    def _refresh_session_summary(self, session: SessionState) -> None:
        current_task = session.current_task()
        current_label = None
        if current_task is not None:
            current_label = current_task.parent_label or current_task.name
        session.parent_summary_short = build_parent_summary_short(
            completed_task_count=session.completed_task_count,
            public_stage=session.public_stage,
            current_task_label=current_label,
        )

    def _build_session_live_view(self, session: SessionState) -> dict[str, Any]:
        current_task = session.current_task()
        current_turn = session.current_turn()
        parent_action = build_parent_action(
            status=session.status,
            end_reason=session.ended_reason,
            help_level_current=current_task.help_level_current if current_task else session.help_level_current,
            has_recent_parent_interrupt=(
                current_turn.state_after == "parent_interrupt_hold"
                if current_turn is not None
                else False
            ),
        )
        return {
            "session_id": session.session_id,
            "header": {
                "public_stage": session.public_stage,
                "public_stage_text": public_stage_text(session.public_stage),
                "display_status": derive_display_status(
                    status=session.status,
                    public_stage=session.public_stage,
                ),
                "started_at": session.started_at,
                "ended_at": session.ended_at,
            },
            "progress": {
                "turn_count": session.turn_count,
                "completed_task_count": session.completed_task_count,
                "retry_count": session.retry_count,
            },
            "current_task": (
                {
                    "parent_label": current_task.parent_label or current_task.name,
                    "help_level_current": current_task.help_level_current,
                    "parent_note": (
                        None
                        if current_task.help_level_current == "parent_takeover"
                        else current_task.parent_note
                    ),
                    "awaiting_child_confirmation": current_task.awaiting_child_confirmation,
                }
                if current_task is not None and session.public_stage not in {"cooling_down", "ended"}
                else None
            ),
            "session_summary": {
                "parent_summary_short": session.parent_summary_short,
            },
            "parent_action": parent_action,
            "meta": {
                "projection_version": "v1",
                "generated_at": iso_now(),
            },
        }

    def _build_home_snapshot_view(self, session: SessionState) -> dict[str, Any]:
        live_view = self._build_session_live_view(session)
        return {
            "active_session": {
                "session_id": session.session_id,
                "public_stage": live_view["header"]["public_stage"],
                "public_stage_text": live_view["header"]["public_stage_text"],
                "display_status": live_view["header"]["display_status"],
                "started_at": session.started_at,
                "parent_summary_short": session.parent_summary_short,
                "completed_task_count": session.completed_task_count,
                "retry_count": session.retry_count,
            },
            "latest_report": None,
            "continue_entry": None,
            "alerts": [],
            "meta": {
                "projection_version": "v1",
                "generated_at": iso_now(),
            },
        }

    def _snapshot_for_session(self, session: SessionState) -> dict[str, Any]:
        current_task = session.current_task()
        current_turn = session.current_turn()
        is_persisted_session = session.is_persisted_session
        disclaimer = (
            "当前 Phase 6 session runtime 已接上 live 状态机字段和最小 projection。"
            "服务重启后 session 还能继续取回。"
            "脚本现在提交结构化 interpretation，不再只是最薄 task 指针。"
            if is_persisted_session
            else (
                "当前 Phase 6 session runtime 已接上 live 状态机字段和最小 projection。"
                "当前会话只保存在进程内存里，不会跨重启保留。"
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
            "session_live_view": self._build_session_live_view(session),
            "home_snapshot_view": self._build_home_snapshot_view(session),
            "viewer_context": {
                "landing_task_id": session.current_task_id,
                "landing_task_index": session.current_task_index,
                "latest_turn_id": current_turn.turn_id if current_turn else None,
                "latest_turn_index": current_turn.turn_index if current_turn else None,
                "latest_turn_task_id": current_turn.task_id if current_turn else None,
                "current_state": session.current_state,
                "public_stage": session.public_stage,
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
                "state_machine_version": session.state_machine_version,
                "current_task_index_semantics": CURRENT_TASK_INDEX_SEMANTICS_ACTIVE_POINTER,
                "current_turn_index_semantics": CURRENT_TURN_INDEX_SEMANTICS_LATEST_IN_SESSION,
                "task_signal_mode": "phase7_interpretation_or_explicit",
                "available_task_signals": sorted(VALID_TASK_SIGNALS),
                "auto_task_signal_is_heuristic": False,
                "turn_history_scope": TURN_HISTORY_SCOPE_FULL_IN_MEMORY,
                "supports": {
                    "create_session": True,
                    "get_session": True,
                    "submit_turn": True,
                    "persisted_session": is_persisted_session,
                    "session_live_view": True,
                    "home_snapshot_view": True,
                },
                "generated_at": iso_now(),
                "disclaimer": disclaimer,
            },
        }
