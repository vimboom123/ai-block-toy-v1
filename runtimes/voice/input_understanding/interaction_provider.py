from __future__ import annotations

import json
import re
import socket
import signal
import sys
import threading
from dataclasses import dataclass, replace
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request

from .models import (
    InteractionContext,
    VALID_ENGAGEMENT_STATES,
    VALID_TASK_SIGNALS,
)
from .task_oral_hints import build_task_oral_hints

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.ark_client import ArkClient, ArkConfig, ArkConfigError, ArkRequestError  # type: ignore[import-not-found]
from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]

JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
MAX_PROVIDER_TIMEOUT_SECONDS = 20.0
DEFAULT_OPENAI_COMPATIBLE_CHAT_PATH = "/chat/completions"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen-plus"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.5"


def describe_provider_failure(exc: BaseException) -> str:
    message = str(exc).strip()
    if isinstance(exc, socket.timeout):
        return f"socket timeout: {message or 'timed out'}"
    if isinstance(exc, TimeoutError):
        return f"timeout: {message or 'timed out'}"
    if isinstance(exc, ArkConfigError):
        return f"Ark config error: {message or exc.__class__.__name__}"
    if isinstance(exc, ArkRequestError):
        return f"Ark request error: {message or exc.__class__.__name__}"
    if isinstance(exc, OpenAICompatibleConfigError):
        return f"OpenAI-compatible config error: {message or exc.__class__.__name__}"
    if isinstance(exc, OpenAICompatibleRequestError):
        return f"OpenAI-compatible request error: {message or exc.__class__.__name__}"
    if isinstance(exc, json.JSONDecodeError):
        return f"Provider JSON decode error: {message or exc.__class__.__name__}"
    if isinstance(exc, ValueError):
        return f"Provider response error: {message or exc.__class__.__name__}"
    return f"Provider runtime error ({exc.__class__.__name__}): {message or exc.__class__.__name__}"


