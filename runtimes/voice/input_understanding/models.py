from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

VALID_TASK_SIGNALS = ("keep_trying", "task_completed", "end_session")
VALID_ENGAGEMENT_STATES = (
    "engaged",
    "curious",
    "playful",
    "distracted",
    "frustrated",
    "withdrawing",
    "unknown",
)
VALID_INTERACTION_MODES = (
    "acknowledge_and_redirect",
    "warm_redirect",
    "gentle_retry",
    "celebrate_completion",
    "graceful_end",
    "emotional_soothing",
    "playful_probe",
)
VALID_EMOTION_TONES = ("playful", "warm", "excited", "calm", "encouraging", "soothing")
VALID_REDIRECT_STRENGTHS = ("none", "soft", "medium", "strong")
VALID_COMPLETION_MATCH_MODES = ("any", "all")
VALID_GENERATION_SOURCES = ("template_fallback", "llm_provider")


def _ensure_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True)
class CompletionPoint:
    label: str
    keywords: tuple[str, ...]

    def __post_init__(self) -> None:
        label = _ensure_non_empty(self.label, "label")
        keywords = tuple(keyword.strip() for keyword in self.keywords if keyword.strip())
        if not keywords:
            raise ValueError("keywords must contain at least one non-empty value")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "keywords", keywords)

    @classmethod
    def parse(cls, raw_spec: str) -> "CompletionPoint":
        raw_value = _ensure_non_empty(raw_spec, "completion_point")
        label_part, separator, keyword_part = raw_value.partition(":")
        if not separator:
            return cls(label=raw_value, keywords=(raw_value,))

        label = _ensure_non_empty(label_part, "completion_point label")
        keywords = tuple(
            chunk.strip()
            for chunk in keyword_part.replace("|", ",").split(",")
            if chunk.strip()
        )
        return cls(label=label, keywords=keywords or (label,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    task_name: str
    task_goal: str
    expected_child_action: str
    completion_points: tuple[CompletionPoint, ...] = ()
    completion_match_mode: str = "any"
    scene_context: str | None = None
    scene_style: str = "playful_companion"
    allowed_signals: tuple[str, ...] = VALID_TASK_SIGNALS

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _ensure_non_empty(self.task_id, "task_id"))
        object.__setattr__(self, "task_name", _ensure_non_empty(self.task_name, "task_name"))
        object.__setattr__(self, "task_goal", _ensure_non_empty(self.task_goal, "task_goal"))
        object.__setattr__(
            self,
            "expected_child_action",
            _ensure_non_empty(self.expected_child_action, "expected_child_action"),
        )
        object.__setattr__(self, "scene_style", _ensure_non_empty(self.scene_style, "scene_style"))
        completion_points = tuple(self.completion_points)
        object.__setattr__(self, "completion_points", completion_points)
        if self.completion_match_mode not in VALID_COMPLETION_MATCH_MODES:
            allowed_modes = ", ".join(VALID_COMPLETION_MATCH_MODES)
            raise ValueError(f"completion_match_mode must be one of: {allowed_modes}")
        allowed_signals = tuple(signal.strip() for signal in self.allowed_signals if signal.strip())
        if not allowed_signals:
            raise ValueError("allowed_signals must contain at least one value")
        for signal in allowed_signals:
            if signal not in VALID_TASK_SIGNALS:
                allowed = ", ".join(VALID_TASK_SIGNALS)
                raise ValueError(f"Unsupported allowed_signal '{signal}'. Allowed: {allowed}")
        object.__setattr__(self, "allowed_signals", allowed_signals)

    def required_completion_count(self) -> int:
        if not self.completion_points:
            return 0
        if self.completion_match_mode == "all":
            return len(self.completion_points)
        return 1

    def completion_point_labels(self) -> tuple[str, ...]:
        return tuple(point.label for point in self.completion_points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_goal": self.task_goal,
            "expected_child_action": self.expected_child_action,
            "completion_match_mode": self.completion_match_mode,
            "completion_points": [point.to_dict() for point in self.completion_points],
            "scene_context": self.scene_context,
            "scene_style": self.scene_style,
            "allowed_signals": list(self.allowed_signals),
        }


@dataclass(frozen=True)
class SignalResolution:
    task_signal: str
    confidence: float
    reason: str
    fallback_needed: bool
    normalized_child_text: str
    partial_credit: bool = False
    matched_completion_points: tuple[str, ...] = ()
    missing_completion_points: tuple[str, ...] = ()
    engagement_state: str = "unknown"

    def __post_init__(self) -> None:
        if self.task_signal not in VALID_TASK_SIGNALS:
            allowed = ", ".join(VALID_TASK_SIGNALS)
            raise ValueError(f"task_signal must be one of: {allowed}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "reason", _ensure_non_empty(self.reason, "reason"))
        object.__setattr__(
            self,
            "normalized_child_text",
            _ensure_non_empty(self.normalized_child_text, "normalized_child_text"),
        )
        if not isinstance(self.partial_credit, bool):
            raise ValueError("partial_credit must be a boolean")
        if self.engagement_state not in VALID_ENGAGEMENT_STATES:
            allowed = ", ".join(VALID_ENGAGEMENT_STATES)
            raise ValueError(f"engagement_state must be one of: {allowed}")
        object.__setattr__(
            self,
            "matched_completion_points",
            tuple(point for point in self.matched_completion_points if point),
        )
        object.__setattr__(
            self,
            "missing_completion_points",
            tuple(point for point in self.missing_completion_points if point),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_signal": self.task_signal,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "fallback_needed": self.fallback_needed,
            "partial_credit": self.partial_credit,
            "normalized_child_text": self.normalized_child_text,
            "matched_completion_points": list(self.matched_completion_points),
            "missing_completion_points": list(self.missing_completion_points),
            "engagement_state": self.engagement_state,
        }


@dataclass(frozen=True)
class InteractionGeneration:
    reply_text: str
    interaction_mode: str
    emotion_tone: str
    redirect_strength: str
    acknowledged_child_point: str | None = None
    followup_question: str | None = None
    generation_source: str = "template_fallback"
    provider_name: str | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        reply_text = _ensure_non_empty(self.reply_text, "reply_text")
        object.__setattr__(self, "reply_text", reply_text)
        if self.interaction_mode not in VALID_INTERACTION_MODES:
            allowed = ", ".join(VALID_INTERACTION_MODES)
            raise ValueError(f"interaction_mode must be one of: {allowed}")
        if self.emotion_tone not in VALID_EMOTION_TONES:
            allowed = ", ".join(VALID_EMOTION_TONES)
            raise ValueError(f"emotion_tone must be one of: {allowed}")
        if self.redirect_strength not in VALID_REDIRECT_STRENGTHS:
            allowed = ", ".join(VALID_REDIRECT_STRENGTHS)
            raise ValueError(f"redirect_strength must be one of: {allowed}")
        if self.generation_source not in VALID_GENERATION_SOURCES:
            allowed = ", ".join(VALID_GENERATION_SOURCES)
            raise ValueError(f"generation_source must be one of: {allowed}")
        if self.acknowledged_child_point is not None:
            object.__setattr__(
                self,
                "acknowledged_child_point",
                _ensure_non_empty(self.acknowledged_child_point, "acknowledged_child_point"),
            )
        if self.followup_question is not None:
            object.__setattr__(
                self,
                "followup_question",
                _ensure_non_empty(self.followup_question, "followup_question"),
            )
        if self.provider_name is not None:
            object.__setattr__(
                self,
                "provider_name",
                _ensure_non_empty(self.provider_name, "provider_name"),
            )
        if self.fallback_reason is not None:
            object.__setattr__(
                self,
                "fallback_reason",
                _ensure_non_empty(self.fallback_reason, "fallback_reason"),
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reply_text": self.reply_text,
            "interaction_mode": self.interaction_mode,
            "emotion_tone": self.emotion_tone,
            "redirect_strength": self.redirect_strength,
            "generation_source": self.generation_source,
        }
        if self.acknowledged_child_point is not None:
            payload["acknowledged_child_point"] = self.acknowledged_child_point
        if self.followup_question is not None:
            payload["followup_question"] = self.followup_question
        if self.provider_name is not None:
            payload["provider_name"] = self.provider_name
        if self.fallback_reason is not None:
            payload["fallback_reason"] = self.fallback_reason
        return payload


@dataclass(frozen=True)
class InteractionContext:
    task_name: str
    child_input_text: str
    normalized_child_text: str
    task_signal: str
    engagement_state: str
    partial_credit: bool
    matched_completion_points: tuple[str, ...]
    missing_completion_points: tuple[str, ...]
    interaction_goal: str
    scene_style: str
    redirect_strength: str
    expected_child_action: str
    interaction_mode: str
    emotion_tone: str
    task_goal: str | None = None
    completion_points: tuple[CompletionPoint, ...] = ()
    preferred_acknowledged_child_point: str | None = None
    preferred_followup_question: str | None = None
    recent_turn_summary: str | None = None
    rule_reason: str | None = None
    scene_context: str | None = None
    session_memory: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_name", _ensure_non_empty(self.task_name, "task_name"))
        object.__setattr__(
            self,
            "child_input_text",
            _ensure_non_empty(self.child_input_text, "child_input_text"),
        )
        object.__setattr__(
            self,
            "normalized_child_text",
            _ensure_non_empty(self.normalized_child_text, "normalized_child_text"),
        )
        if self.task_signal not in VALID_TASK_SIGNALS:
            allowed = ", ".join(VALID_TASK_SIGNALS)
            raise ValueError(f"task_signal must be one of: {allowed}")
        if self.engagement_state not in VALID_ENGAGEMENT_STATES:
            allowed = ", ".join(VALID_ENGAGEMENT_STATES)
            raise ValueError(f"engagement_state must be one of: {allowed}")
        if not isinstance(self.partial_credit, bool):
            raise ValueError("partial_credit must be a boolean")
        if self.redirect_strength not in VALID_REDIRECT_STRENGTHS:
            allowed = ", ".join(VALID_REDIRECT_STRENGTHS)
            raise ValueError(f"redirect_strength must be one of: {allowed}")
        if self.interaction_mode not in VALID_INTERACTION_MODES:
            allowed = ", ".join(VALID_INTERACTION_MODES)
            raise ValueError(f"interaction_mode must be one of: {allowed}")
        if self.emotion_tone not in VALID_EMOTION_TONES:
            allowed = ", ".join(VALID_EMOTION_TONES)
            raise ValueError(f"emotion_tone must be one of: {allowed}")
        object.__setattr__(
            self,
            "interaction_goal",
            _ensure_non_empty(self.interaction_goal, "interaction_goal"),
        )
        object.__setattr__(self, "scene_style", _ensure_non_empty(self.scene_style, "scene_style"))
        object.__setattr__(
            self,
            "expected_child_action",
            _ensure_non_empty(self.expected_child_action, "expected_child_action"),
        )
        if self.task_goal is not None:
            object.__setattr__(self, "task_goal", _ensure_non_empty(self.task_goal, "task_goal"))
        object.__setattr__(self, "completion_points", tuple(self.completion_points))
        object.__setattr__(
            self,
            "matched_completion_points",
            tuple(point for point in self.matched_completion_points if point),
        )
        object.__setattr__(
            self,
            "missing_completion_points",
            tuple(point for point in self.missing_completion_points if point),
        )
        for field_name in (
            "preferred_acknowledged_child_point",
            "preferred_followup_question",
            "recent_turn_summary",
            "rule_reason",
            "scene_context",
            "session_memory",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _ensure_non_empty(value, field_name))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_name": self.task_name,
            "child_input_text": self.child_input_text,
            "normalized_child_text": self.normalized_child_text,
            "task_signal": self.task_signal,
            "engagement_state": self.engagement_state,
            "partial_credit": self.partial_credit,
            "matched_completion_points": list(self.matched_completion_points),
            "missing_completion_points": list(self.missing_completion_points),
            "interaction_goal": self.interaction_goal,
            "scene_style": self.scene_style,
            "redirect_strength": self.redirect_strength,
            "expected_child_action": self.expected_child_action,
            "interaction_mode": self.interaction_mode,
            "emotion_tone": self.emotion_tone,
        }
        if self.task_goal is not None:
            payload["task_goal"] = self.task_goal
        if self.completion_points:
            payload["completion_points"] = [point.to_dict() for point in self.completion_points]
        if self.preferred_acknowledged_child_point is not None:
            payload["preferred_acknowledged_child_point"] = self.preferred_acknowledged_child_point
        if self.preferred_followup_question is not None:
            payload["preferred_followup_question"] = self.preferred_followup_question
        if self.recent_turn_summary is not None:
            payload["recent_turn_summary"] = self.recent_turn_summary
        if self.rule_reason is not None:
            payload["rule_reason"] = self.rule_reason
        if self.scene_context is not None:
            payload["scene_context"] = self.scene_context
        if self.session_memory is not None:
            payload["session_memory"] = self.session_memory
        return payload

    def to_prompt_payload(self) -> dict[str, Any]:
        task_payload: dict[str, Any] = {
            "name": self.task_name,
            "signal": self.task_signal,
            "goal": self.interaction_goal,
            "expected_action": self.expected_child_action,
            "scene_style": self.scene_style,
        }
        if self.task_goal is not None:
            task_payload["task_goal"] = self.task_goal
        if self.completion_points:
            task_payload["completion_points"] = [point.to_dict() for point in self.completion_points]
        if self.scene_context is not None:
            task_payload["scene_context"] = self.scene_context
        if self.session_memory is not None:
            task_payload["session_memory"] = self.session_memory
        if self.partial_credit:
            task_payload["progress"] = "partial_credit"
        if self.matched_completion_points:
            task_payload["done"] = list(self.matched_completion_points)
        if self.missing_completion_points:
            task_payload["need"] = list(self.missing_completion_points)

        child_payload: dict[str, Any] = {
            "said": self.child_input_text,
            "normalized": self.normalized_child_text,
        }
        if self.engagement_state not in {"engaged", "unknown"}:
            child_payload["state"] = self.engagement_state
        if self.recent_turn_summary is not None:
            child_payload["summary"] = self.recent_turn_summary

        reply_payload: dict[str, Any] = {
            "mode": self.interaction_mode,
            "tone": self.emotion_tone,
        }
        if self.redirect_strength != "none":
            reply_payload["redirect"] = self.redirect_strength
        if self.preferred_acknowledged_child_point is not None:
            reply_payload["ack"] = self.preferred_acknowledged_child_point
        if self.preferred_followup_question is not None:
            reply_payload["ask"] = self.preferred_followup_question

        return {
            "task": task_payload,
            "child": child_payload,
            "reply": reply_payload,
        }


@dataclass(frozen=True)
class PrototypeTurnEnvelope:
    child_input_text: str
    current_task: TaskContext
    signal_resolution: SignalResolution
    interaction_generation: InteractionGeneration
    interaction_context: InteractionContext | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "child_input_text": self.child_input_text,
            "current_task": self.current_task.to_dict(),
            "signal_resolution": self.signal_resolution.to_dict(),
            "interaction_generation": self.interaction_generation.to_dict(),
        }
        if self.interaction_context is not None:
            payload["interaction_context"] = self.interaction_context.to_dict()
        return payload


def completion_ratio(task_context: TaskContext, matched_count: int) -> float:
    total = len(task_context.completion_points)
    if total == 0:
        return 0.0
    if matched_count <= 0:
        return 0.0
    if task_context.completion_match_mode == "all":
        return matched_count / total
    return min(matched_count / max(task_context.required_completion_count(), 1), 1.0)


def partial_completion_threshold(task_context: TaskContext) -> int:
    total = len(task_context.completion_points)
    if total <= 1:
        return total
    return ceil(total / 2)
