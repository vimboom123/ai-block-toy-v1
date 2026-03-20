from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


DEFAULT_ARK_API_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ARK_API_CHAT_PATH = "/chat/completions"
DEFAULT_ARK_RESPONSES_MAX_OUTPUT_TOKENS = 1000
DEFAULT_ARK_RESPONSES_REASONING_EFFORT = "low"


class ArkConfigError(RuntimeError):
    """Raised when Ark runtime configuration is incomplete."""


class ArkRequestError(RuntimeError):
    """Raised when the Ark API call fails."""


def _api_mode_for_url(request_url: str) -> str:
    path = parse.urlparse(request_url).path.rstrip("/")
    if path.endswith("/responses"):
        return "responses"
    return "chat_completions"


@dataclass(frozen=True)
class ArkConfig:
    api_key: str
    model: str
    request_url: str
    temperature: float = 0.3
    timeout_seconds: float = 30.0
    max_tokens: int = 300
    reasoning_effort: str | None = DEFAULT_ARK_RESPONSES_REASONING_EFFORT

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "ArkConfig":
        api_key = (env.get("ARK_API_KEY") or "").strip()
        if not api_key:
            raise ArkConfigError("Missing ARK_API_KEY. Put it in .env.local or the shell env.")

        model = (
            env.get("ARK_MODEL")
            or env.get("ARK_MODEL_ID")
            or env.get("ARK_ENDPOINT_ID")
            or env.get("ARK_MODEL_ENDPOINT")
            or ""
        ).strip()
        if not model:
            raise ArkConfigError(
                "Missing ARK model id. Set ARK_MODEL, ARK_MODEL_ID, or ARK_ENDPOINT_ID."
            )

        request_url = (env.get("ARK_REQUEST_URL") or env.get("ARK_CHAT_COMPLETIONS_URL") or "").strip()
        if not request_url:
            base_url = (env.get("ARK_API_BASE_URL") or DEFAULT_ARK_API_BASE_URL).strip()
            chat_path = (env.get("ARK_API_CHAT_PATH") or DEFAULT_ARK_API_CHAT_PATH).strip()
            request_url = parse.urljoin(base_url.rstrip("/") + "/", chat_path.lstrip("/"))

        api_mode = _api_mode_for_url(request_url)
        temperature = float(env.get("ARK_TEMPERATURE", "0.3"))
        timeout_seconds = float(env.get("ARK_TIMEOUT_SECONDS", "30"))
        default_max_tokens = (
            str(DEFAULT_ARK_RESPONSES_MAX_OUTPUT_TOKENS)
            if api_mode == "responses"
            else "300"
        )
        max_tokens = int(env.get("ARK_MAX_TOKENS", default_max_tokens))
        if api_mode == "responses":
            max_tokens = max(max_tokens, DEFAULT_ARK_RESPONSES_MAX_OUTPUT_TOKENS)
        reasoning_effort = (
            env.get(
                "ARK_REASONING_EFFORT",
                DEFAULT_ARK_RESPONSES_REASONING_EFFORT if api_mode == "responses" else "",
            ).strip()
            or None
        )

        return cls(
            api_key=api_key,
            model=model,
            request_url=request_url,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    @property
    def api_mode(self) -> str:
        return _api_mode_for_url(self.request_url)


@dataclass(frozen=True)
class ArkChatResult:
    response_json: dict[str, Any]
    content_text: str


class ArkClient:
    def __init__(self, config: ArkConfig):
        self.config = config

    def create_chat_completion(self, messages: list[dict[str, str]]) -> ArkChatResult:
        payload = self._build_payload(messages)
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
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ArkRequestError(f"Ark API HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise ArkRequestError(f"Ark API connection failed: {exc.reason}") from exc

        data = json.loads(raw)
        content = self._extract_text_content(data)

        return ArkChatResult(response_json=data, content_text=content)

    def _build_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self.config.api_mode == "responses":
            instructions = "\n\n".join(
                message["content"].strip()
                for message in messages
                if message.get("role") in {"system", "developer"} and message.get("content", "").strip()
            )
            input_items: list[dict[str, str]] = []
            for message in messages:
                role = message.get("role") or "user"
                content = message.get("content", "").strip()
                if not content or role in {"system", "developer"}:
                    continue
                input_items.append({"role": role, "content": content})
            if not input_items:
                input_value: str | list[dict[str, str]] = ""
            elif len(input_items) == 1 and input_items[0]["role"] == "user":
                input_value = input_items[0]["content"]
            else:
                input_value = input_items

            payload: dict[str, Any] = {
                "model": self.config.model,
                "input": input_value,
                "temperature": self.config.temperature,
                "max_output_tokens": max(
                    self.config.max_tokens,
                    DEFAULT_ARK_RESPONSES_MAX_OUTPUT_TOKENS,
                ),
            }
            if instructions:
                payload["instructions"] = instructions
            reasoning_effort = self.config.reasoning_effort or DEFAULT_ARK_RESPONSES_REASONING_EFFORT
            if reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}
            return payload

        return {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

    def _extract_text_content(self, response_json: dict[str, Any]) -> str:
        chat_text = self._extract_chat_completion_text(response_json)
        if chat_text:
            return chat_text

        responses_text = self._extract_responses_text(response_json)
        if responses_text:
            return responses_text

        error_message = self._extract_response_error(response_json)
        if error_message:
            raise ArkRequestError(error_message)

        raise ArkRequestError("Ark response missing usable text output.")

    def _extract_chat_completion_text(self, response_json: dict[str, Any]) -> str | None:
        try:
            content = response_json["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        return self._flatten_text_content(content)

    def _extract_responses_text(self, response_json: dict[str, Any]) -> str | None:
        top_level_text = self._flatten_text_content(response_json.get("output_text"))
        if top_level_text:
            return top_level_text

        output = response_json.get("output")
        if not isinstance(output, list):
            return None

        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            text = self._flatten_text_content(item.get("content")) or self._flatten_text_content(item)
            if text:
                parts.append(text)

        if parts:
            return "\n".join(parts)
        return None

    def _extract_response_error(self, response_json: dict[str, Any]) -> str | None:
        error_payload = response_json.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message") or json.dumps(error_payload, ensure_ascii=False)
            return f"Ark response error: {message}"
        if isinstance(error_payload, str) and error_payload.strip():
            return f"Ark response error: {error_payload.strip()}"

        status = response_json.get("status")
        if status in {"failed", "incomplete", "cancelled"}:
            details = response_json.get("incomplete_details")
            if details:
                return f"Ark response status={status}: {json.dumps(details, ensure_ascii=False)}"
            return f"Ark response status={status}"
        return None

    def _flatten_text_content(self, content: Any) -> str | None:
        if isinstance(content, str):
            text = content.strip()
            return text or None

        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            nested_text = content.get("content")
            if isinstance(nested_text, str) and nested_text.strip():
                return nested_text.strip()
            if isinstance(text, dict):
                value = text.get("value")
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                flattened = self._flatten_text_content(item)
                if flattened:
                    parts.append(flattened)
            if parts:
                return "\n".join(parts)
        return None
