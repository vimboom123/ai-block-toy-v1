from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple


RUNNER_BASE_TIME = datetime(2026, 3, 17, tzinfo=timezone.utc)
SUPPORTED_ACTORS = {"system", "child", "parent"}
SUPPORTED_STEP_TYPES = {
    "session.started",
    "child.intent_recognized",
    "child.answer_incorrect",
    "child.no_response_timeout",
    "help.level_changed",
    "task.activated",
    "task.failed",
    "task.completed",
    "parent.interrupt_requested",
    "parent.resume_requested",
    "parent.end_session_requested",
    "safety.checked",
    "session.ended",
    "parent_report.generated",
}
PARENT_VISIBLE_STEP_TYPES = {
    "session.started",
    "child.no_response_timeout",
    "help.level_changed",
    "task.activated",
    "task.failed",
    "task.completed",
    "parent.interrupt_requested",
    "parent.resume_requested",
    "parent.end_session_requested",
    "safety.checked",
    "session.ended",
    "parent_report.generated",
}
HELP_LEVEL_ORDER = {
    "none": 0,
    "light_nudge": 1,
    "guided_hint": 2,
    "step_by_step": 3,
    "demo_mode": 4,
    "parent_takeover": 5,
}
CAUTION_LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
TERMINAL_ABORT_REASONS = {
    "child_quit",
    "timeout_no_input",
    "network_error",
    "no_response_timeout",
    "asr_fail_exhausted",
    "safety_stop",
    "parent_interrupted",
    "device_shutdown",
    "theme_switched",
    "system_abort",
}


@dataclass
class FixtureStep:
    step_no: int
    at: str
    at_ms: int
    actor: str
    step_type: str
    payload: Dict[str, Any]


@dataclass
class Fixture:
    id: str
    category: str
    theme_code: str
    seed_profile: Dict[str, Any]
    session_bootstrap: Dict[str, Any]
    steps: List[FixtureStep]
    expected: Dict[str, Any]
    source_path: Path


@dataclass
class DomainEvent:
    event_id: str
    session_id: str
    fixture_id: str
    seq_no: int
    step_no: int
    occurred_at: str
    offset_ms: int
    actor: str
    producer: str
    event_type: str
    task_id: Optional[str]
    correlation_id: str
    parent_visible: bool
    state_before: Optional[str]
    state_after: Optional[str]
    payload_public: Dict[str, Any]
    payload_private: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SessionAggregate:
    session_id: str
    fixture_id: str
    theme_code: str
    status: str
    current_state: str
    public_stage: str
    current_task_id: Optional[str] = None
    completed_task_count: int = 0
    retry_count: int = 0
    help_level_peak: str = "none"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    end_reason: Optional[str] = None
    safety_notice_level: str = "none"
    timeout_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskAggregate:
    task_id: str
    status: str = "pending"
    parent_label: str = ""
    attempt_count: int = 0
    help_level_current: str = "none"
    help_level_peak: str = "none"
    parent_note: Optional[str] = None
    activated_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReportAggregate:
    publish_status: str
    theme_name_snapshot: str
    duration_sec: int
    completed_task_count: int
    help_level_peak: str
    safety_notice_level: str
    achievements: List[str]
    notable_moments: List[str]
    parent_summary: str
    follow_up_suggestion: str
    generated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FixtureRunArtifacts:
    fixture: Fixture
    session: SessionAggregate
    tasks: Dict[str, TaskAggregate]
    report: Optional[ReportAggregate]
    events: List[DomainEvent]
    projections: Dict[str, Any]
    display_status_history: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture": {
                "id": self.fixture.id,
                "category": self.fixture.category,
                "theme_code": self.fixture.theme_code,
                "source_path": str(self.fixture.source_path),
            },
            "session": self.session.to_dict(),
            "tasks": {task_id: task.to_dict() for task_id, task in self.tasks.items()},
            "report": self.report.to_dict() if self.report else None,
            "events": [event.to_dict() for event in self.events],
            "projections": self.projections,
            "display_status_history": list(self.display_status_history),
        }


@dataclass
class AssertionFailure:
    path: str
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AssertionResult:
    ok: bool
    failures: List[AssertionFailure] = field(default_factory=list)


@dataclass
class RunnerContext:
    fixture: Fixture
    session: SessionAggregate
    tasks: Dict[str, TaskAggregate]
    report: Optional[ReportAggregate]
    events: List[DomainEvent]
    display_status_history: List[str]
    flags: Dict[str, Any]


def iter_fixture_paths(fixtures_dir: Path) -> List[Path]:
    return sorted(
        path
        for path in fixtures_dir.glob("*.yaml")
        if path.is_file()
    )


