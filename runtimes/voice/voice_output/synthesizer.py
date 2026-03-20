from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request

from .models import SynthesizedSpeech

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]

DEFAULT_DASHSCOPE_TTS_REQUEST_PATH = "/services/aigc/multimodal-generation/generation"
LEGACY_OPENAI_COMPATIBLE_SPEECH_PATH = "/audio/speech"
LEGACY_OPENAI_COMPATIBLE_BASE_SUFFIX = "/compatible-mode/v1"
DEFAULT_QWEN_TTS_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_QWEN_TTS_MODEL = "qwen-tts-latest"
DEFAULT_QWEN_TTS_VOICE = "Cherry"
DEFAULT_QWEN_TTS_FORMAT = "wav"
DEFAULT_QWEN_TTS_LANGUAGE_TYPE = "Chinese"
MAX_TTS_TIMEOUT_SECONDS = 60.0


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
        raise OpenAICompatibleSpeechConfigError(f"Invalid {field_name}: {raw_value}") from exc


def _infer_audio_format(content_type: str | None, default_format: str) -> str:
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type.startswith("audio/"):
        inferred = normalized_content_type.split("/", 1)[1].strip()
        if inferred in {"mpeg", "mp3"}:
            return "mp3"
        if inferred:
            return inferred
    return default_format


def _infer_audio_format_from_url(url: str | None, default_format: str) -> str:
    if not url:
        return default_format

    path = parse.urlparse(url).path
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in {"mpeg", "mp3"}:
        return "mp3"
    if suffix:
        return suffix
    return default_format