def is_retryable_provider_failure(exc: BaseException) -> bool:
    if isinstance(exc, ArkConfigError):
        return False
    if isinstance(exc, OpenAICompatibleConfigError):
        return False
    if isinstance(exc, (socket.timeout, TimeoutError, ArkRequestError, json.JSONDecodeError, ValueError)):
        return True
    if isinstance(exc, OpenAICompatibleRequestError):
        return True
    return True


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(candidate)
        if not match:
            raise
        loaded = json.loads(match.group(0))

    if not isinstance(loaded, dict):
        raise ValueError("Model response JSON must decode to an object.")
    return loaded


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_non_empty(env: dict[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        value = (env.get(key) or "").strip()
        if value:
            return value
    return ""


def _format_env_keys(keys: Iterable[str]) -> str:
    return ", ".join(key for key in keys if key)


def _parse_float_env(
    env: dict[str, str],
    keys: Iterable[str],
    default: float,
    *,
    field_name: str,
) -> float:
    raw_value = _first_non_empty(env, keys)
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError as exc:
        raise OpenAICompatibleConfigError(f"Invalid {field_name}: {raw_value}") from exc


def _parse_int_env(
    env: dict[str, str],
    keys: Iterable[str],
    default: int | None,
    *,
    field_name: str,
) -> int | None:
    raw_value = _first_non_empty(env, keys)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise OpenAICompatibleConfigError(f"Invalid {field_name}: {raw_value}") from exc


def _flatten_text_content(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _flatten_text_content(item)
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts)
        return None
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key not in value:
                continue
            text = _flatten_text_content(value.get(key))
            if text:
                return text
    return None


@dataclass(frozen=True)
class InteractionDraft:
    reply_text: str
    acknowledged_child_point: str | None = None
    followup_question: str | None = None
    provider_name: str | None = None


@dataclass(frozen=True)
class UnifiedTurnDraft:
    task_signal: str
    reason: str
    confidence: float
    reply_text: str
    matched_completion_points: tuple[str, ...] = ()
    partial_credit: bool = False
    engagement_state: str = "unknown"
    acknowledged_child_point: str | None = None
    followup_question: str | None = None
    provider_name: str | None = None


@dataclass(frozen=True)
class ProviderRequestOptions:
    timeout_seconds: float | None = None
    prompt_variant: str = "default"
    retry_hint: str | None = None


class InteractionProviderError(RuntimeError):
    """Raised when a natural-language provider is unavailable or returns unusable output."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class OpenAICompatibleConfigError(RuntimeError):
    """Raised when an OpenAI-compatible provider config is incomplete."""


class OpenAICompatibleRequestError(RuntimeError):
    """Raised when an OpenAI-compatible provider request fails."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    api_key: str
    model: str
    request_url: str
    temperature: float = 0.3
    timeout_seconds: float | None = 30.0
    max_tokens: int | None = 300
    provider_label: str = "OpenAI-compatible"

    @classmethod
    def from_env(
        cls,
        env: dict[str, str],
        *,
        provider_label: str,
        api_key_env_keys: tuple[str, ...],
        model_env_keys: tuple[str, ...],
        base_url_env_keys: tuple[str, ...],
        request_url_env_keys: tuple[str, ...] = (),
        timeout_env_keys: tuple[str, ...] = (),
        max_tokens_env_keys: tuple[str, ...] = (),
        temperature_env_keys: tuple[str, ...] = (),
        default_base_url: str,
        default_model: str,
        default_timeout_seconds: float = 30.0,
        default_max_tokens: int | None = 300,
        default_temperature: float = 0.3,
    ) -> "OpenAICompatibleConfig":
        api_key = _first_non_empty(env, api_key_env_keys)
        if not api_key:
            env_keys = _format_env_keys(api_key_env_keys)
            raise OpenAICompatibleConfigError(
                f"Missing {provider_label} API key. Set one of: {env_keys}."
            )

        model = _first_non_empty(env, model_env_keys) or default_model
        if not model:
            env_keys = _format_env_keys(model_env_keys)
            raise OpenAICompatibleConfigError(
                f"Missing {provider_label} model. Set one of: {env_keys}."
            )

        request_url = _first_non_empty(env, request_url_env_keys)
        if not request_url:
            base_url = _first_non_empty(env, base_url_env_keys) or default_base_url
            if not base_url:
                env_keys = _format_env_keys(base_url_env_keys)
                raise OpenAICompatibleConfigError(
                    f"Missing {provider_label} base URL. Set one of: {env_keys}."
                )
            request_url = parse.urljoin(
                base_url.rstrip("/") + "/",
                DEFAULT_OPENAI_COMPATIBLE_CHAT_PATH.lstrip("/"),
            )

        return cls(
            api_key=api_key,
            model=model,
            request_url=request_url,
            temperature=_parse_float_env(
                env,
                temperature_env_keys,
                default_temperature,
                field_name=f"{provider_label} temperature",
            ),
            timeout_seconds=_parse_float_env(
                env,
                timeout_env_keys,
                default_timeout_seconds,
                field_name=f"{provider_label} timeout",
            ),
            max_tokens=_parse_int_env(
                env,
                max_tokens_env_keys,
                default_max_tokens,
                field_name=f"{provider_label} max tokens",
            ),
            provider_label=provider_label,
        )


@dataclass(frozen=True)
class OpenAICompatibleChatResult:
    response_json: dict[str, Any]
    content_text: str


class OpenAICompatibleClient:
    def __init__(self, config: OpenAICompatibleConfig):
        self.config = config

    def create_chat_completion(self, messages: list[dict[str, str]]) -> OpenAICompatibleChatResult:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": messages,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.config.request_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with _wall_clock_timeout(self.config.timeout_seconds):
                if self.config.timeout_seconds is None or self.config.timeout_seconds <= 0:
                    with request.urlopen(req) as response:
                        raw = response.read().decode("utf-8")
                else:
                    with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                        raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleRequestError(
                f"{self.config.provider_label} API HTTP {exc.code}: {detail}"
            ) from exc
        except TimeoutError as exc:
            raise OpenAICompatibleRequestError(
                f"{self.config.provider_label} total timeout after {self.config.timeout_seconds:g}s"
            ) from exc
        except error.URLError as exc:
            raise OpenAICompatibleRequestError(
                f"{self.config.provider_label} API connection failed: {exc.reason}"
            ) from exc

        data = json.loads(raw)
        content = self._extract_text_content(data)
        return OpenAICompatibleChatResult(response_json=data, content_text=content)

    def _extract_text_content(self, response_json: dict[str, Any]) -> str:
        try:
            content = response_json["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None

        text = _flatten_text_content(content)
        if text:
            return text

        error_payload = response_json.get("error")
        if isinstance(error_payload, dict):
            message = _optional_string(error_payload.get("message"))
            if message:
                raise OpenAICompatibleRequestError(message)
        elif isinstance(error_payload, str) and error_payload.strip():
            raise OpenAICompatibleRequestError(error_payload.strip())

        raise OpenAICompatibleRequestError(
            f"{self.config.provider_label} response missing usable text output."
        )


class BaseInteractionProvider:
    provider_name = "custom_provider"
    provider_label = "Provider"

    def generate_reply(
        self,
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None = None,
    ) -> InteractionDraft:
        if getattr(self, "setup_error", None):
            raise self.setup_error  # type: ignore[misc]

        try:
            content_text = self._request_model_text(
                interaction_context=interaction_context,
                request_options=request_options,
            )
            payload = _extract_json_object(content_text)

            reply_text = _optional_string(payload.get("reply_text"))
            if reply_text is None:
                raise ValueError(f"{self.provider_label} response missing reply_text.")

            return InteractionDraft(
                reply_text=reply_text,
                acknowledged_child_point=(
                    _optional_string(payload.get("acknowledged_child_point"))
                    or interaction_context.preferred_acknowledged_child_point
                ),
                followup_question=(
                    _optional_string(payload.get("followup_question"))
                    or interaction_context.preferred_followup_question
                ),
                provider_name=self.provider_name,
            )
        except InteractionProviderError:
            raise
        except Exception as exc:
            raise InteractionProviderError(
                describe_provider_failure(exc),
                retryable=is_retryable_provider_failure(exc),
            ) from exc

    @staticmethod
    def _validate_timeout_seconds(timeout_seconds: float | None, *, field_name: str) -> float | None:
        if timeout_seconds is None:
            return None
        normalized_timeout = float(timeout_seconds)
        if normalized_timeout < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
        if normalized_timeout == 0:
            return None
        return normalized_timeout

    def _resolve_timeout_seconds(self, requested_timeout_seconds: float | None) -> float | None:
        if requested_timeout_seconds is None:
            default_timeout_seconds = getattr(self, "default_timeout_seconds", None)
            if default_timeout_seconds is None:
                return getattr(self, "max_timeout_seconds", MAX_PROVIDER_TIMEOUT_SECONDS)
            return min(default_timeout_seconds, getattr(self, "max_timeout_seconds", MAX_PROVIDER_TIMEOUT_SECONDS))
        normalized_timeout = self._validate_timeout_seconds(
            requested_timeout_seconds,
            field_name="timeout_seconds",
        )
        if normalized_timeout is None:
            return None
        return min(normalized_timeout, getattr(self, "max_timeout_seconds", MAX_PROVIDER_TIMEOUT_SECONDS))

    @staticmethod
    def _build_system_prompt(
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None,
    ) -> str:
        prompt_variant = request_options.prompt_variant if request_options is not None else "default"
        if prompt_variant == "relaxed_keep_trying":
            variant_line = "keep_trying 重试：先接孩子，再用一个明确问句收住；默认 1 到 2 句，尽量短。"
        elif prompt_variant == "fast_path":
            variant_line = "fast_path：自然、具体，但默认 1 到 2 句；如果需要继续推进，最后给一个明确问句。"
        else:
            variant_line = "keep_trying：优先像故事陪玩一样自然口语，但默认只说 1 到 2 句；先接住孩子，再用最后一个明确问句拉回当前任务。"

        scene_context = interaction_context.scene_context or f"{interaction_context.task_name} 这个场景"
        session_memory = (
            interaction_context.session_memory
            or interaction_context.recent_turn_summary
            or "暂无上一轮记忆"
        )
        completion_points = interaction_context.completion_points
        completion_focus = (
            "；".join(
                f"{point.label}({','.join(point.keywords)})"
                for point in completion_points
            )
            if completion_points
            else "暂无"
        )

        return "\n".join(
            [
                "你是中文陪玩引导员，不是老师。",
                "你在讲连贯的小故事，当前任务只是一个关卡。",
                f"故事背景：{scene_context}",
                f"原始任务目标：{interaction_context.task_goal or interaction_context.interaction_goal}",
                f"故事目标：{interaction_context.interaction_goal}",
                f"任务完成点：{completion_focus}",
                f"会话记忆：{session_memory}",
                f"必须带回的动作：{interaction_context.expected_child_action}",
                "孩子偏题时，先接住，再自然拉回。",
                "scene_context 只做故事底色，不要把“背景上有什么/场景里有什么”当成默认提问方向。",
                "只在原始任务目标明确要求识别背景或场景细节时才问背景；否则优先围绕任务完成点提问。",
                "优先用任务完成点里的关键词来问，不要自己发散成泛背景问题。",
                "优先顺着 reply.ack 和 reply.ask，但可以自然改写。",
                "reply_text 优先由模型自由生成，不要套固定句式；每次尽量换说法。",
                "除开场外，reply_text 默认控制在 1 到 2 句、35 到 70 个汉字；不要越说越长。",
                "可以带一点现场画面、动作感和情绪反应，但不要离开当前任务。",
                "keep_trying 时只保留一个明确问句，放在 reply_text 最后；不要连续追问两三个问题。",
                "如果当前任务本身是二选一或少数选项，就直接把选项说出来，例如“是大火还是小火”。",
                "如果有 followup_question，就把它放在 reply_text 的末尾。",
                "不要像老师点名或填空，禁用“你来告诉我 / 请回答 / 你来试试 / 跟我说”。",
                "禁用空洞收口：不要只说“没错 / 对啦 / 答对啦 / 这一步就是...”。",
                "不要解释规则，不要复述任务说明。",
                variant_line,
                "只输出 JSON：reply_text, acknowledged_child_point, followup_question。",
                "reply_text 必须非空；其余字段可以是空字符串。",
            ]
        )

    @staticmethod
    def _build_user_prompt(
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None,
    ) -> str:
        payload: dict[str, Any] = interaction_context.to_prompt_payload()
        if request_options is not None and request_options.retry_hint:
            payload["retry_hint"] = request_options.retry_hint
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _request_model_text(
        self,
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None,
    ) -> str:
        raise NotImplementedError


class ArkDoubaoInteractionProvider(BaseInteractionProvider):
    provider_name = "ark_doubao"
    provider_label = "Ark/Doubao"

    def __init__(
        self,
        *,
        root_dir: Path = PHASE5_ROOT_DIR,
        dotenv_file: str = ".env.local",
        request_timeout_seconds: float | None = None,
        max_timeout_seconds: float = MAX_PROVIDER_TIMEOUT_SECONDS,
    ):
        self.root_dir = root_dir
        self.dotenv_file = dotenv_file
        self.max_timeout_seconds = self._validate_timeout_seconds(
            max_timeout_seconds,
            field_name="max_timeout_seconds",
        )
        self.default_timeout_seconds: float | None = None
        self.ark_config: ArkConfig | None = None
        self.setup_error: InteractionProviderError | None = None

        try:
            runtime_env = build_runtime_env(self.root_dir / self.dotenv_file)
            ark_config = ArkConfig.from_env(runtime_env)
            self.default_timeout_seconds = self._resolve_timeout_seconds(
                request_timeout_seconds if request_timeout_seconds is not None else ark_config.timeout_seconds
            )
            self.ark_config = replace(
                ark_config,
                timeout_seconds=self.default_timeout_seconds,
            )
        except Exception as exc:
            self.setup_error = InteractionProviderError(
                describe_provider_failure(exc),
                retryable=is_retryable_provider_failure(exc),
            )

    def _request_model_text(
        self,
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None = None,
    ) -> str:
        if self.ark_config is None:
            raise InteractionProviderError("Ark/Doubao provider is not ready.", retryable=False)

        effective_timeout_seconds = self._resolve_timeout_seconds(
            request_options.timeout_seconds if request_options is not None else None
        )
        client = ArkClient(
            replace(
                self.ark_config,
                timeout_seconds=effective_timeout_seconds,
            )
        )
        system_prompt = self._build_system_prompt(interaction_context=interaction_context, request_options=request_options)
        user_prompt = self._build_user_prompt(interaction_context=interaction_context, request_options=request_options)

        try:
            chat_result = client.create_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"{user_prompt}\n\n请按上面的规则输出这一轮自然回复。",
                    },
                ]
            )
            return chat_result.content_text
        except Exception as exc:
            raise InteractionProviderError(
                describe_provider_failure(exc),
                retryable=is_retryable_provider_failure(exc),
            ) from exc