def load_fixture(fixture_path: Path) -> Fixture:
    document = _load_yaml_with_ruby(fixture_path)
    if not isinstance(document, dict):
        raise ValueError(f"{fixture_path} must contain a top-level mapping")

    for key in ("id", "category", "theme_code", "session_bootstrap", "steps", "expected"):
        if key not in document:
            raise ValueError(f"{fixture_path} is missing required key: {key}")

    bootstrap = document["session_bootstrap"]
    if not isinstance(bootstrap, dict):
        raise ValueError(f"{fixture_path} session_bootstrap must be a mapping")
    if "public_stage" not in bootstrap or "initial_state" not in bootstrap:
        raise ValueError(f"{fixture_path} session_bootstrap needs public_stage and initial_state")

    steps_raw = document["steps"]
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"{fixture_path} steps must be a non-empty list")

    steps: List[FixtureStep] = []
    for index, raw_step in enumerate(steps_raw, start=1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"{fixture_path} steps[{index}] must be a mapping")
        actor = str(raw_step.get("actor", ""))
        step_type = str(raw_step.get("type", ""))
        at = str(raw_step.get("at", ""))
        if actor not in SUPPORTED_ACTORS:
            raise ValueError(f"{fixture_path} steps[{index}] has unsupported actor: {actor}")
        if step_type not in SUPPORTED_STEP_TYPES:
            raise ValueError(f"{fixture_path} steps[{index}] has unsupported type: {step_type}")
        payload = raw_step.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{fixture_path} steps[{index}] payload must be a mapping when present")
        steps.append(
            FixtureStep(
                step_no=index,
                at=at,
                at_ms=_parse_offset_ms(at),
                actor=actor,
                step_type=step_type,
                payload=dict(payload),
            )
        )

    steps.sort(key=lambda step: (step.at_ms, step.step_no))

    expected = document["expected"]
    if not isinstance(expected, dict):
        raise ValueError(f"{fixture_path} expected must be a mapping")

    return Fixture(
        id=str(document["id"]),
        category=str(document["category"]),
        theme_code=str(document["theme_code"]),
        seed_profile=document.get("seed_profile", {}) if isinstance(document.get("seed_profile", {}), dict) else {},
        session_bootstrap=dict(bootstrap),
        steps=steps,
        expected=dict(expected),
        source_path=fixture_path,
    )


def run_fixture(fixture_path: Path) -> FixtureRunArtifacts:
    fixture = load_fixture(fixture_path)
    session = SessionAggregate(
        session_id=_session_id(fixture.id),
        fixture_id=fixture.id,
        theme_code=fixture.theme_code,
        status=str(fixture.session_bootstrap.get("status", "active")),
        current_state=str(fixture.session_bootstrap["initial_state"]),
        public_stage=str(fixture.session_bootstrap["public_stage"]),
    )
    context = RunnerContext(
        fixture=fixture,
        session=session,
        tasks={},
        report=None,
        events=[],
        display_status_history=[],
        flags={
            "had_reengagement": False,
            "had_parent_interrupt": False,
            "had_parent_resume": False,
            "had_hint_completion": False,
            "had_safety_warn": False,
            "last_task_id": None,
        },
    )

    seq_no = 1
    for step in fixture.steps:
        occurred_at = _iso_at_offset(step.at_ms)
        correlation_id = f"corr_{session.session_id}_{step.step_no:03d}"
        task_id = _extract_task_id(step, context)
        state_before = context.session.current_state
        event = DomainEvent(
            event_id=_event_id(session.session_id, seq_no),
            session_id=session.session_id,
            fixture_id=fixture.id,
            seq_no=seq_no,
            step_no=step.step_no,
            occurred_at=occurred_at,
            offset_ms=step.at_ms,
            actor=step.actor,
            producer=_producer_for(step.step_type),
            event_type=step.step_type,
            task_id=task_id,
            correlation_id=correlation_id,
            parent_visible=step.step_type in PARENT_VISIBLE_STEP_TYPES,
            state_before=state_before,
            state_after=None,
            payload_public=_public_payload_for_step(step, task_id),
            payload_private=dict(step.payload),
        )
        context.events.append(event)
        _reduce_event(context, event)
        context.display_status_history.append(_display_status(context.session))
        seq_no += 1

        next_state, rule_id, reason = _next_state_for_event(event, state_before)
        if rule_id:
            transition_event = DomainEvent(
                event_id=_event_id(session.session_id, seq_no),
                session_id=session.session_id,
                fixture_id=fixture.id,
                seq_no=seq_no,
                step_no=step.step_no,
                occurred_at=occurred_at,
                offset_ms=step.at_ms,
                actor="system",
                producer="state_driver",
                event_type="state.transition_applied",
                task_id=task_id,
                correlation_id=correlation_id,
                parent_visible=False,
                state_before=state_before,
                state_after=next_state,
                payload_public={},
                payload_private={
                    "rule_id": rule_id,
                    "reason": reason,
                    "trigger_event": event.event_type,
                },
            )
            context.events.append(transition_event)
            context.session.current_state = next_state
            context.display_status_history.append(_display_status(context.session))
            seq_no += 1

    projections = _build_projections(context)
    return FixtureRunArtifacts(
        fixture=fixture,
        session=context.session,
        tasks=context.tasks,
        report=context.report,
        events=context.events,
        projections=projections,
        display_status_history=context.display_status_history,
    )


