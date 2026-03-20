from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .interaction_provider import (
    InteractionProviderError,
    ProviderRequestOptions,
    build_interaction_provider,
    describe_provider_failure,
)
from .models import InteractionContext, InteractionGeneration, SignalResolution, TaskContext

MECHANICAL_REPLY_MARKERS = ("你来告诉我", "你来试试", "请回答", "跟我说", "请你说")
DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS = 2.0  # shorter first wait to keep replies snappy
DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS = 0.0  # single-attempt by default; fall back immediately on failure
DEFAULT_FAST_PATH_TIMEOUT_SECONDS = 1.8

FIRE_STATION_FOLLOWUP_HINTS: dict[str, tuple[str, ...]] = {
    "fs_001": (
        "这次一开始最先提醒大家的是哪一样呀？",
        "故事一开始，你觉得先该看哪里呀？",
        "你先找到这次故事最先冒出来的线索，好不好？",
    ),
    "fs_002": (
        "这次警报更像是从哪一块传来的呀？",
        "你觉得先该判断哪一座屋子或哪一边出事了？",
        "是谁把这条警情先送到指挥中心的呀？",
    ),
    "fs_003": (
        "这一步先让谁动起来呀？",
        "你想先摆动哪一个角色或载具出发？",
        "现在最该先动的是谁，还是哪块积木？",
    ),
    "fs_004": (
        "现在最需要再看清的是哪一点呀？",
        "你觉得现在最关键的是位置、路线，还是哪样东西被卡住了？",
        "这会儿最该先看清的是哪边更急，或者哪样东西更靠前要处理呀？",
    ),
    "fs_005": (
        "现在先动哪块积木去处理呀？",
        "你准备先把谁摆过去，或者先做哪个动作？",
        "这一步你想先开门、出车、接应，还是先把东西送过去？",
    ),
    "fs_006": (
        "这一步我来帮你把刚才的消防故事收一下尾，好不好？",
        "我们现在一起听系统把这次出动和救援顺一遍。",
        "这轮快收尾了，我来讲讲刚才是谁提醒、谁又动起来了。",
    ),
}

FIRE_STATION_TASK_ANCHOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fs_001": ("提醒", "铃铛", "线索", "哪里", "先看", "先找", "先响", "亮了", "入口"),
    "fs_002": ("接警", "哪一块", "哪一边", "哪座屋子", "谁提醒", "警情来源"),
    "fs_003": ("先动", "先出发", "先摆", "角色", "载具", "集合"),
    "fs_004": ("关键", "路线", "位置", "靠近", "哪边", "更急", "卡住"),
    "fs_005": ("动作", "摆过去", "送过去", "开门", "出车", "接应", "整理", "处理"),
    "fs_006": ("系统总结", "回站总结", "收尾", "刚才", "谁提醒", "谁动起来"),
}

FIRE_STATION_TASK_DRIFT_MARKERS: dict[str, tuple[str, ...]] = {
    "fs_001": ("最先提醒", "先该看哪里", "最先冒出来的线索", "故事入口在哪", "先找到故事线索"),
    "fs_002": ("从哪一块传来", "先该判断哪一座屋子", "谁把警情送到指挥中心"),
    "fs_003": ("先让谁动起来", "先摆动哪一个角色或载具", "最该先动的是谁"),
    "fs_004": ("最需要再看清的是哪一点", "最关键的是位置路线还是哪样东西卡住了", "哪边更急或者哪样东西更靠前要处理"),
    "fs_005": ("先动哪块积木去处理", "先把谁摆过去", "先开门出车接应还是送东西过去"),
    "fs_006": ("我来帮你把刚才的消防故事收一下尾", "一起听系统把这次出动和救援顺一遍", "这轮快收尾了"),
}


def build_task_followup_question(current_task: TaskContext, *, seed: str | None = None) -> str:
    resolved_seed = seed or f"{current_task.task_id}:followup"
    scene_task_question = MinimalInteractionGenerator._build_scene_task_followup_question(
        current_task,
        seed=f"{resolved_seed}:scene-task",
    )
    if scene_task_question is not None:
        return scene_task_question

    action_phrase = MinimalInteractionGenerator._extract_action_phrase(current_task.expected_child_action)
    action_question = MinimalInteractionGenerator._build_action_question(
        action_phrase,
        seed=f"{resolved_seed}:action",
    )
    if action_question is not None:
        return action_question

    completion_point_question = MinimalInteractionGenerator._build_completion_point_question(
        current_task,
        seed=f"{resolved_seed}:completion",
    )
    if completion_point_question is not None:
        return completion_point_question

    subject = MinimalInteractionGenerator._extract_subject(action_phrase)
    if subject is not None:
        return MinimalInteractionGenerator._pick_variant(
            (
                f"那{subject}现在要做什么呀？",
                f"你觉得{subject}接下来会怎么做呢？",
                f"那{subject}这会儿是在忙什么呀？",
            ),
            f"{resolved_seed}:subject",
        )

    return MinimalInteractionGenerator._pick_variant(
        (
            "那你觉得这一步该怎么说呀？",
            "我们就看现在这件事，你会怎么讲呢？",
            "先把这一步说出来，好不好？",
        ),
        f"{resolved_seed}:generic",
    )


@dataclass(frozen=True)
class InteractionPlan:
    interaction_mode: str
    emotion_tone: str
    redirect_strength: str
    acknowledged_child_point: str | None = None
    followup_question: str | None = None
    completion_point: str | None = None