class OpenAICompatibleInteractionProvider(BaseInteractionProvider):
    api_key_env_keys: tuple[str, ...] = ()
    model_env_keys: tuple[str, ...] = ()
    base_url_env_keys: tuple[str, ...] = ()
    request_url_env_keys: tuple[str, ...] = ()
    timeout_env_keys: tuple[str, ...] = ()
    max_tokens_env_keys: tuple[str, ...] = ()
    temperature_env_keys: tuple[str, ...] = ()
    default_base_url = ""
    default_model = ""
    default_max_tokens = 300
    default_temperature = 0.3

    def __init__(
        self,
        *,
        root_dir: Path = PHASE5_ROOT_DIR,
        dotenv_file: str = ".env.local",
        request_timeout_seconds: float | None = None,
        max_timeout_seconds: float = MAX_PROVIDER_TIMEOUT_SECONDS,
    ):
        self.root_dir = root_dir
        self.dotenv_file = dotenv_file
        self.max_timeout_seconds = self._validate_timeout_seconds(
            max_timeout_seconds,
            field_name="max_timeout_seconds",
        )
        self.default_timeout_seconds: float | None = None
        self.config: OpenAICompatibleConfig | None = None
        self.setup_error: InteractionProviderError | None = None

        try:
            runtime_env = build_runtime_env(self.root_dir / self.dotenv_file)
            config = OpenAICompatibleConfig.from_env(
                runtime_env,
                provider_label=self.provider_label,
                api_key_env_keys=self.api_key_env_keys,
                model_env_keys=self.model_env_keys,
                base_url_env_keys=self.base_url_env_keys,
                request_url_env_keys=self.request_url_env_keys,
                timeout_env_keys=self.timeout_env_keys,
                max_tokens_env_keys=self.max_tokens_env_keys,
                temperature_env_keys=self.temperature_env_keys,
                default_base_url=self.default_base_url,
                default_model=self.default_model,
                default_max_tokens=self.default_max_tokens,
                default_temperature=self.default_temperature,
            )
            self.default_timeout_seconds = self._resolve_timeout_seconds(
                request_timeout_seconds if request_timeout_seconds is not None else config.timeout_seconds
            )
            self.config = replace(config, timeout_seconds=self.default_timeout_seconds)
        except Exception as exc:
            self.setup_error = InteractionProviderError(
                describe_provider_failure(exc),
                retryable=is_retryable_provider_failure(exc),
            )

    def _request_model_text(
        self,
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None = None,
    ) -> str:
        if self.config is None:
            raise InteractionProviderError(f"{self.provider_label} provider is not ready.", retryable=False)

        effective_timeout_seconds = self._resolve_timeout_seconds(
            request_options.timeout_seconds if request_options is not None else None
        )
        client = OpenAICompatibleClient(
            replace(
                self.config,
                timeout_seconds=effective_timeout_seconds,
            )
        )
        system_prompt = self._build_system_prompt(interaction_context=interaction_context, request_options=request_options)
        user_prompt = self._build_user_prompt(interaction_context=interaction_context, request_options=request_options)
        result = client.create_chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"{user_prompt}\n\n请按上面的规则输出这一轮自然回复。",
                },
            ]
        )
        return result.content_text


