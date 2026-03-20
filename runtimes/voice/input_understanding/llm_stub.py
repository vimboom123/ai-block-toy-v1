from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .interaction_provider import (
    DEFAULT_QWEN_BASE_URL,
    DEFAULT_QWEN_MODEL,
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    OpenAICompatibleConfigError,
    OpenAICompatibleRequestError,
    _extract_json_object,
    _optional_string,
)
from .models import CompletionPoint, SignalResolution, TaskContext, VALID_ENGAGEMENT_STATES, VALID_TASK_SIGNALS
from .task_oral_hints import build_task_oral_hints

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE5_ROOT_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]


class SignalResolverLLMStub:
    """No-op hook for second-stage semantic resolution."""

    def resolve(
        self,
        *,
        child_input_text: str,
        normalized_child_text: str,
        current_task: TaskContext,
        rule_candidate: SignalResolution | None,
    ) -> SignalResolution | None:
        del child_input_text, normalized_child_text, current_task, rule_candidate
        return None


class QwenSemanticSignalResolver(SignalResolverLLMStub):
    """Cheap semantic override that can upgrade keep_trying to task_completed."""

    DEFAULT_TIMEOUT_SECONDS = 0.9
    DEFAULT_MAX_TOKENS = 96

    def __init__(
        self,
        *,
        client: OpenAICompatibleClient | None = None,
        root_dir: Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.setup_error: BaseException | None = None
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        if client is not None:
            self.client = client
            return

        try:
            env = build_runtime_env((root_dir or PHASE5_ROOT_DIR) / ".env.local")
            config = OpenAICompatibleConfig.from_env(
                env,
                provider_label="Qwen semantic resolver",
                api_key_env_keys=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
                model_env_keys=("QWEN_MODEL", "DASHSCOPE_MODEL"),
                base_url_env_keys=("QWEN_BASE_URL", "DASHSCOPE_BASE_URL"),
                request_url_env_keys=("QWEN_REQUEST_URL", "DASHSCOPE_REQUEST_URL", "DASHSCOPE_CHAT_COMPLETIONS_URL"),
                timeout_env_keys=("QWEN_SIGNAL_TIMEOUT_SECONDS", "DASHSCOPE_SIGNAL_TIMEOUT_SECONDS"),
                max_tokens_env_keys=(),
                temperature_env_keys=(),
                default_base_url=DEFAULT_QWEN_BASE_URL,
                default_model=DEFAULT_QWEN_MODEL,
                default_timeout_seconds=timeout_seconds,
                default_max_tokens=max_tokens,
                default_temperature=0.0,
            )
            self.client = OpenAICompatibleClient(config)
        except (OpenAICompatibleConfigError, ValueError) as exc:
            self.setup_error = exc

    def resolve(
        self,
        *,
        child_input_text: str,
        normalized_child_text: str,
        current_task: TaskContext,
        rule_candidate: SignalResolution | None,
    ) -> SignalResolution | None:
        if self.setup_error is not None:
            return None
        if rule_candidate is None or rule_candidate.task_signal != "keep_trying":
            return None
        if not current_task.completion_points:
            return None
        if normalized_child_text.strip() in {"", "(empty)"}:
            return None

        try:
            content = self.client.create_chat_completion(
                [
                    {
                        "role": "system",
                        "content": self._build_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": self._build_user_prompt(
                            child_input_text=child_input_text,
                            normalized_child_text=normalized_child_text,
                            current_task=current_task,
                            rule_candidate=rule_candidate,
                        ),
                    },
                ]
            ).content_text
            payload = _extract_json_object(content)
        except (OpenAICompatibleRequestError, OpenAICompatibleConfigError, ValueError, json.JSONDecodeError):
            return None
        except Exception:
            return None

        if _optional_string(payload.get("task_signal")) != "task_completed":
            return None

        matched_completion_points = self._coerce_matched_completion_points(
            payload.get("matched_completion_points"),
            current_task=current_task,
        )
        if len(matched_completion_points) < current_task.required_completion_count():
            return None

        raw_confidence = payload.get("confidence")
        confidence = (
            min(max(float(raw_confidence), 0.0), 1.0)
            if isinstance(raw_confidence, (int, float))
            else 0.74
        )
        reason = (
            _optional_string(payload.get("reason"))
            or f"AI 语义判定：孩子的表达已经等价命中 {', '.join(matched_completion_points)}。"
        )
        missing_completion_points = tuple(
            label
            for label in current_task.completion_point_labels()
            if label not in matched_completion_points
        )
        return SignalResolution(
            task_signal="task_completed",
            confidence=confidence,
            reason=reason,
            fallback_needed=False,
            normalized_child_text=normalized_child_text,
            partial_credit=False,
            matched_completion_points=matched_completion_points,
            missing_completion_points=missing_completion_points,
            engagement_state=rule_candidate.engagement_state,
        )

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "你是儿童任务语义判定器。"
            "只判断孩子这句话在语义上是否已经完成当前任务。"
            "允许口语、同义词、ASR 小偏差、近义表达。"
            "只有在明显已经完成时才返回 task_completed；拿不准就返回 keep_trying。"
            "不要额外解释规则。只输出 JSON，字段必须是 "
            '{"task_signal":"task_completed|keep_trying","matched_completion_points":["label"],"confidence":0.0,"reason":"..."}'
        )

    @classmethod
    def _build_user_prompt(
        cls,
        *,
        child_input_text: str,
        normalized_child_text: str,
        current_task: TaskContext,
        rule_candidate: SignalResolution,
    ) -> str:
        payload = {
            "task": {
                "task_id": current_task.task_id,
                "task_name": current_task.task_name,
                "task_goal": current_task.task_goal,
                "expected_child_action": current_task.expected_child_action,
                "completion_match_mode": current_task.completion_match_mode,
                "completion_points": [cls._completion_point_to_dict(point) for point in current_task.completion_points],
            },
            "child": {
                "said": child_input_text,
                "normalized": normalized_child_text,
            },
            "rule_candidate": {
                "task_signal": rule_candidate.task_signal,
                "reason": rule_candidate.reason,
                "matched_completion_points": list(rule_candidate.matched_completion_points),
                "missing_completion_points": list(rule_candidate.missing_completion_points),
                "partial_credit": rule_candidate.partial_credit,
                "engagement_state": rule_candidate.engagement_state,
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _completion_point_to_dict(point: CompletionPoint) -> dict[str, Any]:
        return {
            "label": point.label,
            "keywords": list(point.keywords),
        }

    @staticmethod
    def _normalize_label(value: str) -> str:
        return "".join(ch for ch in value.strip().lower() if ch not in {" ", "_", "-", "，", "。", ",", "."})

    @classmethod
    def _coerce_matched_completion_points(
        cls,
        raw_value: Any,
        *,
        current_task: TaskContext,
    ) -> tuple[str, ...]:
        if isinstance(raw_value, str):
            candidates = [raw_value]
        elif isinstance(raw_value, list):
            candidates = [candidate for candidate in raw_value if isinstance(candidate, str)]
        else:
            candidates = []

        completion_point_by_label = {
            cls._normalize_label(point.label): point.label
            for point in current_task.completion_points
        }
        completion_point_by_keyword = {
            cls._normalize_label(keyword): point.label
            for point in current_task.completion_points
            for keyword in point.keywords
        }

        matched_labels: list[str] = []
        for candidate in candidates:
            normalized_candidate = cls._normalize_label(candidate)
            resolved_label = (
                completion_point_by_label.get(normalized_candidate)
                or completion_point_by_keyword.get(normalized_candidate)
            )
            if resolved_label and resolved_label not in matched_labels:
                matched_labels.append(resolved_label)
        return tuple(matched_labels)


def build_signal_resolver_llm(provider_mode: str) -> SignalResolverLLMStub:
    if provider_mode in {"qwen", "auto"}:
        return QwenSemanticSignalResolver()
    return SignalResolverLLMStub()


class QwenTaskSignalResolver(QwenSemanticSignalResolver):
    """AI-first task signal resolver for qwen/auto paths."""

    def resolve(self, child_input_text: str, current_task: TaskContext) -> SignalResolution | None:  # type: ignore[override]
        if self.setup_error is not None:
            return None

        normalized_child_text = child_input_text.strip() or "(empty)"
        try:
            content = self.client.create_chat_completion(
                [
                    {
                        "role": "system",
                        "content": self._build_task_signal_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": self._build_task_signal_user_prompt(
                            child_input_text=child_input_text,
                            current_task=current_task,
                        ),
                    },
                ]
            ).content_text
            payload = _extract_json_object(content)
        except (OpenAICompatibleRequestError, OpenAICompatibleConfigError, ValueError, json.JSONDecodeError):
            return None
        except Exception:
            return None

        raw_task_signal = _optional_string(payload.get("task_signal")) or "keep_trying"
        if raw_task_signal not in VALID_TASK_SIGNALS:
            return None
        if raw_task_signal not in current_task.allowed_signals:
            raw_task_signal = "keep_trying"

        matched_completion_points = self._coerce_matched_completion_points(
            payload.get("matched_completion_points"),
            current_task=current_task,
        )
        if raw_task_signal == "task_completed" and not matched_completion_points and current_task.completion_points:
            matched_completion_points = current_task.completion_point_labels()

        missing_completion_points = tuple(
            label
            for label in current_task.completion_point_labels()
            if label not in matched_completion_points
        )
        raw_confidence = payload.get("confidence")
        confidence = (
            min(max(float(raw_confidence), 0.0), 1.0)
            if isinstance(raw_confidence, (int, float))
            else (0.86 if raw_task_signal == "task_completed" else 0.74)
        )
        engagement_state = _optional_string(payload.get("engagement_state")) or "unknown"
        if engagement_state not in VALID_ENGAGEMENT_STATES:
            engagement_state = "unknown"
        partial_credit = bool(payload.get("partial_credit")) if raw_task_signal == "keep_trying" else False
        reason = (
            _optional_string(payload.get("reason"))
            or (
                f"AI 判定已完成：{', '.join(matched_completion_points)}。"
                if raw_task_signal == "task_completed"
                else "AI 判定当前还需继续引导。"
            )
        )

        return SignalResolution(
            task_signal=raw_task_signal,
            confidence=confidence,
            reason=reason,
            fallback_needed=raw_task_signal == "keep_trying",
            normalized_child_text=normalized_child_text,
            partial_credit=partial_credit,
            matched_completion_points=matched_completion_points,
            missing_completion_points=missing_completion_points,
            engagement_state=engagement_state,
        )

    @staticmethod
    def _build_task_signal_system_prompt() -> str:
        return (
            "你负责判断孩子当前这句话有没有完成任务。"
            "优先按语义理解，不要死卡关键词。"
            "口语、近义词、同音词、谐音词、半句、ASR 误听都尽量放过去理解。"
            "task_completed=已完成；keep_trying=还没完成；end_session=明确不想继续。"
            "只输出 JSON："
            '{"task_signal":"keep_trying|task_completed|end_session","matched_completion_points":["label"],"partial_credit":true,"engagement_state":"engaged","confidence":0.0,"reason":"..."}'
        )

    @classmethod
    def _build_task_signal_user_prompt(
        cls,
        *,
        child_input_text: str,
        current_task: TaskContext,
    ) -> str:
        payload = {
            "task": {
                "id": current_task.task_id,
                "name": current_task.task_name,
                "goal": current_task.task_goal,
                "action": current_task.expected_child_action,
                "points": [cls._completion_point_to_dict(point) for point in current_task.completion_points],
                "hints": list(build_task_oral_hints(current_task)),
                "scene": current_task.scene_context,
            },
            "child": child_input_text,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