def run_fixtures(fixtures_dir: Path) -> List[Tuple[FixtureRunArtifacts, AssertionResult]]:
    results: List[Tuple[FixtureRunArtifacts, AssertionResult]] = []
    for fixture_path in iter_fixture_paths(fixtures_dir):
        artifacts = run_fixture(fixture_path)
        results.append((artifacts, assert_golden(artifacts)))
    return results


def assert_golden(artifacts: FixtureRunArtifacts) -> AssertionResult:
    expected = artifacts.fixture.expected
    failures: List[AssertionFailure] = []
    session = artifacts.session
    report = artifacts.report
    projections = artifacts.projections

    if "terminal_session_status" in expected and session.status != expected["terminal_session_status"]:
        failures.append(
            AssertionFailure(
                path="session.status",
                expected=expected["terminal_session_status"],
                actual=session.status,
                message="terminal_session_status mismatch",
            )
        )

    if "terminal_public_stage" in expected and session.public_stage != expected["terminal_public_stage"]:
        failures.append(
            AssertionFailure(
                path="session.public_stage",
                expected=expected["terminal_public_stage"],
                actual=session.public_stage,
                message="terminal_public_stage mismatch",
            )
        )

    if "completed_task_count" in expected and session.completed_task_count != int(expected["completed_task_count"]):
        failures.append(
            AssertionFailure(
                path="session.completed_task_count",
                expected=int(expected["completed_task_count"]),
                actual=session.completed_task_count,
                message="completed_task_count mismatch",
            )
        )

    if "retry_count" in expected and session.retry_count != int(expected["retry_count"]):
        failures.append(
            AssertionFailure(
                path="session.retry_count",
                expected=int(expected["retry_count"]),
                actual=session.retry_count,
                message="retry_count mismatch",
            )
        )

    if "help_level_peak" in expected and session.help_level_peak != str(expected["help_level_peak"]):
        failures.append(
            AssertionFailure(
                path="session.help_level_peak",
                expected=str(expected["help_level_peak"]),
                actual=session.help_level_peak,
                message="help_level_peak mismatch",
            )
        )

    if "end_reason" in expected and session.end_reason != expected["end_reason"]:
        failures.append(
            AssertionFailure(
                path="session.end_reason",
                expected=expected["end_reason"],
                actual=session.end_reason,
                message="end_reason mismatch",
            )
        )

    if "report_publish_status" in expected:
        actual_publish_status = report.publish_status if report else None
        if actual_publish_status != expected["report_publish_status"]:
            failures.append(
                AssertionFailure(
                    path="report.publish_status",
                    expected=expected["report_publish_status"],
                    actual=actual_publish_status,
                    message="report_publish_status mismatch",
                )
            )

    if "display_status" in expected:
        _assert_display_status_expectation(
            failures,
            expected["display_status"],
            projections["live"]["header"]["display_status"],
            artifacts.display_status_history,
        )

    projection_assert = expected.get("projection_assert", {})
    if isinstance(projection_assert, dict):
        _assert_live_projection(failures, projection_assert.get("live"), artifacts)
        _assert_timeline_projection(failures, projection_assert.get("timeline"), artifacts)
        _assert_report_projection(failures, projection_assert.get("report"), artifacts)
        _assert_home_projection(failures, projection_assert.get("home"), artifacts)

    return AssertionResult(ok=not failures, failures=failures)


