from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

VALID_PUBLIC_STAGES = (
    "warming_up",
    "doing_task",
    "receiving_hint",
    "celebrating",
    "cooling_down",
    "ended",
)

VALID_HELP_LEVELS = (
    "none",
    "light_nudge",
    "guided_hint",
    "step_by_step",
    "demo_mode",
    "parent_takeover",
)

HELP_LEVEL_ORDER = {
    "none": 0,
    "light_nudge": 1,
    "guided_hint": 2,
    "step_by_step": 3,
    "demo_mode": 4,
    "parent_takeover": 5,
}

PUBLIC_STAGE_TEXT = {
    "warming_up": "正在热身进入状态",
    "doing_task": "正在完成任务",
    "receiving_hint": "正在接收提示",
    "celebrating": "刚完成一个小目标",
    "cooling_down": "正在收尾",
    "ended": "本轮已结束",
}

STATE_MACHINE_VERSION = "ai_block_toy_state_machine_v1"

STATE_TO_PUBLIC_STAGE = {
    "session_bootstrap": "warming_up",
    "warming_up": "warming_up",
    "task_dispatch": "doing_task",
    "await_answer": "doing_task",
    "interpret_input": "doing_task",
    "self_report_confirm": "doing_task",
    "give_hint": "receiving_hint",
    "guided_hint": "receiving_hint",
    "step_by_step_help": "receiving_hint",
    "demo_mode": "receiving_hint",
    "off_topic_repair": "receiving_hint",
    "reengagement": "receiving_hint",
    "celebrate_success": "celebrating",
    "next_task_ready": "doing_task",
    "cooling_down": "cooling_down",
    "safety_hold": "ended",
    "parent_interrupt_hold": "doing_task",
    "abort_cleanup": "ended",
    "ended": "ended",
}

FRUSTRATION_MARKERS = (
    "不知道",
    "我不知道",
    "嗯不知道",
    "不晓得",
    "不会",
    "我不会",
    "想不出来",
    "想不起来",
    "不会做",
    "你告诉我",
)


def contains_frustration_marker(text: str) -> bool:
    normalized_text = text.strip()
    if not normalized_text:
        return False
    return any(marker in normalized_text for marker in FRUSTRATION_MARKERS)