def _normalize_output_path(output_path: str | Path | None, *, audio_format: str, prefix: str) -> Path:
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path("/tmp") / f"{prefix}-{timestamp}.{audio_format}"
    else:
        path = Path(output_path).expanduser()
        expected_suffix = f".{audio_format.lower()}"
        if path.suffix.lower() != expected_suffix:
            path = path.with_suffix(expected_suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _normalize_dashscope_tts_base_url(base_url: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url:
        return normalized_base_url
    if normalized_base_url.endswith(LEGACY_OPENAI_COMPATIBLE_BASE_SUFFIX):
        parsed = parse.urlparse(normalized_base_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/api/v1"
    return normalized_base_url


def _normalize_dashscope_tts_request_url(request_url: str) -> str:
    normalized_request_url = request_url.strip()
    if not normalized_request_url:
        return normalized_request_url
    parsed = parse.urlparse(normalized_request_url)
    if parsed.path.endswith(LEGACY_OPENAI_COMPATIBLE_SPEECH_PATH):
        normalized_base_url = f"{parsed.scheme}://{parsed.netloc}/api/v1"
        return parse.urljoin(
            normalized_base_url.rstrip("/") + "/",
            DEFAULT_DASHSCOPE_TTS_REQUEST_PATH.lstrip("/"),
        )
    return normalized_request_url


def _maybe_extract_base64_audio(payload: Any) -> bytes | None:
    if isinstance(payload, str):
        try:
            return base64.b64decode(payload, validate=True)
        except Exception:
            return None

    if isinstance(payload, list):
        for item in payload:
            audio_bytes = _maybe_extract_base64_audio(item)
            if audio_bytes:
                return audio_bytes
        return None

    if not isinstance(payload, dict):
        return None

    for key in ("audio", "audio_base64", "b64_audio", "data", "output_audio"):
        if key not in payload:
            continue
        audio_bytes = _maybe_extract_base64_audio(payload[key])
        if audio_bytes:
            return audio_bytes
    return None


class SpeechSynthesisError(RuntimeError):
    """Raised when TTS synthesis fails or no provider can return audio."""


class OpenAICompatibleSpeechConfigError(RuntimeError):
    """Raised when the DashScope speech config is incomplete."""


class OpenAICompatibleSpeechRequestError(RuntimeError):
    """Raised when the DashScope speech request fails."""


@dataclass(frozen=True)
class OpenAICompatibleSpeechConfig:
    api_key: str
    model: str
    request_url: str
    voice: str
    audio_format: str = DEFAULT_QWEN_TTS_FORMAT
    language_type: str = DEFAULT_QWEN_TTS_LANGUAGE_TYPE
    timeout_seconds: float = 30.0
    provider_label: str = "DashScope speech"

    @classmethod
    def from_env(
        cls,
        env: dict[str, str],
        *,
        provider_label: str,
        api_key_env_keys: tuple[str, ...],
        model_env_keys: tuple[str, ...],
        base_url_env_keys: tuple[str, ...],
        request_url_env_keys: tuple[str, ...],
        voice_env_keys: tuple[str, ...],
        audio_format_env_keys: tuple[str, ...],
        language_type_env_keys: tuple[str, ...],
        timeout_env_keys: tuple[str, ...],
        default_base_url: str,
        default_model: str,
        default_voice: str,
        default_audio_format: str,
        default_language_type: str,
        default_timeout_seconds: float = 30.0,
    ) -> "OpenAICompatibleSpeechConfig":
        api_key = _first_non_empty(env, api_key_env_keys)
        if not api_key:
            env_keys = _format_env_keys(api_key_env_keys)
            raise OpenAICompatibleSpeechConfigError(
                f"Missing {provider_label} API key. Set one of: {env_keys}."
            )

        model = _first_non_empty(env, model_env_keys) or default_model
        if not model:
            env_keys = _format_env_keys(model_env_keys)
            raise OpenAICompatibleSpeechConfigError(
                f"Missing {provider_label} model. Set one of: {env_keys}."
            )

        request_url = _first_non_empty(env, request_url_env_keys)
        if not request_url:
            base_url = _normalize_dashscope_tts_base_url(_first_non_empty(env, base_url_env_keys) or default_base_url)
            if not base_url:
                env_keys = _format_env_keys(base_url_env_keys)
                raise OpenAICompatibleSpeechConfigError(
                    f"Missing {provider_label} base URL. Set one of: {env_keys}."
                )
            request_url = parse.urljoin(
                base_url.rstrip("/") + "/",
                DEFAULT_DASHSCOPE_TTS_REQUEST_PATH.lstrip("/"),
            )
        request_url = _normalize_dashscope_tts_request_url(request_url)

        voice = _first_non_empty(env, voice_env_keys) or default_voice
        if not voice:
            env_keys = _format_env_keys(voice_env_keys)
            raise OpenAICompatibleSpeechConfigError(
                f"Missing {provider_label} voice. Set one of: {env_keys}."
            )

        return cls(
            api_key=api_key,
            model=model,
            request_url=request_url,
            voice=voice,
            audio_format=(
                _first_non_empty(env, audio_format_env_keys) or default_audio_format or DEFAULT_QWEN_TTS_FORMAT
            ),
            language_type=(
                _first_non_empty(env, language_type_env_keys) or default_language_type or DEFAULT_QWEN_TTS_LANGUAGE_TYPE
            ),
            timeout_seconds=_parse_float_env(
                env,
                timeout_env_keys,
                default_timeout_seconds,
                field_name=f"{provider_label} timeout",
            ),
            provider_label=provider_label,
        )


@dataclass(frozen=True)
class OpenAICompatibleSpeechResult:
    audio_bytes: bytes
    audio_format: str


class OpenAICompatibleSpeechClient:
    def __init__(self, config: OpenAICompatibleSpeechConfig):
        self.config = config

    def create_speech(self, *, text: str) -> OpenAICompatibleSpeechResult:
        payload = {
            "model": self.config.model,
            "input": {
                "text": text,
                "voice": self.config.voice,
                "language_type": self.config.language_type,
            },
        }
        if self.config.audio_format:
            payload["parameters"] = {"response_format": self.config.audio_format}
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
                raw = response.read()
                response_headers = getattr(response, "headers", None)
                content_type = ""
                if response_headers is not None:
                    if hasattr(response_headers, "get_content_type"):
                        content_type = response_headers.get_content_type()
                    else:
                        content_type = response_headers.get("Content-Type", "")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleSpeechRequestError(
                f"{self.config.provider_label} API HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise OpenAICompatibleSpeechRequestError(
                f"{self.config.provider_label} API connection failed: {exc.reason}"
            ) from exc

        return self._parse_speech_response(raw=raw, content_type=content_type)

    def _parse_speech_response(self, *, raw: bytes, content_type: str | None) -> OpenAICompatibleSpeechResult:
        normalized_content_type = (content_type or "").lower()
        if not (normalized_content_type.startswith("application/json") or raw[:1] in {b"{", b"["}):
            audio_format = _infer_audio_format(normalized_content_type, self.config.audio_format)
            return OpenAICompatibleSpeechResult(audio_bytes=raw, audio_format=audio_format)

        payload_json = json.loads(raw.decode("utf-8"))
        audio_url = self._extract_audio_url(payload_json)
        if audio_url:
            return self._download_audio(audio_url)

        audio_bytes = _maybe_extract_base64_audio(payload_json)
        if audio_bytes:
            return OpenAICompatibleSpeechResult(
                audio_bytes=audio_bytes,
                audio_format=self.config.audio_format,
            )

        error_payload = payload_json.get("error")
        if isinstance(error_payload, dict):
            message = (error_payload.get("message") or "").strip()
            if message:
                raise OpenAICompatibleSpeechRequestError(message)
        raise OpenAICompatibleSpeechRequestError(
            f"{self.config.provider_label} response missing audio bytes."
        )

    def _extract_audio_url(self, payload_json: dict[str, Any]) -> str | None:
        output_payload = payload_json.get("output")
        if not isinstance(output_payload, dict):
            return None
        audio_payload = output_payload.get("audio")
        if not isinstance(audio_payload, dict):
            return None
        audio_url = (audio_payload.get("url") or "").strip()
        return audio_url or None

    def _download_audio(self, audio_url: str) -> OpenAICompatibleSpeechResult:
        try:
            with request.urlopen(audio_url, timeout=self.config.timeout_seconds) as response:
                audio_bytes = response.read()
                response_headers = getattr(response, "headers", None)
                content_type = ""
                if response_headers is not None:
                    if hasattr(response_headers, "get_content_type"):
                        content_type = response_headers.get_content_type()
                    else:
                        content_type = response_headers.get("Content-Type", "")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleSpeechRequestError(
                f"{self.config.provider_label} audio download HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise OpenAICompatibleSpeechRequestError(
                f"{self.config.provider_label} audio download failed: {exc.reason}"
            ) from exc

        audio_format = _infer_audio_format(
            content_type,
            _infer_audio_format_from_url(audio_url, self.config.audio_format),
        )
        return OpenAICompatibleSpeechResult(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
        )


class QwenTtsProvider:
    provider_name = "qwen_tts"
    provider_label = "Qwen TTS"
    api_key_env_keys = ("QWEN_TTS_API_KEY", "DASHSCOPE_TTS_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    model_env_keys = ("QWEN_TTS_MODEL", "DASHSCOPE_TTS_MODEL")
    base_url_env_keys = ("QWEN_TTS_BASE_URL", "DASHSCOPE_TTS_BASE_URL")
    request_url_env_keys = ("QWEN_TTS_REQUEST_URL", "DASHSCOPE_TTS_REQUEST_URL", "DASHSCOPE_SPEECH_REQUEST_URL")
    voice_env_keys = ("QWEN_TTS_VOICE", "DASHSCOPE_TTS_VOICE")
    audio_format_env_keys = ("QWEN_TTS_FORMAT", "DASHSCOPE_TTS_FORMAT")
    language_type_env_keys = ("QWEN_TTS_LANGUAGE_TYPE", "DASHSCOPE_TTS_LANGUAGE_TYPE")
    timeout_env_keys = ("QWEN_TTS_TIMEOUT_SECONDS", "DASHSCOPE_TTS_TIMEOUT_SECONDS")

    def __init__(
        self,
        *,
        root_dir: Path = PHASE5_ROOT_DIR,
        dotenv_file: str = ".env.local",
        model: str | None = None,
        voice: str | None = None,
        audio_format: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        runtime_env = build_runtime_env(root_dir / dotenv_file)
        if model:
            runtime_env["QWEN_TTS_MODEL"] = model
        if voice:
            runtime_env["QWEN_TTS_VOICE"] = voice
        if audio_format:
            runtime_env["QWEN_TTS_FORMAT"] = audio_format
        if request_timeout_seconds is not None:
            runtime_env["QWEN_TTS_TIMEOUT_SECONDS"] = str(request_timeout_seconds)

        try:
            config = OpenAICompatibleSpeechConfig.from_env(
                runtime_env,
                provider_label=self.provider_label,
                api_key_env_keys=self.api_key_env_keys,
                model_env_keys=self.model_env_keys,
                base_url_env_keys=self.base_url_env_keys,
                request_url_env_keys=self.request_url_env_keys,
                voice_env_keys=self.voice_env_keys,
                audio_format_env_keys=self.audio_format_env_keys,
                language_type_env_keys=self.language_type_env_keys,
                timeout_env_keys=self.timeout_env_keys,
                default_base_url=DEFAULT_QWEN_TTS_BASE_URL,
                default_model=DEFAULT_QWEN_TTS_MODEL,
                default_voice=DEFAULT_QWEN_TTS_VOICE,
                default_audio_format=DEFAULT_QWEN_TTS_FORMAT,
                default_language_type=DEFAULT_QWEN_TTS_LANGUAGE_TYPE,
            )
        except Exception as exc:
            raise SpeechSynthesisError(str(exc)) from exc

        timeout_seconds = min(config.timeout_seconds, MAX_TTS_TIMEOUT_SECONDS)
        self.config = replace(config, timeout_seconds=timeout_seconds)

    def synthesize(self, *, text: str, output_path: str | Path | None = None) -> SynthesizedSpeech:
        client = OpenAICompatibleSpeechClient(self.config)

        try:
            result = client.create_speech(text=text)
        except Exception as exc:
            raise SpeechSynthesisError(str(exc)) from exc

        resolved_output_path = _normalize_output_path(
            output_path,
            audio_format=result.audio_format,
            prefix="ai-block-toy-phase7-tts",
        )
        resolved_output_path.write_bytes(result.audio_bytes)

        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name=self.provider_name,
            model_name=self.config.model,
            audio_format=result.audio_format,
            voice=self.config.voice,
        )


class SystemSayTtsProvider:
    provider_name = "macos_say"
    provider_label = "macOS say"

    def __init__(self, *, command: str = "say", voice: str | None = None) -> None:
        self.command = command
        self.voice = voice

    def synthesize(
        self,
        *,
        text: str,
        output_path: str | Path | None = None,
    ) -> SynthesizedSpeech:
        command_path = shutil.which(self.command)
        if command_path is None:
            raise SpeechSynthesisError(f"{self.provider_label} command not found in PATH: {self.command}")

        resolved_output_path = _normalize_output_path(
            output_path,
            audio_format="aiff",
            prefix="ai-block-toy-phase7-tts",
        )
        command = [command_path]
        if self.voice:
            command.extend(["-v", self.voice])
        command.extend(["-o", str(resolved_output_path), text])

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            error_message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise SpeechSynthesisError(f"{self.provider_label} failed: {error_message}") from exc

        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name=self.provider_name,
            model_name="say",
            audio_format="aiff",
            voice=self.voice,
        )


def synthesize_reply_audio(
    *,
    text: str,
    provider_mode: str = "auto",
    output_path: str | Path | None = None,
    qwen_model: str | None = None,
    qwen_voice: str | None = None,
    qwen_audio_format: str | None = None,
    tts_timeout_seconds: float | None = None,
    say_voice: str | None = None,
) -> SynthesizedSpeech | None:
    normalized_provider_mode = provider_mode.strip().lower()
    if normalized_provider_mode == "none":
        return None

    if normalized_provider_mode == "qwen":
        provider = QwenTtsProvider(
            model=qwen_model,
            voice=qwen_voice,
            audio_format=qwen_audio_format,
            request_timeout_seconds=tts_timeout_seconds,
        )
        return provider.synthesize(text=text, output_path=output_path)

    if normalized_provider_mode == "say":
        provider = SystemSayTtsProvider(voice=say_voice)
        return provider.synthesize(text=text, output_path=output_path)

    if normalized_provider_mode != "auto":
        raise ValueError("provider_mode must be one of: auto, qwen, say, none")

    qwen_error: SpeechSynthesisError | None = None
    try:
        provider = QwenTtsProvider(
            model=qwen_model,
            voice=qwen_voice,
            audio_format=qwen_audio_format,
            request_timeout_seconds=tts_timeout_seconds,
        )
        return provider.synthesize(text=text, output_path=output_path)
    except SpeechSynthesisError as exc:
        qwen_error = exc

    fallback_speech = SystemSayTtsProvider(voice=say_voice).synthesize(
        text=text,
        output_path=output_path,
    )
    if qwen_error is not None:
        fallback_speech = replace(fallback_speech, fallback_reason=str(qwen_error))
    return fallback_speech