def dump_artifacts(artifacts: FixtureRunArtifacts, dump_dir: Path) -> None:
    target = dump_dir / artifacts.fixture.id
    target.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("events", [event.to_dict() for event in artifacts.events]),
        ("session", artifacts.session.to_dict()),
        ("tasks", {task_id: task.to_dict() for task_id, task in artifacts.tasks.items()}),
        ("report", artifacts.report.to_dict() if artifacts.report else None),
        ("live", artifacts.projections["live"]),
        ("timeline", artifacts.projections["timeline"]),
        ("report_view", artifacts.projections["report"]),
        ("home", artifacts.projections["home"]),
    ):
        (target / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _reduce_event(context: RunnerContext, event: DomainEvent) -> None:
    session = context.session
    task_id = event.task_id
    if task_id:
        context.flags["last_task_id"] = task_id

    if event.event_type == "session.started":
        session.status = "active"
        session.started_at = session.started_at or event.occurred_at
        return

    if event.event_type == "task.activated":
        if not task_id:
            raise ValueError("task.activated requires task_id")
        task = context.tasks.get(task_id) or TaskAggregate(
            task_id=task_id,
            parent_label=_humanize_task_id(task_id),
        )
        task.status = "active"
        task.parent_label = task.parent_label or _humanize_task_id(task_id)
        task.parent_note = "当前正在进行这个任务"
        task.activated_at = task.activated_at or event.occurred_at
        task.finished_at = None
        task.help_level_current = "none"
        context.tasks[task_id] = task
        session.current_task_id = task_id
        session.public_stage = "active"
        return

    if event.event_type == "help.level_changed":
        next_level = _next_help_level_from_payload(event.payload_private)
        if next_level:
            session.help_level_peak = _max_help_level(session.help_level_peak, next_level)
            session.public_stage = "receiving_hint"
            task = _current_task(context, task_id)
            if task:
                task.help_level_current = next_level
                task.help_level_peak = _max_help_level(task.help_level_peak, next_level)
                task.parent_note = _parent_note_for_help_level(next_level)
                context.tasks[task.task_id] = task
        return

    if event.event_type == "child.no_response_timeout":
        session.timeout_count += 1
        context.flags["had_reengagement"] = True
        next_level = _timeout_escalation_level(event.payload_private)
        if next_level:
            session.help_level_peak = _max_help_level(session.help_level_peak, next_level)
            task = _current_task(context, task_id)
            if task:
                task.help_level_current = next_level
                task.help_level_peak = _max_help_level(task.help_level_peak, next_level)
                task.parent_note = _parent_note_for_help_level(next_level)
                context.tasks[task.task_id] = task
        return

    if event.event_type == "task.failed":
        if not task_id:
            raise ValueError("task.failed requires task_id")
        task = context.tasks.get(task_id) or TaskAggregate(
            task_id=task_id,
            parent_label=_humanize_task_id(task_id),
        )
        task.status = "failed"
        task.attempt_count = max(task.attempt_count + 1, 1)
        task.finished_at = event.occurred_at
        task.last_failure_reason = str(event.payload_private.get("reason") or "")
        task.parent_note = "这个任务暂时没完成"
        context.tasks[task_id] = task
        session.current_task_id = task_id
        return

    if event.event_type == "parent.interrupt_requested":
        session.status = "paused"
        context.flags["had_parent_interrupt"] = True
        task = _current_task(context, task_id)
        if task:
            task.help_level_current = "parent_takeover"
            task.help_level_peak = _max_help_level(task.help_level_peak, "parent_takeover")
            task.parent_note = "等待家长介入"
            context.tasks[task.task_id] = task
        return

    if event.event_type == "parent.resume_requested":
        session.status = "active"
        session.retry_count += 1
        context.flags["had_parent_resume"] = True
        task = _current_task(context, task_id)
        if task:
            if task.status == "failed":
                task.status = "active"
                task.finished_at = None
            task.help_level_current = "none"
            task.parent_note = "家长介入后恢复，继续尝试"
            context.tasks[task.task_id] = task
            session.current_task_id = task.task_id
        return

    if event.event_type == "parent.end_session_requested":
        session.status = "paused"
        return

    if event.event_type == "task.completed":
        if not task_id:
            raise ValueError("task.completed requires task_id")
        task = context.tasks.get(task_id) or TaskAggregate(
            task_id=task_id,
            parent_label=_humanize_task_id(task_id),
        )
        if task.status != "completed":
            session.completed_task_count += 1
        task.status = "completed"
        task.attempt_count = max(task.attempt_count, 1)
        task.finished_at = event.occurred_at
        task.parent_note = _completion_parent_note(task, context.flags)
        context.tasks[task_id] = task
        session.current_task_id = None
        session.public_stage = "active"
        if task.help_level_peak in {"light_nudge", "guided_hint"}:
            context.flags["had_hint_completion"] = True
        return

    if event.event_type == "safety.checked":
        result = str(event.payload_private.get("result") or "")
        level = str(event.payload_private.get("safety_notice_level") or "none")
        session.safety_notice_level = _max_caution_level(session.safety_notice_level, level)
        if result == "warn":
            context.flags["had_safety_warn"] = True
        return

    if event.event_type == "session.ended":
        end_reason = event.payload_private.get("end_reason")
        session.end_reason = str(end_reason) if end_reason is not None else None
        session.status = "aborted" if session.end_reason in TERMINAL_ABORT_REASONS else "ended"
        session.public_stage = "ended"
        session.current_task_id = None
        session.ended_at = event.occurred_at
        return

    if event.event_type == "parent_report.generated":
        requested_publish_status = str(event.payload_private.get("publish_status") or "")
        context.report = _build_report(context, requested_publish_status, event.occurred_at)
        return


def _build_projections(context: RunnerContext) -> Dict[str, Any]:
    live = _build_live_projection(context)
    timeline = _build_timeline_projection(context)
    report = _build_report_projection(context)
    home = _build_home_projection(context, live, report)
    return {
        "live": live,
        "timeline": timeline,
        "report": report,
        "home": home,
    }


def _build_live_projection(context: RunnerContext) -> Dict[str, Any]:
    session = context.session
    display_status = _display_status(session)
    current_task = None
    if display_status not in {"ended", "aborted"}:
        task = _current_task(context, session.current_task_id)
        if task:
            current_task = {
                "task_id": task.task_id,
                "parent_label": task.parent_label,
                "help_level_current": task.help_level_current,
                "parent_note": task.parent_note,
            }

    need_parent_intervention = (
        session.status == "paused"
        or session.status == "aborted"
        or bool(current_task and current_task["help_level_current"] == "parent_takeover")
    )

    return {
        "header": {
            "public_stage": session.public_stage,
            "display_status": display_status,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
        },
        "progress": {
            "completed_task_count": session.completed_task_count,
            "retry_count": session.retry_count,
            "timeout_count": session.timeout_count,
        },
        "current_task": current_task,
        "parent_action": {
            "need_parent_intervention": need_parent_intervention,
        },
    }


def _build_timeline_projection(context: RunnerContext) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for event in context.events:
        if not event.parent_visible:
            continue
        items.append(
            {
                "seq_no": event.seq_no,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "display_text": event.payload_public.get("display_text") or _timeline_text(event),
                "related_task_id": event.task_id,
                "source_event_types": [event.event_type],
            }
        )
    return {
        "items": items,
        "count": len(items),
    }


def _build_report_projection(context: RunnerContext) -> Optional[Dict[str, Any]]:
    report = context.report
    if not report:
        return None
    return {
        "summary": {
            "theme_name_snapshot": report.theme_name_snapshot,
            "duration_sec": report.duration_sec,
            "completed_task_count": report.completed_task_count,
            "help_level_peak": report.help_level_peak,
            "publish_status": report.publish_status,
            "end_reason": context.session.end_reason,
        },
        "highlights": {
            "achievements": list(report.achievements),
            "notable_moments": list(report.notable_moments),
        },
        "parent_text": {
            "parent_summary": report.parent_summary,
            "follow_up_suggestion": report.follow_up_suggestion,
        },
        "safety": {
            "safety_notice_level": report.safety_notice_level,
        },
    }


def _build_home_projection(
    context: RunnerContext,
    live: Dict[str, Any],
    report_view: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    session = context.session
    latest_summary = None
    if report_view:
        latest_summary = report_view["parent_text"]["parent_summary"]
    elif session.status == "aborted":
        latest_summary = "本轮已提前结束。"
    elif session.status == "ended":
        latest_summary = "本轮已结束。"

    continue_entry = None
    if session.status == "aborted" and session.end_reason != "safety_stop":
        continue_entry = {
            "theme_code": session.theme_code,
            "entry_reason_text": "这一轮提前结束了，可以稍后再继续。",
        }

    alerts: List[Dict[str, Any]] = []
    if session.end_reason == "safety_stop":
        alerts.append(
            {
                "type": "safety_stop",
                "severity": "high",
                "text": "本轮因安全原因提前结束。",
            }
        )

    return {
        "latest_session_status": session.status,
        "latest_theme_name": session.theme_code,
        "latest_summary": latest_summary,
        "active_session": live if live["header"]["display_status"] in {"active", "paused"} else None,
        "continue_entry": continue_entry,
        "alerts": alerts,
    }


def _build_report(
    context: RunnerContext,
    requested_publish_status: str,
    generated_at: str,
) -> ReportAggregate:
    session = context.session
    tasks = list(context.tasks.values())
    ended_cleanly = session.status == "ended"
    publish_status = requested_publish_status or ("published" if ended_cleanly else "partial")
    achievements: List[str] = []
    notable_moments: List[str] = []

    if context.flags["had_parent_resume"]:
        achievements.append("家长介入后恢复")
        notable_moments.append("家长接手后又顺利回到任务")
    if context.flags["had_reengagement"]:
        achievements.append("中途分心后重新投入")
        notable_moments.append("系统把注意力重新拉回任务")
    if any(task.help_level_peak in {"light_nudge", "guided_hint"} for task in tasks):
        achievements.append("在提示下完成")
        notable_moments.append("系统给出关键线索后继续完成")
    if session.completed_task_count and not achievements:
        achievements.append("顺利完成")
        notable_moments.append("本轮按正常节奏完成任务")

    parent_summary = _parent_summary_for_context(context, publish_status)
    follow_up = _follow_up_suggestion_for_context(context, publish_status)

    return ReportAggregate(
        publish_status=publish_status,
        theme_name_snapshot=session.theme_code,
        duration_sec=_duration_seconds(session.started_at, session.ended_at),
        completed_task_count=session.completed_task_count,
        help_level_peak=session.help_level_peak,
        safety_notice_level=session.safety_notice_level,
        achievements=achievements,
        notable_moments=notable_moments,
        parent_summary=parent_summary,
        follow_up_suggestion=follow_up,
        generated_at=generated_at,
    )


def _parent_summary_for_context(context: RunnerContext, publish_status: str) -> str:
    session = context.session
    if publish_status == "partial":
        if session.end_reason == "safety_stop":
            return "本轮已因安全原因提前结束，以下是目前能确认的部分总结。"
        if session.end_reason == "parent_interrupted":
            return "本轮在家长接管后已提前结束，以下是目前能确认的部分总结。"
        if session.end_reason == "network_error":
            return "本轮因连接问题已提前结束，以下是目前能确认的部分总结。"
        if session.end_reason == "system_abort":
            return "本轮因系统原因已提前结束，以下是目前能确认的部分总结。"
        if session.end_reason == "no_response_timeout":
            return "本轮因长时间没有继续互动已提前结束，以下是目前能确认的部分总结。"
        return "本轮已提前结束，以下是目前能确认的部分总结。"

    if context.flags["had_parent_resume"]:
        return "家长介入后恢复，孩子又重新跟上了任务节奏。"
    if context.flags["had_reengagement"]:
        return "孩子中途分心后重新投入，并把当前任务继续做完了。"
    if any(task.help_level_peak in {"light_nudge", "guided_hint"} for task in context.tasks.values()):
        return "孩子在提示下完成了关键步骤，整体节奏保持住了。"
    return f"本轮顺利完成了 {session.completed_task_count} 个任务。"


def _follow_up_suggestion_for_context(context: RunnerContext, publish_status: str) -> str:
    if publish_status == "partial":
        if context.session.end_reason == "safety_stop":
            return "先处理当前情况，确认安全后再决定要不要继续。"
        return "先确认孩子状态，再决定要不要稍后继续。"
    return "可以和孩子一起回顾刚刚是怎么完成这一步的。"


def _assert_live_projection(
    failures: List[AssertionFailure],
    expected_live: Any,
    artifacts: FixtureRunArtifacts,
) -> None:
    if not isinstance(expected_live, dict):
        return
    live = artifacts.projections["live"]
    if "display_status" in expected_live and live["header"]["display_status"] != expected_live["display_status"]:
        failures.append(
            AssertionFailure(
                path="projections.live.header.display_status",
                expected=expected_live["display_status"],
                actual=live["header"]["display_status"],
                message="live display_status mismatch",
            )
        )
    if expected_live.get("current_task", "__missing__") is None and live["current_task"] is not None:
        failures.append(
            AssertionFailure(
                path="projections.live.current_task",
                expected=None,
                actual=live["current_task"],
                message="live current_task should be null",
            )
        )
    if "need_parent_intervention" in expected_live:
        actual_flag = live["parent_action"]["need_parent_intervention"]
        if actual_flag != bool(expected_live["need_parent_intervention"]):
            failures.append(
                AssertionFailure(
                    path="projections.live.parent_action.need_parent_intervention",
                    expected=bool(expected_live["need_parent_intervention"]),
                    actual=actual_flag,
                    message="live parent intervention mismatch",
                )
            )
    if "parent_note_contains" in expected_live:
        needle = str(expected_live["parent_note_contains"])
        haystacks = []
        if live["current_task"] and live["current_task"].get("parent_note"):
            haystacks.append(str(live["current_task"]["parent_note"]))
        haystacks.extend(
            str(task.parent_note)
            for task in artifacts.tasks.values()
            if task.parent_note
        )
        if not any(needle in haystack for haystack in haystacks):
            failures.append(
                AssertionFailure(
                    path="projections.live.parent_note_contains",
                    expected=needle,
                    actual=haystacks,
                    message="live/task parent_note did not include expected text",
                )
            )
        return


def _assert_timeline_projection(
    failures: List[AssertionFailure],
    expected_timeline: Any,
    artifacts: FixtureRunArtifacts,
) -> None:
    if not isinstance(expected_timeline, dict):
        return
    actual_event_types = [item["event_type"] for item in artifacts.projections["timeline"]["items"]]
    for event_type in expected_timeline.get("required_events", []):
        if event_type not in actual_event_types:
            failures.append(
                AssertionFailure(
                    path=f"projections.timeline.required_events.{event_type}",
                    expected=True,
                    actual=False,
                    message="required timeline event missing",
                )
            )


def _assert_report_projection(
    failures: List[AssertionFailure],
    expected_report: Any,
    artifacts: FixtureRunArtifacts,
) -> None:
    if not isinstance(expected_report, dict):
        return
    report = artifacts.projections["report"] or {}
    if "safety_notice_level" in expected_report:
        actual = ((report.get("safety") or {}).get("safety_notice_level"))
        if actual != expected_report["safety_notice_level"]:
            failures.append(
                AssertionFailure(
                    path="projections.report.safety.safety_notice_level",
                    expected=expected_report["safety_notice_level"],
                    actual=actual,
                    message="report safety_notice_level mismatch",
                )
            )
    if "publish_status" in expected_report:
        actual = ((report.get("summary") or {}).get("publish_status"))
        if actual != expected_report["publish_status"]:
            failures.append(
                AssertionFailure(
                    path="projections.report.summary.publish_status",
                    expected=expected_report["publish_status"],
                    actual=actual,
                    message="report publish_status mismatch",
                )
            )
    if "achievement_contains" in expected_report:
        needle = str(expected_report["achievement_contains"])
        achievements = " | ".join((report.get("highlights") or {}).get("achievements", []))
        if needle not in achievements:
            failures.append(
                AssertionFailure(
                    path="projections.report.highlights.achievements",
                    expected=needle,
                    actual=achievements,
                    message="report achievements missing expected text",
                )
            )
    if "summary_contains" in expected_report:
        needle = str(expected_report["summary_contains"])
        summary = ((report.get("parent_text") or {}).get("parent_summary")) or ""
        if needle not in summary:
            failures.append(
                AssertionFailure(
                    path="projections.report.parent_text.parent_summary",
                    expected=needle,
                    actual=summary,
                    message="report summary missing expected text",
                )
            )
    if "parent_summary_contains" in expected_report:
        needle = str(expected_report["parent_summary_contains"])
        summary = ((report.get("parent_text") or {}).get("parent_summary")) or ""
        if needle not in summary:
            failures.append(
                AssertionFailure(
                    path="projections.report.parent_text.parent_summary",
                    expected=needle,
                    actual=summary,
                    message="report parent_summary missing expected text",
                )
            )


def _assert_home_projection(
    failures: List[AssertionFailure],
    expected_home: Any,
    artifacts: FixtureRunArtifacts,
) -> None:
    if not isinstance(expected_home, dict):
        return
    home = artifacts.projections["home"]
    if "latest_session_status" in expected_home and home["latest_session_status"] != expected_home["latest_session_status"]:
        failures.append(
            AssertionFailure(
                path="projections.home.latest_session_status",
                expected=expected_home["latest_session_status"],
                actual=home["latest_session_status"],
                message="home latest_session_status mismatch",
            )
        )
    if expected_home.get("continue_entry", "__missing__") is None and home["continue_entry"] is not None:
        failures.append(
            AssertionFailure(
                path="projections.home.continue_entry",
                expected=None,
                actual=home["continue_entry"],
                message="home continue_entry should be null",
            )
        )
    if "latest_summary_contains" in expected_home:
        needle = str(expected_home["latest_summary_contains"])
        summary = home.get("latest_summary") or ""
        if needle not in summary:
            failures.append(
                AssertionFailure(
                    path="projections.home.latest_summary",
                    expected=needle,
                    actual=summary,
                    message="home latest_summary missing expected text",
                )
            )


def _assert_display_status_expectation(
    failures: List[AssertionFailure],
    expected_value: Any,
    actual_final_status: str,
    status_history: List[str],
) -> None:
    if expected_value == "active_then_ended":
        if not status_history or "active" not in status_history or status_history[-1] != "ended":
            failures.append(
                AssertionFailure(
                    path="display_status_history",
                    expected="contains active and ends with ended",
                    actual=status_history,
                    message="display_status history did not follow active_then_ended",
                )
            )
        return
    if actual_final_status != expected_value:
        failures.append(
            AssertionFailure(
                path="projections.live.header.display_status",
                expected=expected_value,
                actual=actual_final_status,
                message="display_status mismatch",
            )
        )


def _current_task(context: RunnerContext, preferred_task_id: Optional[str]) -> Optional[TaskAggregate]:
    if preferred_task_id and preferred_task_id in context.tasks:
        return context.tasks[preferred_task_id]
    current_task_id = context.session.current_task_id
    if current_task_id and current_task_id in context.tasks:
        return context.tasks[current_task_id]
    last_task_id = context.flags.get("last_task_id")
    if last_task_id and last_task_id in context.tasks:
        return context.tasks[last_task_id]
    return None


def _extract_task_id(step: FixtureStep, context: RunnerContext) -> Optional[str]:
    task_id = step.payload.get("task_id")
    if isinstance(task_id, str):
        return task_id
    if context.session.current_task_id:
        return context.session.current_task_id
    last_task_id = context.flags.get("last_task_id")
    return str(last_task_id) if last_task_id else None


def _next_state_for_event(event: DomainEvent, current_state: str) -> Tuple[str, Optional[str], Optional[str]]:
    if event.event_type == "session.started":
        return current_state, "rule_session_started", "session_bootstrap"
    if event.event_type == "task.activated":
        return "doing_task", "rule_task_activated", "task_activated"
    if event.event_type == "help.level_changed":
        next_level = _next_help_level_from_payload(event.payload_private) or "light_nudge"
        return "receiving_hint", "rule_help_level_changed", next_level
    if event.event_type == "child.no_response_timeout":
        return "reengagement", "rule_no_response_timeout", "timeout"
    if event.event_type == "task.failed":
        return "task_failed", "rule_task_failed", str(event.payload_private.get("reason") or "task_failed")
    if event.event_type == "parent.interrupt_requested":
        return "parent_interrupt_hold", "rule_parent_interrupt_requested", "parent_interrupt"
    if event.event_type == "parent.resume_requested":
        return "doing_task", "rule_parent_resume_requested", "parent_resume"
    if event.event_type == "parent.end_session_requested":
        return "abort_cleanup", "rule_parent_end_session_requested", str(event.payload_private.get("reason") or "parent_end")
    if event.event_type == "task.completed":
        return "celebrating", "rule_task_completed", "task_completed"
    if event.event_type == "safety.checked" and event.payload_private.get("result") == "stop":
        return "safety_hold", "rule_safety_stop", "safety_stop"
    if event.event_type == "session.ended":
        return "ended", "rule_session_ended", str(event.payload_private.get("end_reason") or "completed")
    return current_state, None, None


def _producer_for(step_type: str) -> str:
    if step_type in {"session.started", "session.ended", "parent_report.generated"}:
        return "system"
    if step_type in {"parent.interrupt_requested", "parent.resume_requested", "parent.end_session_requested"}:
        return "parent_proxy"
    if step_type in {"child.no_response_timeout", "safety.checked"}:
        return "state_driver"
    return "runner"


def _public_payload_for_step(step: FixtureStep, task_id: Optional[str]) -> Dict[str, Any]:
    if step.step_type == "session.started":
        return {"display_text": "互动开始"}
    if step.step_type == "task.activated":
        return {
            "display_text": "进入新任务",
            "task_id": task_id,
            "parent_label": _humanize_task_id(task_id or "任务"),
        }
    if step.step_type == "task.failed":
        return {"display_text": "这个任务暂时没完成", "task_id": task_id}
    if step.step_type == "task.completed":
        return {"display_text": "完成一个任务", "task_id": task_id}
    if step.step_type == "help.level_changed":
        return {"display_text": _parent_note_for_help_level(_next_help_level_from_payload(step.payload) or "light_nudge")}
    if step.step_type == "child.no_response_timeout":
        return {"display_text": "这一步等待有点久了", "task_id": task_id}
    if step.step_type == "parent.interrupt_requested":
        return {"display_text": "家长已接管当前会话"}
    if step.step_type == "parent.resume_requested":
        return {"display_text": "家长已恢复会话"}
    if step.step_type == "parent.end_session_requested":
        return {"display_text": "家长决定结束本轮会话"}
    if step.step_type == "safety.checked":
        result = step.payload.get("result")
        return {"display_text": "触发安全停止" if result == "stop" else "系统给了一个安全提醒"}
    if step.step_type == "session.ended":
        return {"display_text": "本轮已结束"}
    if step.step_type == "parent_report.generated":
        return {"display_text": "家长报告已生成"}
    return {"display_text": step.step_type}


def _timeline_text(event: DomainEvent) -> str:
    return event.payload_public.get("display_text") or event.event_type


def _next_help_level_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("to", "to_level"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _timeout_escalation_level(payload: Dict[str, Any]) -> Optional[str]:
    value = payload.get("escalation_to")
    if isinstance(value, str) and value in HELP_LEVEL_ORDER:
        return value
    return None


def _parent_note_for_help_level(level: str) -> str:
    if level == "guided_hint":
        return "系统已给出关键线索"
    if level == "light_nudge":
        return "系统轻提醒后继续"
    if level == "parent_takeover":
        return "等待家长介入"
    return "系统已切换到提示模式"


def _completion_parent_note(task: TaskAggregate, flags: Dict[str, Any]) -> str:
    if flags.get("had_parent_resume"):
        return "家长介入后恢复，任务继续完成"
    if flags.get("had_reengagement") and task.help_level_peak == "light_nudge":
        return "中途分心后重新投入并完成"
    if task.help_level_peak == "guided_hint":
        return "系统已给出关键线索，任务完成"
    if task.help_level_peak == "light_nudge":
        return "系统轻提醒后顺利完成"
    return "已顺利完成这个任务"


def _humanize_task_id(task_id: str) -> str:
    match = re.match(r"task_(\d+)", task_id)
    if match:
        return f"任务 {match.group(1)}"
    return task_id.replace("_", " ")


def _display_status(session: SessionAggregate) -> str:
    if session.status == "aborted":
        return "aborted"
    if session.status == "ended" or session.public_stage == "ended":
        return "ended"
    if session.status == "paused":
        return "paused"
    return "active"


def _max_help_level(current: str, incoming: str) -> str:
    current_rank = HELP_LEVEL_ORDER.get(current, 0)
    incoming_rank = HELP_LEVEL_ORDER.get(incoming, 0)
    return incoming if incoming_rank > current_rank else current


def _max_caution_level(current: str, incoming: str) -> str:
    current_rank = CAUTION_LEVEL_ORDER.get(current, 0)
    incoming_rank = CAUTION_LEVEL_ORDER.get(incoming, 0)
    return incoming if incoming_rank > current_rank else current


def _load_yaml_with_ruby(path: Path) -> Dict[str, Any]:
    script = (
        "require 'yaml'; "
        "require 'json'; "
        "document = YAML.load_file(ARGV[0]); "
        "puts JSON.generate(document)"
    )
    completed = subprocess.run(
        ["ruby", "-e", script, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Failed to parse YAML via ruby for {path}: {detail}")
    return json.loads(completed.stdout)


def _parse_offset_ms(value: str) -> int:
    match = re.fullmatch(r"(\d+)(ms|s|m|h)", value.strip())
    if not match:
        raise ValueError(f"Unsupported logical offset: {value}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return amount
    if unit == "s":
        return amount * 1000
    if unit == "m":
        return amount * 60 * 1000
    return amount * 60 * 60 * 1000


def _iso_at_offset(offset_ms: int) -> str:
    timestamp = RUNNER_BASE_TIME + timedelta(milliseconds=offset_ms)
    return timestamp.isoformat().replace("+00:00", "Z")


def _duration_seconds(started_at: Optional[str], ended_at: Optional[str]) -> int:
    if not started_at or not ended_at:
        return 0
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    delta = end - start
    return max(int(delta.total_seconds()), 0)


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _session_id(fixture_id: str) -> str:
    return f"ses_{re.sub(r'[^a-zA-Z0-9]+', '_', fixture_id).strip('_')}"


def _event_id(session_id: str, seq_no: int) -> str:
    return f"evt_{session_id}_{seq_no:04d}"
