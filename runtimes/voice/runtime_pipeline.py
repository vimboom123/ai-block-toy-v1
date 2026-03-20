from __future__ import annotations

from input_understanding import (
    MinimalInteractionGenerator,
    QwenTaskSignalResolver,
    RuleFirstSignalResolver,
    TaskContext,
    build_signal_resolver_llm,
)
from input_understanding.interaction_provider import InteractionProviderError, build_unified_turn_provider
from input_understanding.models import SignalResolution
from phase6_bridge import Phase7BridgePackage, build_phase7_bridge_package


def run_phase7_turn_pipeline(
    *,
    child_input_text: str,
    current_task: TaskContext,
    interaction_provider: str = "qwen",
    provider_fast_timeout_seconds: float = MinimalInteractionGenerator.DEFAULT_FAST_PATH_TIMEOUT_SECONDS,
    provider_keep_trying_timeout_seconds: float = (
        MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS
    ),
    provider_keep_trying_retry_timeout_seconds: float = (
        MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS
    ),
    session_memory_summary: str | None = None,
    session_id: str | None = None,
    next_task_hint: TaskContext | None = None,
) -> Phase7BridgePackage:
    unified_provider_failed = False
    interaction_generator = MinimalInteractionGenerator(
        provider_mode=interaction_provider,
        keep_trying_timeout_seconds=provider_keep_trying_timeout_seconds,
        keep_trying_retry_timeout_seconds=provider_keep_trying_retry_timeout_seconds,
        fast_path_timeout_seconds=provider_fast_timeout_seconds,
    )

    unified_turn_provider = build_unified_turn_provider(interaction_provider)
    if unified_turn_provider is not None:
        try:
            unified_turn_draft = unified_turn_provider.generate_turn(
                child_input_text=child_input_text,
                current_task=current_task,
                session_memory_summary=session_memory_summary,
                next_task_hint=next_task_hint,
                timeout_seconds=max(provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds),
            )
            signal_resolution = _build_signal_resolution_from_unified_turn(
                child_input_text=child_input_text,
                current_task=current_task,
                draft=unified_turn_draft,
            )
            interaction_context = interaction_generator.build_context(
                child_input_text=child_input_text,
                current_task=current_task,
                signal_resolution=signal_resolution,
                session_memory_summary=session_memory_summary,
            )
            interaction_generation = interaction_generator.build_generation_from_draft(
                interaction_context=interaction_context,
                draft=unified_turn_draft,
                provider_name=unified_turn_draft.provider_name,
            )
            return build_phase7_bridge_package(
                child_input_text=child_input_text,
                current_task=current_task,
                signal_resolution=signal_resolution,
                interaction_generation=interaction_generation,
                interaction_context=interaction_context,
                session_id=session_id,
            )
        except InteractionProviderError:
            unified_provider_failed = True
        except Exception:
            unified_provider_failed = True

    signal_resolution = None
    if interaction_provider in {"qwen", "auto"} and not unified_provider_failed:
        signal_resolution = QwenTaskSignalResolver().resolve(
            child_input_text=child_input_text,
            current_task=current_task,
        )

    if signal_resolution is None:
        signal_resolver = (
            RuleFirstSignalResolver()
            if unified_provider_failed
            else RuleFirstSignalResolver(
                llm_stub=build_signal_resolver_llm(interaction_provider),
            )
        )
        signal_resolution = signal_resolver.resolve(
            child_input_text=child_input_text,
            current_task=current_task,
        )

    if unified_provider_failed:
        interaction_generator = MinimalInteractionGenerator(
            provider_mode="template",
            keep_trying_timeout_seconds=provider_keep_trying_timeout_seconds,
            keep_trying_retry_timeout_seconds=provider_keep_trying_retry_timeout_seconds,
            fast_path_timeout_seconds=provider_fast_timeout_seconds,
        )

    interaction_context, interaction_generation = interaction_generator.generate_with_context(
        child_input_text=child_input_text,
        current_task=current_task,
        signal_resolution=signal_resolution,
        session_memory_summary=session_memory_summary,
    )
    return build_phase7_bridge_package(
        child_input_text=child_input_text,
        current_task=current_task,
        signal_resolution=signal_resolution,
        interaction_generation=interaction_generation,
        interaction_context=interaction_context,
        session_id=session_id,
    )


def _build_signal_resolution_from_unified_turn(
    *,
    child_input_text: str,
    current_task: TaskContext,
    draft: object,
) -> SignalResolution:
    task_signal = str(getattr(draft, "task_signal"))
    matched_completion_points = tuple(getattr(draft, "matched_completion_points", ()) or ())
    if task_signal == "task_completed" and not matched_completion_points and current_task.completion_points:
        matched_completion_points = current_task.completion_point_labels()
    missing_completion_points = tuple(
        label for label in current_task.completion_point_labels() if label not in matched_completion_points
    )
    return SignalResolution(
        task_signal=task_signal,
        confidence=float(getattr(draft, "confidence")),
        reason=str(getattr(draft, "reason")),
        fallback_needed=task_signal == "keep_trying",
        normalized_child_text=child_input_text.strip() or "(empty)",
        partial_credit=bool(getattr(draft, "partial_credit", False)) if task_signal == "keep_trying" else False,
        matched_completion_points=matched_completion_points,
        missing_completion_points=missing_completion_points,
        engagement_state=str(getattr(draft, "engagement_state", "unknown") or "unknown"),
    )