class MinimalInteractionGenerator:
    DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS = DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS
    DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS = DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS
    DEFAULT_FAST_PATH_TIMEOUT_SECONDS = DEFAULT_FAST_PATH_TIMEOUT_SECONDS

    def __init__(
        self,
        *,
        provider: Any | None = None,
        provider_mode: str = "qwen",
        keep_trying_timeout_seconds: float = DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS,
        keep_trying_retry_timeout_seconds: float = DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS,
        fast_path_timeout_seconds: float = DEFAULT_FAST_PATH_TIMEOUT_SECONDS,
    ):
        if provider_mode not in {"qwen", "minimax", "template", "auto", "ark_doubao"}:
            raise ValueError("provider_mode must be one of: qwen, minimax, template, auto, ark_doubao")

        self.keep_trying_timeout_seconds = self._validate_timeout_seconds(
            keep_trying_timeout_seconds,
            field_name="keep_trying_timeout_seconds",
        )
        self.keep_trying_retry_timeout_seconds = self._validate_retry_timeout_seconds(
            keep_trying_retry_timeout_seconds,
            field_name="keep_trying_retry_timeout_seconds",
        )
        self.fast_path_timeout_seconds = self._validate_timeout_seconds(
            fast_path_timeout_seconds,
            field_name="fast_path_timeout_seconds",
        )
        if (
            self.keep_trying_retry_timeout_seconds > 0
            and self.keep_trying_retry_timeout_seconds < self.keep_trying_timeout_seconds
        ):
            raise ValueError(
                "keep_trying_retry_timeout_seconds must be greater than or equal to keep_trying_timeout_seconds"
            )

        self.provider_mode = provider_mode
        if provider is not None:
            self.provider = provider
        else:
            self.provider = build_interaction_provider(provider_mode)

    def generate(
        self,
        *,
        child_input_text: str,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        session_memory_summary: str | None = None,
    ) -> InteractionGeneration:
        _, interaction_generation = self.generate_with_context(
            child_input_text=child_input_text,
            current_task=current_task,
            signal_resolution=signal_resolution,
            session_memory_summary=session_memory_summary,
        )
        return interaction_generation

    def generate_with_context(
        self,
        *,
        child_input_text: str,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        session_memory_summary: str | None = None,
    ) -> tuple[InteractionContext, InteractionGeneration]:
        interaction_context = self.build_context(
            child_input_text=child_input_text,
            current_task=current_task,
            signal_resolution=signal_resolution,
            session_memory_summary=session_memory_summary,
        )

        fallback_reason: str | None = None
        if self.provider is not None:
            provider_generation, fallback_reason = self._generate_with_provider(
                current_task=current_task,
                signal_resolution=signal_resolution,
                interaction_context=interaction_context,
            )
            if provider_generation is not None:
                return interaction_context, provider_generation

        return interaction_context, self._render_template_interaction(
            interaction_context=interaction_context,
            fallback_reason=fallback_reason,
        )

    def build_context(
        self,
        *,
        child_input_text: str,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        session_memory_summary: str | None = None,
    ) -> InteractionContext:
        interaction_plan = self._build_plan(
            child_input_text=child_input_text,
            current_task=current_task,
            signal_resolution=signal_resolution,
        )
        return self._build_interaction_context(
            child_input_text=child_input_text,
            current_task=current_task,
            signal_resolution=signal_resolution,
            interaction_plan=interaction_plan,
            session_memory_summary=session_memory_summary,
        )

    def build_generation_from_draft(
        self,
        *,
        interaction_context: InteractionContext,
        draft: Any,
        provider_name: str | None = None,
    ) -> InteractionGeneration:
        resolved_provider_name = provider_name or getattr(draft, "provider_name", None)
        if resolved_provider_name is None and self.provider is not None:
            resolved_provider_name = getattr(self.provider, "provider_name", "custom_provider")

        reply_text = self._normalize_reply(getattr(draft, "reply_text"))
        draft_followup_question = getattr(draft, "followup_question", None)
        if draft_followup_question and self._looks_off_task_followup_question(
            followup_question=draft_followup_question,
            interaction_context=interaction_context,
        ):
            draft_followup_question = None
        followup_question = (
            draft_followup_question
            or interaction_context.preferred_followup_question
        )
        fallback_reason: str | None = None
        reply_needs_realignment = (
            self._looks_mismatched_task_reply(
                reply_text=reply_text,
                followup_question=followup_question,
                interaction_context=interaction_context,
            )
            if resolved_provider_name == "qwen_unified"
            else self._looks_off_task_reply(
                reply_text=reply_text,
                followup_question=followup_question,
                interaction_context=interaction_context,
            )
        )
        if reply_needs_realignment:
            reply_text = self._realign_reply_to_current_task(
                interaction_context=interaction_context,
                followup_question=followup_question,
            )
            fallback_reason = (
                "provider reply drifted into a different fire-station task and was realigned"
                if resolved_provider_name == "qwen_unified"
                else "provider reply lead was realigned to the current task"
            )
        else:
            if resolved_provider_name == "qwen_unified":
                reply_text = self._preserve_ai_rich_reply(
                    reply_text=reply_text,
                    followup_question=followup_question,
                    interaction_context=interaction_context,
                )
            else:
                reply_text = self._prioritize_followup_question(
                    reply_text=reply_text,
                    followup_question=followup_question,
                    task_signal=interaction_context.task_signal,
                )
                reply_text = self._collapse_keep_trying_reply_to_single_followup(
                    reply_text=reply_text,
                    followup_question=followup_question,
                    interaction_context=interaction_context,
                )
        if self._looks_mechanical(reply_text):
            raise InteractionProviderError(
                f"{resolved_provider_name or 'provider'} reply fell back to mechanical classroom wording.",
                retryable=False,
            )
        return InteractionGeneration(
            reply_text=reply_text,
            interaction_mode=interaction_context.interaction_mode,
            emotion_tone=interaction_context.emotion_tone,
            redirect_strength=interaction_context.redirect_strength,
            acknowledged_child_point=(
                getattr(draft, "acknowledged_child_point", None)
                or interaction_context.preferred_acknowledged_child_point
            ),
            followup_question=followup_question,
            generation_source="llm_provider",
            provider_name=resolved_provider_name,
            fallback_reason=fallback_reason,
        )

    def _build_plan(
        self,
        *,
        child_input_text: str,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
    ) -> InteractionPlan:
        followup_question = self._build_followup_question(current_task, signal_resolution)
        acknowledged_child_point = self._extract_acknowledged_point(
            child_input_text=child_input_text,
            signal_resolution=signal_resolution,
        )

        if signal_resolution.task_signal == "task_completed":
            completion_point = (
                signal_resolution.matched_completion_points[0]
                if signal_resolution.matched_completion_points
                else "这一步"
            )
            return InteractionPlan(
                interaction_mode="celebrate_completion",
                emotion_tone="excited",
                redirect_strength="none",
                acknowledged_child_point=completion_point,
                completion_point=completion_point,
            )

        if signal_resolution.task_signal == "end_session":
            return InteractionPlan(
                interaction_mode="graceful_end",
                emotion_tone="soothing",
                redirect_strength="none",
                acknowledged_child_point=acknowledged_child_point,
            )

        if signal_resolution.partial_credit:
            return InteractionPlan(
                interaction_mode="acknowledge_and_redirect",
                emotion_tone="encouraging",
                redirect_strength="soft",
                acknowledged_child_point=acknowledged_child_point,
                followup_question=self._build_partial_credit_followup(
                    current_task=current_task,
                    signal_resolution=signal_resolution,
                    acknowledged_child_point=acknowledged_child_point,
                ),
            )

        if signal_resolution.engagement_state == "frustrated":
            return InteractionPlan(
                interaction_mode="emotional_soothing",
                emotion_tone="soothing",
                redirect_strength="soft",
                acknowledged_child_point=acknowledged_child_point,
                followup_question=followup_question,
            )

        if acknowledged_child_point and signal_resolution.engagement_state in {"curious", "playful"}:
            return InteractionPlan(
                interaction_mode="playful_probe",
                emotion_tone="playful",
                redirect_strength="soft",
                acknowledged_child_point=acknowledged_child_point,
                followup_question=followup_question,
            )

        if acknowledged_child_point:
            return InteractionPlan(
                interaction_mode="warm_redirect",
                emotion_tone="warm",
                redirect_strength="soft",
                acknowledged_child_point=acknowledged_child_point,
                followup_question=followup_question,
            )

        if signal_resolution.fallback_needed:
            return InteractionPlan(
                interaction_mode="acknowledge_and_redirect",
                emotion_tone="encouraging",
                redirect_strength="medium",
                followup_question=followup_question,
            )

        return InteractionPlan(
            interaction_mode="gentle_retry",
            emotion_tone="encouraging",
            redirect_strength="medium",
            followup_question=followup_question,
        )

    def _render_template_interaction(
        self,
        *,
        interaction_context: InteractionContext,
        fallback_reason: str | None,
    ) -> InteractionGeneration:
        reply_text = self._build_template_reply(
            interaction_context=interaction_context,
        )
        return InteractionGeneration(
            reply_text=self._normalize_reply(reply_text),
            interaction_mode=interaction_context.interaction_mode,
            emotion_tone=interaction_context.emotion_tone,
            redirect_strength=interaction_context.redirect_strength,
            acknowledged_child_point=interaction_context.preferred_acknowledged_child_point,
            followup_question=interaction_context.preferred_followup_question,
            generation_source="template_fallback",
            provider_name=getattr(self.provider, "provider_name", None),
            fallback_reason=fallback_reason,
        )

    def _generate_with_provider(
        self,
        *,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        interaction_context: InteractionContext,
    ) -> tuple[InteractionGeneration | None, str | None]:
        attempt_failures: list[str] = []
        retry_hint: str | None = None
        provider_request_options = self._build_provider_request_options(signal_resolution)

        for attempt_index, request_options in enumerate(provider_request_options, start=1):
            effective_request_options = request_options
            if retry_hint is not None:
                effective_request_options = replace(
                    request_options,
                    retry_hint=retry_hint,
                )
            try:
                draft = self._request_provider_draft(
                    current_task=current_task,
                    signal_resolution=signal_resolution,
                    interaction_context=interaction_context,
                    request_options=effective_request_options,
                )
                return (
                    self.build_generation_from_draft(
                        interaction_context=interaction_context,
                        draft=draft,
                        provider_name=(
                            draft.provider_name
                            or getattr(self.provider, "provider_name", "custom_provider")
                        ),
                    ),
                    None,
                )
            except InteractionProviderError as exc:
                reason = str(exc)
                attempt_failures.append(
                    self._format_attempt_failure(
                        attempt_index=attempt_index,
                        request_options=request_options,
                        reason=reason,
                    )
                )
                retry_hint = self._build_retry_hint(reason)
                if attempt_index < len(provider_request_options) and getattr(exc, "retryable", True):
                    continue
                return None, "; ".join(attempt_failures)
            except Exception as exc:
                reason = describe_provider_failure(exc)
                attempt_failures.append(
                    self._format_attempt_failure(
                        attempt_index=attempt_index,
                        request_options=request_options,
                        reason=reason,
                    )
                )
                retry_hint = self._build_retry_hint(reason)
                if attempt_index < len(provider_request_options):
                    continue
                return None, "; ".join(attempt_failures)

        if attempt_failures:
            return None, "; ".join(attempt_failures)
        return None, None

    def _request_provider_draft(
        self,
        *,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions,
    ) -> Any:
        assert self.provider is not None

        try:
            return self.provider.generate_reply(
                interaction_context=interaction_context,
                request_options=request_options,
            )
        except TypeError as exc:
            if "interaction_context" not in str(exc):
                raise
        try:
            return self.provider.generate_reply(
                child_input_text=interaction_context.child_input_text,
                current_task=current_task,
                signal_resolution=signal_resolution,
                acknowledged_child_point=interaction_context.preferred_acknowledged_child_point,
                followup_question=interaction_context.preferred_followup_question,
                request_options=request_options,
            )
        except TypeError as exc:
            if "request_options" not in str(exc):
                raise
            return self.provider.generate_reply(
                child_input_text=interaction_context.child_input_text,
                current_task=current_task,
                signal_resolution=signal_resolution,
                acknowledged_child_point=interaction_context.preferred_acknowledged_child_point,
                followup_question=interaction_context.preferred_followup_question,
            )

    def _build_provider_request_options(
        self,
        signal_resolution: SignalResolution,
    ) -> tuple[ProviderRequestOptions, ...]:
        if signal_resolution.task_signal == "keep_trying":
            options = [
                ProviderRequestOptions(
                    timeout_seconds=self.keep_trying_timeout_seconds,
                    prompt_variant="default",
                ),
            ]
            if self.keep_trying_retry_timeout_seconds > 0:
                options.append(
                    ProviderRequestOptions(
                        timeout_seconds=self.keep_trying_retry_timeout_seconds,
                        prompt_variant="relaxed_keep_trying",
                    )
                )
            return tuple(options)
        return (
            ProviderRequestOptions(
                timeout_seconds=self.fast_path_timeout_seconds,
                prompt_variant="fast_path",
            ),
        )

    @staticmethod
    def _format_attempt_failure(
        *,
        attempt_index: int,
        request_options: ProviderRequestOptions,
        reason: str,
    ) -> str:
        timeout_label = (
            f"{request_options.timeout_seconds:g}s"
            if request_options.timeout_seconds is not None
            else "default"
        )
        return f"attempt {attempt_index} [{request_options.prompt_variant}, {timeout_label}]: {reason}"

    @staticmethod
    def _build_retry_hint(reason: str) -> str:
        normalized_reason = reason.lower()
        if "timeout" in normalized_reason:
            return "上次超时了，这次直接给更短版本。"
        if "mechanical" in normalized_reason or "classroom" in normalized_reason:
            return "上次太像课堂提问了，这次顺着孩子的话头说。"
        return "上次没出可用回复，这次更短更直接。"

    @staticmethod
    def _validate_timeout_seconds(timeout_seconds: float, *, field_name: str) -> float:
        normalized_timeout = float(timeout_seconds)
        if normalized_timeout < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
        return normalized_timeout

    @staticmethod
    def _validate_retry_timeout_seconds(timeout_seconds: float, *, field_name: str) -> float:
        normalized_timeout = float(timeout_seconds)
        if normalized_timeout < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
        return normalized_timeout

    def _build_template_reply(
        self,
        *,
        interaction_context: InteractionContext,
    ) -> str:
        seed = (
            f"{interaction_context.task_name}:"
            f"{interaction_context.task_signal}:"
            f"{interaction_context.child_input_text}"
        )

        if interaction_context.task_signal == "task_completed":
            completion_point = interaction_context.preferred_acknowledged_child_point
            if completion_point is None and interaction_context.matched_completion_points:
                completion_point = interaction_context.matched_completion_points[0]
            completion_point = completion_point or "这一步"
            return self._pick_variant(
                (
                    f"对啦，就是{completion_point}。我们接着往下玩。",
                    f"没错，这一步就是{completion_point}。我们继续。",
                    f"答对啦，就是{completion_point}。消防车可以出发啦。",
                ),
                seed,
            )

        if interaction_context.task_signal == "end_session":
            return self._pick_variant(
                (
                    "好，那我们先停这儿。等你想继续的时候再叫我。",
                    "行，今天先玩到这里。你想接着玩，我们就从这儿继续。",
                    "收到，我们先休息一下。下次回来我还记得这一步。",
                ),
                seed,
            )

        if interaction_context.partial_credit:
            followup_question = (
                interaction_context.preferred_followup_question or "那它具体是去做什么呀？"
            )
            partial_phrase = interaction_context.preferred_acknowledged_child_point or "这一步"
            lead = self._build_partial_credit_lead(partial_phrase)
            return self._pick_variant(
                (
                    f"{lead}{followup_question}",
                    f"嗯，这个方向是对的。{followup_question}",
                    f"对，你已经说到一点了。{followup_question}",
                ),
                seed,
            )

        followup_question = (
            interaction_context.preferred_followup_question or "那这一步你觉得该怎么说呀？"
        )
        acknowledged_child_point = interaction_context.preferred_acknowledged_child_point

        if interaction_context.engagement_state == "frustrated":
            return self._pick_variant(
                (
                    f"没事，我们一起想。{followup_question}",
                    f"卡住也正常，我陪你慢慢想。{followup_question}",
                    f"先不急，我们一点点来。{followup_question}",
                ),
                seed,
            )

        if acknowledged_child_point and interaction_context.engagement_state in {"curious", "playful"}:
            return self._pick_variant(
                (
                    f"是啊，{acknowledged_child_point}是挺有意思的。{followup_question}",
                    f"哈哈，你注意到{acknowledged_child_point}了。{followup_question}",
                    f"对，这个地方你看到了。{followup_question}",
                ),
                seed,
            )

        if acknowledged_child_point:
            return self._pick_variant(
                (
                    f"嗯，听到啦，你刚刚提到{acknowledged_child_point}。{followup_question}",
                    f"好，我听到了，{acknowledged_child_point}。{followup_question}",
                    f"对，你刚刚注意到{acknowledged_child_point}了。{followup_question}",
                ),
                seed,
            )

        return self._pick_variant(
            (
                f"我们先看眼前这一步。{followup_question}",
                f"先不急，我们把现在这件事说出来。{followup_question}",
                f"来，这一步先说清楚。{followup_question}",
            ),
            seed,
        )

    @staticmethod
    def _prioritize_followup_question(
        *,
        reply_text: str,
        followup_question: str | None,
        task_signal: str,
    ) -> str:
        if not followup_question or task_signal in {"task_completed", "end_session"}:
            return reply_text

        normalized_reply = " ".join(reply_text.split()).strip()
        if not normalized_reply:
            return followup_question
        if followup_question in normalized_reply:
            return normalized_reply

        lead = normalized_reply
        for separator in ("。", "！", "!", "？", "?", "；", ";"):
            if separator in lead:
                lead = lead.split(separator, 1)[0].strip()
                break
        lead = lead.rstrip("，,：: ")
        if not lead:
            lead = normalized_reply
        if len(lead) > 32:
            lead = lead[:32].rstrip("，,：: ")
        if not lead:
            return followup_question
        return f"{lead}。{followup_question}"

    @classmethod
    def _collapse_keep_trying_reply_to_single_followup(
        cls,
        *,
        reply_text: str,
        followup_question: str | None,
        interaction_context: InteractionContext,
    ) -> str:
        if interaction_context.task_signal != "keep_trying" or not followup_question:
            return reply_text

        normalized_reply = " ".join(reply_text.split()).strip()
        if not normalized_reply:
            return followup_question

        question_count = normalized_reply.count("？") + normalized_reply.count("?")
        if question_count <= 1 and normalized_reply.endswith(followup_question):
            return normalized_reply

        lead = normalized_reply
        for separator in ("？", "?", "。", "！", "!", "；", ";"):
            if separator in lead:
                lead = lead.split(separator, 1)[0].strip()
                break
        lead = lead.rstrip("，,：: ")
        if not lead or lead == followup_question:
            acknowledged = interaction_context.preferred_acknowledged_child_point
            if acknowledged:
                lead = f"对，{acknowledged}。"
            else:
                lead = "我们就看这一步。"
        if lead.endswith(("吗", "呢", "呀", "啊")):
            acknowledged = interaction_context.preferred_acknowledged_child_point
            lead = f"对，{acknowledged}。" if acknowledged else "我们就看这一步。"
        return f"{lead}{followup_question}" if lead.endswith(("。", "！", "!", "？", "?")) else f"{lead}。{followup_question}"

    @classmethod
    def _preserve_ai_rich_reply(
        cls,
        *,
        reply_text: str,
        followup_question: str | None,
        interaction_context: InteractionContext,
    ) -> str:
        normalized_reply = " ".join(reply_text.split()).strip()
        if not normalized_reply:
            return followup_question or ""
        if interaction_context.task_signal == "keep_trying" and len(normalized_reply) > 90:
            return cls._compress_keep_trying_reply(
                reply_text=normalized_reply,
                followup_question=followup_question,
                interaction_context=interaction_context,
            )
        if interaction_context.task_signal == "task_completed" and len(normalized_reply) > 100:
            return cls._compress_completed_reply(normalized_reply)
        if interaction_context.task_signal != "keep_trying" or not followup_question:
            return normalized_reply
        if followup_question in normalized_reply:
            return normalized_reply
        question_count = normalized_reply.count("？") + normalized_reply.count("?")
        if question_count >= 1:
            return normalized_reply
        trimmed_reply = normalized_reply.rstrip("。！？!? ")
        return f"{trimmed_reply}。{followup_question}"

    @classmethod
    def _compress_keep_trying_reply(
        cls,
        *,
        reply_text: str,
        followup_question: str | None,
        interaction_context: InteractionContext,
    ) -> str:
        acknowledged = interaction_context.preferred_acknowledged_child_point
        if acknowledged:
            lead = f"好，我听到你提到{acknowledged}"
        else:
            lead = "好，我们就看这一步"
        if followup_question:
            return f"{lead}。{followup_question}"
        return f"{lead}。"

    @staticmethod
    def _compress_completed_reply(reply_text: str) -> str:
        sentences = MinimalInteractionGenerator._split_sentences(reply_text)
        if not sentences:
            return reply_text
        kept: list[str] = []
        total_length = 0
        for sentence in sentences:
            if kept and total_length + len(sentence) > 88:
                break
            kept.append(sentence)
            total_length += len(sentence)
            if len(kept) >= 2:
                break
        shortened = "".join(kept).strip()
        return shortened or reply_text

    @staticmethod
    def _take_lead_sentence(reply_text: str) -> str:
        for separator in ("。", "！", "!", "？", "?", "；", ";"):
            if separator in reply_text:
                return reply_text.split(separator, 1)[0].strip()
        return reply_text.strip()

    @staticmethod
    def _split_sentences(reply_text: str) -> tuple[str, ...]:
        parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", reply_text)
        normalized_parts = tuple(part.strip() for part in parts if part.strip())
        return normalized_parts if normalized_parts else (reply_text.strip(),)

    def _build_followup_question(
        self,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
    ) -> str:
        scene_task_question = self._build_scene_task_followup_question(
            current_task,
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:scene-task",
        )
        if scene_task_question is not None:
            return scene_task_question

        action_phrase = self._extract_action_phrase(current_task.expected_child_action)
        action_question = self._build_action_question(
            action_phrase,
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:action",
        )
        if action_question is not None:
            return action_question

        completion_point_question = self._build_completion_point_question(
            current_task,
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:completion",
        )
        if completion_point_question is not None:
            return completion_point_question

        subject = self._extract_subject(action_phrase)
        if subject is not None:
            return self._pick_variant(
                (
                    f"那{subject}现在要做什么呀？",
                    f"你觉得{subject}接下来会怎么做呢？",
                    f"那{subject}这会儿是在忙什么呀？",
                ),
                f"{current_task.task_id}:{signal_resolution.normalized_child_text}:subject",
            )

        return self._pick_variant(
            (
                "那你觉得这一步该怎么说呀？",
                "我们就看现在这件事，你会怎么讲呢？",
                "先把这一步说出来，好不好？",
            ),
            f"{current_task.task_id}:{signal_resolution.normalized_child_text}:generic",
        )

    @classmethod
    def _build_scene_task_followup_question(
        cls,
        current_task: TaskContext,
        *,
        seed: str,
    ) -> str | None:
        options = FIRE_STATION_FOLLOWUP_HINTS.get(current_task.task_id)
        if not options:
            return None
        if not cls._scene_task_followup_applies(current_task):
            return None
        return cls._pick_variant(options, seed)

    @classmethod
    def _scene_task_followup_applies(cls, current_task: TaskContext) -> bool:
        expected_keywords = FIRE_STATION_TASK_ANCHOR_KEYWORDS.get(current_task.task_id)
        if not expected_keywords:
            return True
        task_text = cls._build_task_text(
            task_goal=current_task.task_goal,
            expected_child_action=current_task.expected_child_action,
            completion_points=current_task.completion_points,
        )
        return any(keyword in task_text for keyword in expected_keywords)

    def _build_interaction_context(
        self,
        *,
        child_input_text: str,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        interaction_plan: InteractionPlan,
        session_memory_summary: str | None,
    ) -> InteractionContext:
        normalized_child_input = child_input_text.strip() or "(empty)"
        return InteractionContext(
            task_name=current_task.task_name,
            child_input_text=normalized_child_input,
            normalized_child_text=signal_resolution.normalized_child_text,
            task_signal=signal_resolution.task_signal,
            engagement_state=signal_resolution.engagement_state,
            partial_credit=signal_resolution.partial_credit,
            matched_completion_points=signal_resolution.matched_completion_points,
            missing_completion_points=signal_resolution.missing_completion_points,
            interaction_goal=self._build_interaction_goal(
                current_task=current_task,
                signal_resolution=signal_resolution,
                interaction_plan=interaction_plan,
            ),
            scene_style=current_task.scene_style,
            redirect_strength=interaction_plan.redirect_strength,
            expected_child_action=current_task.expected_child_action,
            task_goal=current_task.task_goal,
            completion_points=current_task.completion_points,
            interaction_mode=interaction_plan.interaction_mode,
            emotion_tone=interaction_plan.emotion_tone,
            preferred_acknowledged_child_point=interaction_plan.acknowledged_child_point,
            preferred_followup_question=interaction_plan.followup_question,
            recent_turn_summary=self._build_recent_turn_summary(
                current_task=current_task,
                signal_resolution=signal_resolution,
                interaction_plan=interaction_plan,
            ),
            rule_reason=signal_resolution.reason,
            scene_context=current_task.scene_context,
            session_memory=session_memory_summary,
        )

    def _build_interaction_goal(
        self,
        *,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        interaction_plan: InteractionPlan,
    ) -> str:
        action_target = self._extract_action_phrase(current_task.expected_child_action)
        if signal_resolution.task_signal == "task_completed":
            completion_point = (
                interaction_plan.completion_point
                or self._join_completion_points(signal_resolution.matched_completion_points)
                or action_target
                or "这一步"
            )
            return f"确认孩子已经说到{completion_point}，轻快收住这一轮。"

        if signal_resolution.task_signal == "end_session":
            return "顺着孩子想停一下的意思收尾，先把这一轮安稳停住。"

        if signal_resolution.partial_credit:
            partial_phrase = interaction_plan.acknowledged_child_point or "动作方向"
            missing = self._join_completion_points(signal_resolution.missing_completion_points)
            return f"先肯定孩子已经说到{partial_phrase}这个方向，再补到{missing or action_target or current_task.expected_child_action}。"

        if signal_resolution.engagement_state == "frustrated":
            return f"先安抚孩子，再把话题带回{action_target or current_task.expected_child_action}。"

        if signal_resolution.matched_completion_points and signal_resolution.missing_completion_points:
            matched = self._join_completion_points(signal_resolution.matched_completion_points)
            missing = self._join_completion_points(signal_resolution.missing_completion_points)
            return f"先接住孩子已经说到的{matched}，再轻轻补回{missing}。"

        if interaction_plan.acknowledged_child_point:
            return (
                f"先接住孩子提到的{interaction_plan.acknowledged_child_point}，"
                f"再把话题拉回{action_target or current_task.expected_child_action}。"
            )

        return f"保持当前任务，继续引导孩子说到{action_target or current_task.expected_child_action}。"

    def _build_recent_turn_summary(
        self,
        *,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        interaction_plan: InteractionPlan,
    ) -> str | None:
        action_target = self._extract_action_phrase(current_task.expected_child_action)

        if signal_resolution.task_signal == "task_completed":
            completion_point = self._join_completion_points(signal_resolution.matched_completion_points)
            return f"孩子已经说到{completion_point or action_target or '当前完成点'}。"

        if signal_resolution.task_signal == "end_session":
            return "孩子明确表示想先停一下。"

        if signal_resolution.partial_credit:
            partial_phrase = interaction_plan.acknowledged_child_point or "动作方向"
            missing = self._join_completion_points(signal_resolution.missing_completion_points)
            return (
                f"孩子已经说到{partial_phrase}这个方向，"
                f"但还没说到{missing or action_target or current_task.expected_child_action}。"
            )

        if signal_resolution.matched_completion_points and signal_resolution.missing_completion_points:
            matched = self._join_completion_points(signal_resolution.matched_completion_points)
            missing = self._join_completion_points(signal_resolution.missing_completion_points)
            return f"孩子已经说到{matched}，但还没说到{missing}。"

        if signal_resolution.engagement_state == "frustrated":
            return f"孩子这轮有点卡住，还没说到{action_target or current_task.expected_child_action}。"

        if interaction_plan.acknowledged_child_point:
            return (
                f"孩子刚提到{interaction_plan.acknowledged_child_point}，"
                f"但还没说到{action_target or current_task.expected_child_action}。"
            )

        if signal_resolution.normalized_child_text != "(empty)":
            return f"这轮还没命中“{action_target or current_task.expected_child_action}”这个完成点。"
        return None

    @staticmethod
    def _join_completion_points(points: tuple[str, ...]) -> str:
        return "、".join(point for point in points if point)

    @staticmethod
    def _extract_acknowledged_point(
        *,
        child_input_text: str,
        signal_resolution: SignalResolution,
    ) -> str | None:
        if signal_resolution.matched_completion_points:
            return signal_resolution.matched_completion_points[0]

        normalized_child_text = signal_resolution.normalized_child_text.strip()
        if normalized_child_text and normalized_child_text != "(empty)":
            normalized_child_text = MinimalInteractionGenerator._simplify_acknowledged_point(
                normalized_child_text
            )
            if len(normalized_child_text) > 16:
                return normalized_child_text[:16]
            return normalized_child_text

        compact_text = child_input_text.strip()
        if compact_text:
            compact_text = MinimalInteractionGenerator._simplify_acknowledged_point(compact_text)
            return compact_text[:16]
        return None

    @staticmethod
    def _extract_action_phrase(expected_child_action: str) -> str:
        phrase = expected_child_action.strip().rstrip("。！？!?")
        for prefix in ("说出", "说一说", "说说", "回答", "告诉我", "指出", "找出", "找到", "选出", "选择"):
            if phrase.startswith(prefix):
                phrase = phrase[len(prefix) :].strip()
                break
        return phrase or expected_child_action.strip().rstrip("。！？!?")

    @classmethod
    def _build_action_question(cls, action_phrase: str, *, seed: str) -> str | None:
        normalized_phrase = action_phrase.strip().rstrip("。！？!?")
        for marker, options in (
            (
                "要去",
                (
                    "那{subject}现在要去做什么呀？",
                    "你觉得{subject}这会儿是去忙什么呀？",
                    "那{subject}现在是去帮什么忙呢？",
                ),
            ),
            (
                "要",
                (
                    "那{subject}现在要做什么呀？",
                    "你觉得{subject}接下来要忙什么呢？",
                    "那{subject}这会儿是要干嘛呀？",
                ),
            ),
            (
                "会",
                (
                    "那{subject}接下来会做什么呀？",
                    "你觉得{subject}后面会怎么做呢？",
                    "那{subject}等下会忙什么呀？",
                ),
            ),
            (
                "在",
                (
                    "那{subject}现在在做什么呀？",
                    "你觉得{subject}这会儿在忙什么呢？",
                    "那{subject}现在是在干嘛呀？",
                ),
            ),
            (
                "去",
                (
                    "那{subject}现在去做什么呀？",
                    "你觉得{subject}这会儿去忙什么呢？",
                    "那{subject}现在是去干嘛呀？",
                ),
            ),
        ):
            if marker not in normalized_phrase:
                continue
            subject, _, _ = normalized_phrase.partition(marker)
            normalized_subject = subject.strip()
            if normalized_subject:
                return cls._pick_variant(
                    tuple(option.format(subject=normalized_subject) for option in options),
                    seed,
            )
        return None

    @classmethod
    def _build_completion_point_question(
        cls,
        current_task: TaskContext,
        *,
        seed: str,
    ) -> str | None:
        if not current_task.completion_points:
            return None

        keyword_pool = {
            cls._normalize_completion_keyword(keyword)
            for completion_point in current_task.completion_points
            for keyword in completion_point.keywords
        }
        if not keyword_pool:
            return None

        if (
            any(keyword in keyword_pool for keyword in ("能动", "会动", "可动"))
            and any(keyword in keyword_pool for keyword in ("不能动", "固定"))
        ):
            return cls._pick_variant(
                (
                    "那哪些能动，哪些不能动呀？",
                    "你觉得哪些可以动，哪些不能动呢？",
                    "那画在墙上的和能动的分别是哪一些呀？",
                ),
                seed,
            )

        if (
            "背景" in keyword_pool
            and any(keyword in keyword_pool for keyword in ("画在墙上", "墙上"))
            and any(keyword in keyword_pool for keyword in ("能动", "会动", "可动"))
        ):
            return cls._pick_variant(
                (
                    "那哪些是能动的，哪些只是画在墙上的呀？",
                    "你觉得哪些会动，哪些是画在墙上的呢？",
                    "那现在哪些能动，哪些不能动呀？",
                ),
                seed,
            )

        label = current_task.completion_points[0].label
        if label:
            return cls._pick_variant(
                (
                    f"那{label}这一轮要怎么说呀？",
                    f"你觉得{label}现在该怎么判断呢？",
                    f"那这一步和{label}有关的内容该怎么讲呀？",
                ),
                seed,
            )
        return None

    @staticmethod
    def _normalize_completion_keyword(keyword: str) -> str:
        normalized = keyword.strip().rstrip("。！？!?")
        return normalized

    @classmethod
    def _build_partial_credit_followup(
        cls,
        *,
        current_task: TaskContext,
        signal_resolution: SignalResolution,
        acknowledged_child_point: str | None,
    ) -> str:
        scene_task_question = cls._build_scene_task_followup_question(
            current_task,
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:partial-scene",
        )
        if scene_task_question is not None:
            return scene_task_question

        completion_point_question = cls._build_completion_point_question(
            current_task,
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:partial-completion",
        )
        if completion_point_question is not None:
            return completion_point_question

        if acknowledged_child_point and "帮忙" in acknowledged_child_point:
            action_phrase = cls._extract_action_phrase(current_task.expected_child_action)
            subject = cls._extract_subject(action_phrase) or "它"
            return cls._pick_variant(
                (
                    f"那{subject}具体是去帮什么忙呀？",
                    f"对，那{subject}是去帮什么忙呢？",
                    f"嗯，那{subject}到底要去帮什么忙呀？",
                ),
                f"{current_task.task_id}:{signal_resolution.normalized_child_text}:partial-help",
            )
        return cls._build_action_question(
            cls._extract_action_phrase(current_task.expected_child_action),
            seed=f"{current_task.task_id}:{signal_resolution.normalized_child_text}:partial-action",
        ) or cls._pick_variant(
            (
                "那它具体是去做什么呀？",
                "你觉得它到底要去忙什么呢？",
                "那这一步具体是在做什么呀？",
            ),
            f"{current_task.task_id}:{signal_resolution.normalized_child_text}:partial-generic",
        )

    @classmethod
    def _looks_off_task_reply(
        cls,
        *,
        reply_text: str,
        followup_question: str | None,
        interaction_context: InteractionContext,
    ) -> bool:
        if interaction_context.task_signal != "keep_trying":
            return False

        normalized_reply = " ".join(reply_text.split()).strip()
        if not normalized_reply:
            return True

        task_anchor_keywords = cls._collect_task_anchor_keywords(
            interaction_context=interaction_context,
            followup_question=followup_question,
        )
        if any(keyword and keyword in normalized_reply for keyword in task_anchor_keywords):
            return False

        lead = normalized_reply
        for separator in ("。", "！", "!", "？", "?", "；", ";"):
            if separator in lead:
                lead = lead.split(separator, 1)[0].strip()
                break

        if not lead:
            return True

        if any(keyword and keyword in lead for keyword in task_anchor_keywords):
            return False

        off_task_markers = (
            "有什么",
            "哪里",
            "哪儿",
            "厨房",
            "卧室",
            "客厅",
            "楼道",
            "拐角",
            "灶台",
            "沙发",
            "停车场",
            "商店",
        )
        return any(marker in lead for marker in off_task_markers)

    @classmethod
    def _looks_mismatched_task_reply(
        cls,
        *,
        reply_text: str,
        followup_question: str | None,
        interaction_context: InteractionContext,
    ) -> bool:
        if interaction_context.task_signal != "keep_trying":
            return False

        normalized_reply = " ".join(reply_text.split()).strip()
        if not normalized_reply:
            return True

        if cls._contains_mismatched_scene_task_markers(
            followup_question=normalized_reply,
            interaction_context=interaction_context,
        ):
            return True

        task_anchor_keywords = cls._collect_task_anchor_keywords(
            interaction_context=interaction_context,
            followup_question=followup_question,
        )
        if any(keyword and keyword in normalized_reply for keyword in task_anchor_keywords):
            return False
        return False

    @classmethod
    def _looks_off_task_followup_question(
        cls,
        *,
        followup_question: str,
        interaction_context: InteractionContext,
    ) -> bool:
        normalized_followup = " ".join(followup_question.split()).strip()
        if not normalized_followup:
            return True
        if cls._contains_mismatched_scene_task_markers(
            followup_question=normalized_followup,
            interaction_context=interaction_context,
        ):
            return True
        task_anchor_keywords = cls._collect_task_anchor_keywords(
            interaction_context=interaction_context,
            followup_question=None,
        )
        if any(keyword and keyword in normalized_followup for keyword in task_anchor_keywords):
            return False
        if any(marker in normalized_followup for marker in ("帮谁", "救谁", "找谁", "去帮谁")):
            return True
        off_task_markers = (
            "有什么",
            "哪里",
            "哪儿",
            "厨房",
            "卧室",
            "客厅",
            "楼道",
            "灶台",
            "停车场",
            "商店",
        )
        return any(marker in normalized_followup for marker in off_task_markers)

    @classmethod
    def _contains_mismatched_scene_task_markers(
        cls,
        *,
        followup_question: str,
        interaction_context: InteractionContext,
    ) -> bool:
        inferred_task_id = cls._infer_task_id_from_interaction_context(interaction_context)
        current_task_keywords = set(FIRE_STATION_TASK_DRIFT_MARKERS.get(inferred_task_id, ()))
        other_task_keywords: set[str] = set()
        for task_id, keywords in FIRE_STATION_TASK_DRIFT_MARKERS.items():
            if task_id == inferred_task_id:
                continue
            if keywords:
                other_task_keywords.update(keywords)
        return any(keyword in followup_question for keyword in other_task_keywords if keyword not in current_task_keywords)

    @classmethod
    def _realign_reply_to_current_task(
        cls,
        *,
        interaction_context: InteractionContext,
        followup_question: str | None,
    ) -> str:
        acknowledged = interaction_context.preferred_acknowledged_child_point
        if interaction_context.engagement_state == "frustrated":
            lead = "我听到了，我们慢慢来。"
        elif acknowledged:
            lead = f"我听到了，你刚刚提到{acknowledged}。"
        else:
            lead = "我们就看这一步。"

        resolved_followup = followup_question or interaction_context.preferred_followup_question or "先把这一步说清楚，好不好？"
        return f"{lead}{resolved_followup}"

    @staticmethod
    def _collect_task_anchor_keywords(
        *,
        interaction_context: InteractionContext,
        followup_question: str | None,
    ) -> tuple[str, ...]:
        keywords: list[str] = []
        inferred_task_id = MinimalInteractionGenerator._infer_task_id_from_interaction_context(
            interaction_context
        )
        if inferred_task_id is not None:
            keywords.extend(FIRE_STATION_TASK_ANCHOR_KEYWORDS.get(inferred_task_id, ()))
        for completion_point in interaction_context.completion_points:
            keywords.append(completion_point.label)
            keywords.extend(keyword for keyword in completion_point.keywords if 1 <= len(keyword) <= 8)
        if followup_question:
            normalized_followup = " ".join(followup_question.split()).strip()
            if inferred_task_id is not None:
                keywords.extend(
                    keyword
                    for keyword in FIRE_STATION_TASK_ANCHOR_KEYWORDS.get(inferred_task_id, ())
                    if keyword in normalized_followup
                )
            keywords.extend(
                chunk
                for chunk in (
                    followup_question.replace("？", " ")
                    .replace("?", " ")
                    .replace("，", " ")
                    .replace(",", " ")
                    .split()
                )
                if 1 <= len(chunk) <= 8
            )
        if interaction_context.task_goal:
            keywords.extend(
                marker
                for marker in ("能动", "不能动", "画在墙上", "固定", "内部", "外部", "消防车", "直升机", "大火", "小火", "中火", "救火", "灭火", "总结", "铃铛", "指挥中心", "停机坪", "门口", "更衣室", "医药箱", "路障")
                if marker in interaction_context.task_goal
            )
        if interaction_context.expected_child_action:
            keywords.extend(
                marker
                for marker in ("能动", "不能动", "画在墙上", "固定", "内部", "外部", "消防车", "直升机", "大火", "小火", "中火", "救火", "灭火", "总结", "铃铛", "指挥中心", "停机坪", "门口", "更衣室", "医药箱", "路障")
                if marker in interaction_context.expected_child_action
            )
        deduped: list[str] = []
        for keyword in keywords:
            if keyword and keyword not in deduped:
                deduped.append(keyword)
        return tuple(deduped)

    @staticmethod
    def _infer_task_id_from_interaction_context(interaction_context: InteractionContext) -> str | None:
        inferred_task_id = MinimalInteractionGenerator._infer_task_id_from_text(
            task_goal=interaction_context.task_goal,
            expected_child_action=interaction_context.expected_child_action,
            completion_points=interaction_context.completion_points,
        )
        if inferred_task_id is not None:
            return inferred_task_id
        return None

    @staticmethod
    def _infer_task_id_from_text(
        *,
        task_goal: str | None,
        expected_child_action: str | None,
        completion_points: tuple[Any, ...],
    ) -> str | None:
        keyword_lookup = {
            "能动": "fs_001",
            "画在墙上": "fs_001",
            "铃铛": "fs_001",
            "指挥中心": "fs_001",
            "停机坪": "fs_001",
            "门口": "fs_001",
            "更衣室": "fs_001",
            "医药箱": "fs_001",
            "路障": "fs_001",
            "内部": "fs_002",
            "外部": "fs_002",
            "直升机": "fs_003",
            "集合": "fs_003",
            "大火": "fs_004",
            "小火": "fs_004",
            "中火": "fs_004",
            "救火": "fs_005",
            "灭火": "fs_005",
            "总结": "fs_006",
            "回站": "fs_006",
        }
        for completion_point in completion_points:
            for keyword in completion_point.keywords:
                inferred_task_id = keyword_lookup.get(keyword)
                if inferred_task_id is not None:
                    return inferred_task_id
        task_text = MinimalInteractionGenerator._build_task_text(
            task_goal=task_goal or "",
            expected_child_action=expected_child_action or "",
            completion_points=completion_points,
        )
        for keyword, inferred_task_id in keyword_lookup.items():
            if keyword in task_text:
                return inferred_task_id
        return None

    @staticmethod
    def _build_task_text(
        *,
        task_goal: str,
        expected_child_action: str,
        completion_points: tuple[Any, ...],
    ) -> str:
        fragments = [task_goal or "", expected_child_action or ""]
        for completion_point in completion_points:
            fragments.append(completion_point.label)
            fragments.extend(completion_point.keywords)
        return " ".join(fragment for fragment in fragments if fragment)

    @staticmethod
    def _extract_subject(action_phrase: str) -> str | None:
        normalized_phrase = action_phrase.strip().rstrip("。！？!?")
        for marker in ("要", "在", "是", "会", "去"):
            if marker not in normalized_phrase:
                continue
            subject, _, _ = normalized_phrase.partition(marker)
            normalized_subject = subject.strip()
            if 1 <= len(normalized_subject) <= 10:
                return normalized_subject
        if 1 <= len(normalized_phrase) <= 8:
            return normalized_phrase
        return None

    @staticmethod
    def _simplify_acknowledged_point(text: str) -> str:
        normalized_text = text.strip().rstrip("。！？!?")
        for suffix in (
            "真帅",
            "好帅",
            "太帅了",
            "好酷",
            "真酷",
            "太酷了",
            "厉害",
            "好厉害",
            "真厉害",
            "真威风",
            "好玩",
            "真好玩",
        ):
            if normalized_text.endswith(suffix):
                candidate = normalized_text[: -len(suffix)].strip()
                if 1 <= len(candidate) <= 8:
                    return candidate
        return normalized_text

    @staticmethod
    def _build_partial_credit_lead(partial_phrase: str) -> str:
        normalized_phrase = partial_phrase.strip().rstrip("。！？!?")
        if normalized_phrase.startswith(("去", "要去", "会去")):
            return f"对，是{normalized_phrase}。"
        return f"对，{normalized_phrase}这个方向对了。"

    @staticmethod
    def _pick_variant(options: tuple[str, ...], seed: str) -> str:
        if not options:
            raise ValueError("options must not be empty")
        index = sum(ord(char) for char in seed) % len(options)
        return options[index]

    @staticmethod
    def _looks_mechanical(reply_text: str) -> bool:
        return any(marker in reply_text for marker in MECHANICAL_REPLY_MARKERS)

    @staticmethod
    def _normalize_reply(reply_text: str) -> str:
        return " ".join(reply_text.split())