@dataclass(frozen=True)
class TurnInterpretation:
    reason: str | None = None
    confidence: float | None = None
    engagement_state: str | None = None
    safety_triggered: bool = False
    safety_reason: str | None = None
    partial_credit: bool = False
    matched_completion_points: tuple[str, ...] = ()
    missing_completion_points: tuple[str, ...] = ()
    interaction_mode: str | None = None
    emotion_tone: str | None = None
    redirect_strength: str | None = None
    followup_question: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "TurnInterpretation | None":
        if not payload:
            return None
        raw_confidence = payload.get("confidence")
        confidence = (
            min(max(float(raw_confidence), 0.0), 1.0)
            if isinstance(raw_confidence, (int, float))
            else None
        )
        matched = payload.get("matched_completion_points")
        missing = payload.get("missing_completion_points")
        return cls(
            reason=str(payload.get("reason")).strip() if payload.get("reason") is not None else None,
            confidence=confidence,
            engagement_state=str(payload.get("engagement_state")).strip()
            if payload.get("engagement_state") is not None
            else None,
            safety_triggered=bool(payload.get("safety_triggered")),
            safety_reason=str(payload.get("safety_reason")).strip()
            if payload.get("safety_reason") is not None
            else None,
            partial_credit=bool(payload.get("partial_credit")),
            matched_completion_points=_coerce_string_tuple(matched),
            missing_completion_points=_coerce_string_tuple(missing),
            interaction_mode=str(payload.get("interaction_mode")).strip()
            if payload.get("interaction_mode") is not None
            else None,
            emotion_tone=str(payload.get("emotion_tone")).strip()
            if payload.get("emotion_tone") is not None
            else None,
            redirect_strength=str(payload.get("redirect_strength")).strip()
            if payload.get("redirect_strength") is not None
            else None,
            followup_question=str(payload.get("followup_question")).strip()
            if payload.get("followup_question") is not None
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "partial_credit": self.partial_credit,
            "matched_completion_points": list(self.matched_completion_points),
            "missing_completion_points": list(self.missing_completion_points),
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.confidence is not None:
            payload["confidence"] = round(self.confidence, 4)
        if self.engagement_state:
            payload["engagement_state"] = self.engagement_state
        if self.safety_triggered:
            payload["safety_triggered"] = True
        if self.safety_reason:
            payload["safety_reason"] = self.safety_reason
        if self.interaction_mode:
            payload["interaction_mode"] = self.interaction_mode
        if self.emotion_tone:
            payload["emotion_tone"] = self.emotion_tone
        if self.redirect_strength:
            payload["redirect_strength"] = self.redirect_strength
        if self.followup_question:
            payload["followup_question"] = self.followup_question
        return payload


def public_stage_for_state(state: str, *, anchor_state: str | None = None) -> str:
    if state == "parent_interrupt_hold" and anchor_state:
        return public_stage_for_state(anchor_state)
    return STATE_TO_PUBLIC_STAGE.get(state, "doing_task")


def public_stage_text(public_stage: str | None) -> str | None:
    if not public_stage:
        return None
    return PUBLIC_STAGE_TEXT.get(public_stage)


def derive_display_status(*, status: str, public_stage: str) -> str:
    if status == "aborted":
        return "aborted"
    if status == "paused":
        return "paused"
    if public_stage == "ended" or status == "ended":
        return "ended"
    return "active"


def max_help_level(current: str, incoming: str) -> str:
    current_rank = HELP_LEVEL_ORDER.get(current, 0)
    incoming_rank = HELP_LEVEL_ORDER.get(incoming, 0)
    return incoming if incoming_rank > current_rank else current


def next_help_level(current: str) -> str:
    if current == "none":
        return "light_nudge"
    if current == "light_nudge":
        return "guided_hint"
    if current == "guided_hint":
        return "step_by_step"
    if current == "step_by_step":
        return "demo_mode"
    return "parent_takeover"


def build_parent_summary_short(
    *,
    completed_task_count: int,
    public_stage: str,
    current_task_label: str | None,
) -> str:
    if current_task_label and public_stage not in {"cooling_down", "ended"}:
        return f"已完成 {completed_task_count} 个任务，当前任务：{current_task_label}"
    stage_text = public_stage_text(public_stage)
    if stage_text:
        return f"已完成 {completed_task_count} 个任务，当前{stage_text}"
    return f"已完成 {completed_task_count} 个任务"


def build_parent_action(
    *,
    status: str,
    end_reason: str | None,
    help_level_current: str,
    has_recent_parent_interrupt: bool,
) -> dict[str, Any]:
    if status == "aborted" and end_reason == "safety_stop":
        return {
            "need_parent_intervention": True,
            "intervention_reason_text": "本轮已因安全原因提前结束。",
            "suggested_action_text": "先安抚孩子，再看一下系统提醒。",
        }
    if help_level_current == "parent_takeover":
        return {
            "need_parent_intervention": True,
            "intervention_reason_text": "这一环节需要家长接手一下。",
            "suggested_action_text": "先到孩子身边看一眼，再决定继续还是结束。",
        }
    if status == "paused" or has_recent_parent_interrupt:
        return {
            "need_parent_intervention": True,
            "intervention_reason_text": "当前流程已暂停，等待家长处理。",
            "suggested_action_text": "确认孩子状态后，再选择继续。",
        }
    if status == "aborted":
        return {
            "need_parent_intervention": True,
            "intervention_reason_text": "本轮已提前结束，建议家长看一下当前情况。",
            "suggested_action_text": "先确认孩子状态，再决定要不要重新开始。",
        }
    return {
        "need_parent_intervention": False,
        "intervention_reason_text": None,
        "suggested_action_text": None,
    }


def collect_task_anchor_keywords(
    *,
    task_name: str,
    task_goal: str,
    expected_child_action: str,
    completion_points: Sequence[dict[str, Any]] | Sequence[Any] | None = None,
) -> tuple[str, ...]:
    keywords: list[str] = []
    for text in (task_name, task_goal, expected_child_action):
        keywords.extend(_split_keywords(text))
    for point in completion_points or ():
        label = getattr(point, "label", None)
        if isinstance(point, dict):
            label = point.get("label")
            point_keywords = point.get("keywords") or ()
        else:
            point_keywords = getattr(point, "keywords", ())
        if label:
            keywords.extend(_split_keywords(str(label)))
        for keyword in point_keywords:
            keywords.extend(_split_keywords(str(keyword)))
    deduped: list[str] = []
    for keyword in keywords:
        if keyword not in deduped:
            deduped.append(keyword)
    return tuple(deduped)


def should_treat_as_off_topic(
    *,
    child_input_text: str,
    interaction_mode: str | None,
    engagement_state: str | None,
    partial_credit: bool,
    matched_completion_points: Sequence[str],
    task_anchor_keywords: Sequence[str],
) -> bool:
    if not child_input_text.strip():
        return False
    if partial_credit or matched_completion_points:
        return False
    normalized_text = child_input_text.strip()
    if contains_frustration_marker(normalized_text):
        return False
    if engagement_state in {"distracted"}:
        return True
    if interaction_mode not in {"warm_redirect", "acknowledge_and_redirect", "playful_probe"}:
        return False
    if any(keyword and keyword in normalized_text for keyword in task_anchor_keywords):
        return False
    return True


def _split_keywords(text: str) -> list[str]:
    normalized = (
        text.replace("，", " ")
        .replace("。", " ")
        .replace("：", " ")
        .replace(":", " ")
        .replace("/", " ")
        .replace("、", " ")
        .replace("(", " ")
        .replace(")", " ")
    )
    return [
        chunk.strip()
        for chunk in normalized.split()
        if chunk.strip() and len(chunk.strip()) <= 12
    ]


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Iterable):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return tuple(result)
    return ()
