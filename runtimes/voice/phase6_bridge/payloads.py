from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from input_understanding.models import (
    InteractionContext,
    InteractionGeneration,
    PrototypeTurnEnvelope,
    SignalResolution,
    TaskContext,
)


@dataclass(frozen=True)
class Phase6TurnPayload:
    child_input_text: str
    task_signal: str
    assistant_reply_text: str | None = None
    assistant_guidance_type: str | None = None
    assistant_prompt_version: str | None = None
    assistant_next_expected_action: str | None = None
    signal_reason: str | None = None
    signal_confidence: float | None = None
    engagement_state: str | None = None
    safety_triggered: bool | None = None
    safety_reason: str | None = None
    partial_credit: bool | None = None
    matched_completion_points: tuple[str, ...] = ()
    missing_completion_points: tuple[str, ...] = ()
    interaction_mode: str | None = None
    emotion_tone: str | None = None
    redirect_strength: str | None = None
    followup_question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "child_input_text": self.child_input_text,
            "task_signal": self.task_signal,
        }
        if self.assistant_reply_text is not None:
            payload["assistant_reply_text"] = self.assistant_reply_text
        if self.assistant_guidance_type is not None:
            payload["assistant_guidance_type"] = self.assistant_guidance_type
        if self.assistant_prompt_version is not None:
            payload["assistant_prompt_version"] = self.assistant_prompt_version
        if self.assistant_next_expected_action is not None:
            payload["assistant_next_expected_action"] = self.assistant_next_expected_action
        if self.signal_reason is not None:
            payload["signal_reason"] = self.signal_reason
        if self.signal_confidence is not None:
            payload["signal_confidence"] = round(self.signal_confidence, 4)
        if self.engagement_state is not None:
            payload["engagement_state"] = self.engagement_state
        if self.safety_triggered is not None:
            payload["safety_triggered"] = self.safety_triggered
        if self.safety_reason is not None:
            payload["safety_reason"] = self.safety_reason
        if self.partial_credit is not None:
            payload["partial_credit"] = self.partial_credit
        if self.matched_completion_points:
            payload["matched_completion_points"] = list(self.matched_completion_points)
        if self.missing_completion_points:
            payload["missing_completion_points"] = list(self.missing_completion_points)
        if self.interaction_mode is not None:
            payload["interaction_mode"] = self.interaction_mode
        if self.emotion_tone is not None:
            payload["emotion_tone"] = self.emotion_tone
        if self.redirect_strength is not None:
            payload["redirect_strength"] = self.redirect_strength
        if self.followup_question is not None:
            payload["followup_question"] = self.followup_question
        return payload


@dataclass(frozen=True)
class Phase7BridgePackage:
    child_input_text: str
    current_task: TaskContext
    signal_resolution: SignalResolution
    interaction_generation: InteractionGeneration
    phase6_turn_payload: Phase6TurnPayload
    interaction_context: InteractionContext | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        envelope = PrototypeTurnEnvelope(
            child_input_text=self.child_input_text,
            current_task=self.current_task,
            signal_resolution=self.signal_resolution,
            interaction_generation=self.interaction_generation,
            interaction_context=self.interaction_context,
        ).to_dict()
        envelope["phase6_turn_payload"] = self.phase6_turn_payload.to_dict()
        if self.session_id is not None:
            envelope["session_id"] = self.session_id
        return envelope


def build_phase6_turn_payload(
    *,
    child_input_text: str,
    current_task: TaskContext,
    signal_resolution: SignalResolution,
    interaction_generation: InteractionGeneration,
) -> Phase6TurnPayload:
    if signal_resolution.task_signal == "task_completed":
        guidance_type = "confirmation"
    elif signal_resolution.task_signal == "end_session":
        guidance_type = "confirmation"
    elif signal_resolution.partial_credit:
        guidance_type = "repair"
    else:
        guidance_type = "action"

    return Phase6TurnPayload(
        child_input_text=child_input_text,
        task_signal=signal_resolution.task_signal,
        assistant_reply_text=interaction_generation.reply_text,
        assistant_guidance_type=guidance_type,
        assistant_prompt_version="phase7_voice_runtime_v1",
        assistant_next_expected_action=current_task.expected_child_action,
        signal_reason=signal_resolution.reason,
        signal_confidence=signal_resolution.confidence,
        engagement_state=signal_resolution.engagement_state,
        partial_credit=signal_resolution.partial_credit,
        matched_completion_points=signal_resolution.matched_completion_points,
        missing_completion_points=signal_resolution.missing_completion_points,
        interaction_mode=interaction_generation.interaction_mode,
        emotion_tone=interaction_generation.emotion_tone,
        redirect_strength=interaction_generation.redirect_strength,
        followup_question=interaction_generation.followup_question,
    )


def build_phase7_bridge_package(
    *,
    child_input_text: str,
    current_task: TaskContext,
    signal_resolution: SignalResolution,
    interaction_generation: InteractionGeneration,
    interaction_context: InteractionContext | None = None,
    session_id: str | None = None,
) -> Phase7BridgePackage:
    phase6_turn_payload = build_phase6_turn_payload(
        child_input_text=child_input_text,
        current_task=current_task,
        signal_resolution=signal_resolution,
        interaction_generation=interaction_generation,
    )
    return Phase7BridgePackage(
        child_input_text=child_input_text,
        current_task=current_task,
        signal_resolution=signal_resolution,
        interaction_generation=interaction_generation,
        interaction_context=interaction_context,
        phase6_turn_payload=phase6_turn_payload,
        session_id=session_id,
    )