@contextmanager
def _wall_clock_timeout(timeout_seconds: float | None):
    if timeout_seconds is None or timeout_seconds <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)

    def _handle_timeout(signum, frame):  # type: ignore[no-untyped-def]
        del signum, frame
        raise TimeoutError(f"wall clock timeout after {timeout_seconds:g}s")

    signal.signal(signal.SIGALRM, _handle_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


class QwenInteractionProvider(OpenAICompatibleInteractionProvider):
    provider_name = "qwen"
    provider_label = "Qwen"
    api_key_env_keys = ("QWEN_API_KEY", "DASHSCOPE_API_KEY")
    model_env_keys = ("QWEN_MODEL", "DASHSCOPE_MODEL")
    base_url_env_keys = ("QWEN_BASE_URL", "DASHSCOPE_BASE_URL")
    request_url_env_keys = ("QWEN_REQUEST_URL", "DASHSCOPE_REQUEST_URL", "DASHSCOPE_CHAT_COMPLETIONS_URL")
    timeout_env_keys = ("QWEN_TIMEOUT_SECONDS", "DASHSCOPE_TIMEOUT_SECONDS")
    max_tokens_env_keys = ("QWEN_MAX_TOKENS", "DASHSCOPE_MAX_TOKENS")
    temperature_env_keys = ("QWEN_TEMPERATURE", "DASHSCOPE_TEMPERATURE")
    default_base_url = DEFAULT_QWEN_BASE_URL
    default_model = DEFAULT_QWEN_MODEL
    default_max_tokens = None
    default_temperature = 0.4


class QwenUnifiedTurnProvider(QwenInteractionProvider):
    provider_name = "qwen_unified"

    def generate_turn(
        self,
        *,
        child_input_text: str,
        current_task: Any,
        session_memory_summary: str | None = None,
        next_task_hint: Any | None = None,
        timeout_seconds: float | None = None,
    ) -> UnifiedTurnDraft:
        if self.config is None:
            raise InteractionProviderError(f"{self.provider_label} provider is not ready.", retryable=False)

        effective_timeout_seconds = self._resolve_timeout_seconds(timeout_seconds)
        client = OpenAICompatibleClient(
            replace(
                self.config,
                timeout_seconds=effective_timeout_seconds,
            )
        )
        try:
            result = client.create_chat_completion(
                [
                    {
                        "role": "system",
                        "content": self._build_unified_turn_system_prompt(next_task_hint=next_task_hint),
                    },
                    {
                        "role": "user",
                        "content": self._build_unified_turn_user_prompt(
                            child_input_text=child_input_text,
                            current_task=current_task,
                            session_memory_summary=session_memory_summary,
                            next_task_hint=next_task_hint,
                        ),
                    },
                ]
            )
            payload = _extract_json_object(result.content_text)

            task_signal = _optional_string(payload.get("task_signal")) or "keep_trying"
            if task_signal not in VALID_TASK_SIGNALS:
                raise ValueError(f"Unsupported task_signal: {task_signal}")
            if task_signal not in getattr(current_task, "allowed_signals", VALID_TASK_SIGNALS):
                raise ValueError(f"task_signal {task_signal} not allowed for current task")

            matched_completion_points = self._coerce_matched_completion_points(
                payload.get("matched_completion_points"),
                current_task=current_task,
            )
            if task_signal == "task_completed" and not matched_completion_points and getattr(
                current_task, "completion_points", ()
            ):
                matched_completion_points = tuple(
                    point.label for point in getattr(current_task, "completion_points", ())
                )

            engagement_state = _optional_string(payload.get("engagement_state")) or "unknown"
            if engagement_state not in VALID_ENGAGEMENT_STATES:
                engagement_state = "unknown"

            raw_confidence = payload.get("confidence")
            confidence = (
                min(max(float(raw_confidence), 0.0), 1.0)
                if isinstance(raw_confidence, (int, float))
                else (0.84 if task_signal == "task_completed" else 0.72)
            )
            reason = _optional_string(payload.get("reason")) or "AI 未给出原因。"
            reply_text = _optional_string(payload.get("reply_text"))
            if reply_text is None:
                raise ValueError("Unified turn response missing reply_text")

            return UnifiedTurnDraft(
                task_signal=task_signal,
                reason=reason,
                confidence=confidence,
                reply_text=reply_text,
                matched_completion_points=matched_completion_points,
                partial_credit=bool(payload.get("partial_credit")) if task_signal == "keep_trying" else False,
                engagement_state=engagement_state,
                acknowledged_child_point=_optional_string(payload.get("acknowledged_child_point")),
                followup_question=_optional_string(payload.get("followup_question")),
                provider_name=self.provider_name,
            )
        except InteractionProviderError:
            raise
        except Exception as exc:
            raise InteractionProviderError(
                describe_provider_failure(exc),
                retryable=is_retryable_provider_failure(exc),
            ) from exc

    @staticmethod
    def _build_unified_turn_system_prompt(*, next_task_hint: Any | None) -> str:
        next_stage_rule = (
            "如果 task_signal=task_completed 且给了 next_task_hint，reply_text 必须顺着当前结果自然引到下一阶段，不要只报步骤名。"
            if next_task_hint is not None
            else "如果 task_signal=task_completed 且没有 next_task_hint，就自然庆祝收尾，不要提问，也不要像播报完成提示。"
        )
        return "\n".join(
            [
                "你负责两件事：判断当前任务是否完成，并生成这一轮回复。",
                "优先按语义理解，不要死卡关键词。",
                "口语、同义词、半句、ASR 近音都尽量放过去理解。",
                "task_completed=这句话已经完成当前任务；keep_trying=还没完成但可继续引导；end_session=明确不想继续。",
                next_stage_rule,
                "reply_text 必须像 AI 陪玩在现场说话，不要像流程图、提示器或老师点名。",
                "除开场外，reply_text 默认 1 到 2 句、35 到 70 个汉字；不要越说越长。",
                "尽量把 scene_context、当前火情、孩子刚才的话带进回复，不要每轮都像重新开题。",
                "每次换一种说法，避免反复使用“没错”“对啦”“答对啦”“这一步就是”。",
                "keep_trying 时只保留一个明确问句，放在 reply_text 最后；前面可以先做自然铺垫，不要连问两个问题。",
                "如果当前任务是二选一或少数选项，最后一句直接把选项词说出来，例如“大火还是小火”。",
                "如果还没完成，就只围绕当前任务追问，不要发散。",
                "reason 尽量短，matched_completion_points 尽量只填最相关的标签。",
                "只输出 JSON，不要代码块。",
                'JSON: {"task_signal":"...","matched_completion_points":["label"],"partial_credit":true,"engagement_state":"engaged","confidence":0.0,"reason":"...","reply_text":"...","acknowledged_child_point":"...","followup_question":"..."}',
            ]
        )

    @classmethod
    def _build_unified_turn_user_prompt(
        cls,
        *,
        child_input_text: str,
        current_task: Any,
        session_memory_summary: str | None,
        next_task_hint: Any | None,
    ) -> str:
        payload: dict[str, Any] = {
            "task": cls._build_task_payload(current_task),
            "child": {
                "said": child_input_text,
            },
        }
        if session_memory_summary is not None:
            payload["session_memory"] = session_memory_summary
        if next_task_hint is not None:
            payload["next_task_hint"] = cls._build_task_payload(next_task_hint)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _build_task_payload(task: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": getattr(task, "task_id", ""),
            "name": getattr(task, "task_name", ""),
            "goal": getattr(task, "task_goal", ""),
            "action": getattr(task, "expected_child_action", ""),
            "points": [
                point.to_dict() for point in getattr(task, "completion_points", ())
            ],
            "hints": list(build_task_oral_hints(task)),
        }
        scene_context = getattr(task, "scene_context", None)
        if scene_context:
            payload["scene"] = scene_context
        suggested_followup_question = _optional_string(getattr(task, "suggested_followup_question", None))
        if suggested_followup_question is not None:
            payload["next_q"] = suggested_followup_question
        return payload

    @staticmethod
    def _normalize_label(value: str) -> str:
        return "".join(ch for ch in value.strip().lower() if ch not in {" ", "_", "-", "，", "。", ",", "."})

    @classmethod
    def _coerce_matched_completion_points(
        cls,
        raw_value: Any,
        *,
        current_task: Any,
    ) -> tuple[str, ...]:
        if isinstance(raw_value, str):
            candidates = [raw_value]
        elif isinstance(raw_value, list):
            candidates = [candidate for candidate in raw_value if isinstance(candidate, str)]
        else:
            candidates = []

        completion_point_by_label = {
            cls._normalize_label(point.label): point.label
            for point in getattr(current_task, "completion_points", ())
        }
        completion_point_by_keyword = {
            cls._normalize_label(keyword): point.label
            for point in getattr(current_task, "completion_points", ())
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


class MinimaxInteractionProvider(OpenAICompatibleInteractionProvider):
    provider_name = "minimax"
    provider_label = "MiniMax"
    api_key_env_keys = ("MINIMAX_API_KEY",)
    model_env_keys = ("MINIMAX_MODEL",)
    base_url_env_keys = ("MINIMAX_BASE_URL",)
    request_url_env_keys = ("MINIMAX_REQUEST_URL",)
    timeout_env_keys = ("MINIMAX_TIMEOUT_SECONDS",)
    max_tokens_env_keys = ("MINIMAX_MAX_TOKENS",)
    temperature_env_keys = ("MINIMAX_TEMPERATURE",)
    default_base_url = DEFAULT_MINIMAX_BASE_URL
    default_model = DEFAULT_MINIMAX_MODEL
    default_max_tokens = 100
    default_temperature = 0.4


class AutoInteractionProvider:
    provider_name = "auto"

    def __init__(
        self,
        *,
        providers: tuple[Any, ...] | None = None,
    ):
        self.providers = providers or (
            QwenInteractionProvider(),
            ArkDoubaoInteractionProvider(),
            MinimaxInteractionProvider(),
        )

    def generate_reply(
        self,
        *,
        interaction_context: InteractionContext,
        request_options: ProviderRequestOptions | None = None,
    ) -> InteractionDraft:
        failures: list[str] = []
        for provider in self.providers:
            provider_name = getattr(provider, "provider_name", provider.__class__.__name__)
            try:
                draft = provider.generate_reply(
                    interaction_context=interaction_context,
                    request_options=request_options,
                )
                if draft.provider_name is None:
                    draft = replace(draft, provider_name=provider_name)
                return draft
            except InteractionProviderError as exc:
                failures.append(f"{provider_name}: {exc}")
            except Exception as exc:
                failures.append(f"{provider_name}: {describe_provider_failure(exc)}")
        raise InteractionProviderError(
            "All auto providers failed. " + "; ".join(failures),
            retryable=True,
        )


def build_interaction_provider(provider_mode: str) -> Any | None:
    if provider_mode == "template":
        return None
    if provider_mode == "qwen":
        return QwenInteractionProvider()
    if provider_mode == "minimax":
        return MinimaxInteractionProvider()
    if provider_mode == "ark_doubao":
        return ArkDoubaoInteractionProvider()
    if provider_mode == "auto":
        return AutoInteractionProvider()
    raise ValueError("provider_mode must be one of: qwen, minimax, ark_doubao, template, auto")


def build_unified_turn_provider(provider_mode: str) -> Any | None:
    if provider_mode in {"qwen", "auto"}:
        return QwenUnifiedTurnProvider()
    return None
