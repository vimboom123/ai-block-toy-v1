from __future__ import annotations

import json
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib import parse

import websocket

from voice_realtime.audio import (
    DEFAULT_ASR_CHUNK_BYTES,
    DEFAULT_ASR_SAMPLE_RATE,
    iter_pcm16_base64_chunks,
    load_audio_pcm16_mono,
)

from .models import AudioTranscription

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]

DEFAULT_QWEN_REALTIME_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_QWEN_REALTIME_MODEL = "qwen3-asr-flash-realtime"
DEFAULT_QWEN_REALTIME_LANGUAGE = "zh"
DEFAULT_QWEN_REALTIME_TIMEOUT_SECONDS = 30.0


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
        raise QwenRealtimeAsrError(f"Invalid {field_name}: {raw_value}") from exc


def _normalize_ws_url(base_url: str, model: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url:
        return normalized_base_url
    return f"{normalized_base_url}?{parse.urlencode({'model': model})}"


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _new_event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class QwenRealtimeAsrError(RuntimeError):
    """Raised when the realtime ASR connection or transcription fails."""


class NoSpeechDetectedError(QwenRealtimeAsrError):
    """Raised when ASR returns an empty transcript — no audible speech was captured."""


class _ConnectionPrewarmer:
    """Establishes a WebSocket connection in the background so the next call avoids TLS/upgrade latency."""

    MAX_IDLE_SECONDS = 15.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ws: Any | None = None
        self._ws_time: float = 0.0

    def prewarm(self, ws_url: str, headers: list[str], timeout: float) -> None:
        threading.Thread(target=self._connect_worker, args=(ws_url, headers, timeout), daemon=True).start()

    def _connect_worker(self, ws_url: str, headers: list[str], timeout: float) -> None:
        try:
            ws = websocket.create_connection(ws_url, header=headers, timeout=timeout)
            with self._lock:
                if self._ws is not None:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                self._ws = ws
                self._ws_time = time.monotonic()
        except Exception:
            pass

    def take(self) -> Any | None:
        """Return a fresh pre-warmed connection, or None if none is available / too old."""
        with self._lock:
            ws = self._ws
            self._ws = None
        if ws is None:
            return None
        if time.monotonic() - self._ws_time > self.MAX_IDLE_SECONDS:
            try:
                ws.close()
            except Exception:
                pass
            return None
        return ws


@dataclass(frozen=True)
class QwenRealtimeAsrConfig:
    api_key: str
    model: str
    ws_url: str
    language: str | None
    sample_rate: int = DEFAULT_ASR_SAMPLE_RATE
    chunk_bytes: int = DEFAULT_ASR_CHUNK_BYTES
    timeout_seconds: float = DEFAULT_QWEN_REALTIME_TIMEOUT_SECONDS
    provider_label: str = "Qwen realtime ASR"

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
        language_env_keys: tuple[str, ...],
        timeout_env_keys: tuple[str, ...],
        default_base_url: str,
        default_model: str,
        default_language: str | None,
        default_timeout_seconds: float = DEFAULT_QWEN_REALTIME_TIMEOUT_SECONDS,
    ) -> "QwenRealtimeAsrConfig":
        api_key = _first_non_empty(env, api_key_env_keys)
        if not api_key:
            env_keys = _format_env_keys(api_key_env_keys)
            raise QwenRealtimeAsrError(f"Missing {provider_label} API key. Set one of: {env_keys}.")

        model = _first_non_empty(env, model_env_keys) or default_model
        if not model:
            env_keys = _format_env_keys(model_env_keys)
            raise QwenRealtimeAsrError(f"Missing {provider_label} model. Set one of: {env_keys}.")

        request_url = _first_non_empty(env, request_url_env_keys)
        if not request_url:
            base_url = _first_non_empty(env, base_url_env_keys) or default_base_url
            if not base_url:
                env_keys = _format_env_keys(base_url_env_keys)
                raise QwenRealtimeAsrError(
                    f"Missing {provider_label} base URL. Set one of: {env_keys}."
                )
            request_url = _normalize_ws_url(base_url, model)

        language = _first_non_empty(env, language_env_keys) or default_language

        return cls(
            api_key=api_key,
            model=model,
            ws_url=request_url,
            language=language,
            timeout_seconds=_parse_float_env(
                env,
                timeout_env_keys,
                default_timeout_seconds,
                field_name=f"{provider_label} timeout",
            ),
            provider_label=provider_label,
        )


class QwenRealtimeAsrTranscriber:
    provider_name = "qwen_asr_realtime"
    provider_label = "Qwen realtime ASR"
    api_key_env_keys = ("QWEN_RT_API_KEY", "DASHSCOPE_RT_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    model_env_keys = ("QWEN_RT_ASR_MODEL", "DASHSCOPE_RT_ASR_MODEL", "QWEN_ASR_REALTIME_MODEL")
    base_url_env_keys = (
        "QWEN_RT_BASE_URL",
        "DASHSCOPE_RT_BASE_URL",
        "QWEN_REALTIME_BASE_URL",
        "DASHSCOPE_REALTIME_BASE_URL",
    )
    request_url_env_keys = ("QWEN_RT_ASR_REQUEST_URL", "DASHSCOPE_RT_ASR_REQUEST_URL")
    language_env_keys = ("QWEN_RT_ASR_LANGUAGE", "DASHSCOPE_RT_ASR_LANGUAGE")
    timeout_env_keys = ("QWEN_RT_ASR_TIMEOUT_SECONDS", "DASHSCOPE_RT_ASR_TIMEOUT_SECONDS")

    def __init__(
        self,
        *,
        root_dir: Path = PHASE5_ROOT_DIR,
        dotenv_file: str = ".env.local",
        model: str | None = None,
        language: str | None = DEFAULT_QWEN_REALTIME_LANGUAGE,
        base_url: str | None = None,
        request_url: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        runtime_env = build_runtime_env(root_dir / dotenv_file)
        if model:
            runtime_env["QWEN_RT_ASR_MODEL"] = model
        if language is not None:
            runtime_env["QWEN_RT_ASR_LANGUAGE"] = language
        if base_url:
            runtime_env["QWEN_RT_BASE_URL"] = base_url
        if request_url:
            runtime_env["QWEN_RT_ASR_REQUEST_URL"] = request_url
        if request_timeout_seconds is not None:
            runtime_env["QWEN_RT_ASR_TIMEOUT_SECONDS"] = str(request_timeout_seconds)

        try:
            self.config = QwenRealtimeAsrConfig.from_env(
                runtime_env,
                provider_label=self.provider_label,
                api_key_env_keys=self.api_key_env_keys,
                model_env_keys=self.model_env_keys,
                base_url_env_keys=self.base_url_env_keys,
                request_url_env_keys=self.request_url_env_keys,
                language_env_keys=self.language_env_keys,
                timeout_env_keys=self.timeout_env_keys,
                default_base_url=DEFAULT_QWEN_REALTIME_BASE_URL,
                default_model=DEFAULT_QWEN_REALTIME_MODEL,
                default_language=DEFAULT_QWEN_REALTIME_LANGUAGE,
            )
        except Exception as exc:
            raise QwenRealtimeAsrError(str(exc)) from exc
        self._prewarmer = _ConnectionPrewarmer()

    def transcribe(self, audio_path: str | Path) -> AudioTranscription:
        resolved_audio_path = Path(audio_path).expanduser().resolve()
        if not resolved_audio_path.is_file():
            raise QwenRealtimeAsrError(f"Audio file not found: {resolved_audio_path}")

        pcm16_audio, sample_rate, duration_seconds = load_audio_pcm16_mono(
            resolved_audio_path,
            target_sample_rate=self.config.sample_rate,
        )
        if sample_rate != self.config.sample_rate:
            raise QwenRealtimeAsrError(
                f"Unexpected ASR sample rate conversion result: {sample_rate} != {self.config.sample_rate}"
            )

        headers = [
            f"Authorization: Bearer {self.config.api_key}",
            "OpenAI-Beta: realtime=v1",
        ]

        ws = None
        transcript: str | None = None
        interim_transcript: str | None = None
        session_finished = False
        try:
            # P1: use pre-warmed connection to avoid TLS/upgrade latency on every turn
            ws = self._prewarmer.take()
            if ws is None:
                ws = websocket.create_connection(
                    self.config.ws_url,
                    header=headers,
                    timeout=self.config.timeout_seconds,
                )
            ws.settimeout(self.config.timeout_seconds)
            self._send_json(
                ws,
                {
                    "type": "session.update",
                    "event_id": _new_event_id("session"),
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": "pcm",
                        "sample_rate": self.config.sample_rate,
                        "input_audio_transcription": {
                            "language": self.config.language or DEFAULT_QWEN_REALTIME_LANGUAGE,
                        },
                        "turn_detection": None,
                    },
                },
            )
            for audio_chunk in iter_pcm16_base64_chunks(
                pcm16_audio,
                chunk_bytes=self.config.chunk_bytes,
            ):
                self._send_json(
                    ws,
                    {
                        "type": "input_audio_buffer.append",
                        "event_id": _new_event_id("audio"),
                        "audio": audio_chunk,
                    },
                )
            self._send_json(
                ws,
                {
                    "type": "input_audio_buffer.commit",
                    "event_id": _new_event_id("audio"),
                },
            )
            self._send_json(
                ws,
                {
                    "type": "session.finish",
                    "event_id": _new_event_id("session"),
                },
            )

            # D2: bound the total receive loop so a stalled session.finished never hangs forever
            deadline = time.monotonic() + self.config.timeout_seconds
            while True:
                remaining = max(0.5, deadline - time.monotonic())
                if remaining <= 0:
                    raise QwenRealtimeAsrError("ASR session timed out waiting for completion")
                ws.settimeout(remaining)
                raw_message = ws.recv()
                payload = self._parse_message(raw_message)
                event_type = (payload.get("type") or "").strip()

                if event_type == "error":
                    raise QwenRealtimeAsrError(self._format_error(payload))
                if event_type == "conversation.item.input_audio_transcription.text":
                    interim_transcript = self._extract_transcript(payload) or interim_transcript
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = self._extract_transcript(payload) or transcript
                elif event_type == "session.finished":
                    session_finished = True
                if session_finished:
                    break
        except Exception as exc:
            if isinstance(exc, QwenRealtimeAsrError):
                raise
            raise QwenRealtimeAsrError(f"Qwen realtime ASR failed: {exc}") from exc
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        final_transcript = transcript or interim_transcript
        if not final_transcript:
            # B2: raise a specific subclass so callers can distinguish "no speech" from API errors
            raise NoSpeechDetectedError("Qwen realtime ASR returned an empty transcript — no speech detected")

        # P1: pre-warm the next connection while the caller processes the result
        self._prewarmer.prewarm(self.config.ws_url, headers, self.config.timeout_seconds)

        return AudioTranscription(
            audio_path=str(resolved_audio_path),
            transcript=final_transcript,
            input_mode="audio_file",
            asr_source=self.provider_name,
            model_name=self.config.model,
            language=self.config.language,
            duration_seconds=duration_seconds,
        )

    @staticmethod
    def _send_json(ws: Any, payload: dict[str, Any]) -> None:
        ws.send(_json_dumps(payload))

    @staticmethod
    def _parse_message(raw_message: Any) -> dict[str, Any]:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="replace")
        if not isinstance(raw_message, str):
            raise QwenRealtimeAsrError(f"Unexpected websocket payload type: {type(raw_message).__name__}")
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise QwenRealtimeAsrError(f"Realtime ASR response is not valid JSON: {raw_message}") from exc
        if not isinstance(payload, dict):
            raise QwenRealtimeAsrError("Realtime ASR response JSON must decode to an object")
        return payload

    @staticmethod
    def _extract_transcript(payload: dict[str, Any]) -> str | None:
        for key in ("transcript", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        item = payload.get("item")
        if isinstance(item, dict):
            for key in ("transcript", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            content = item.get("content")
            if isinstance(content, list):
                for entry in content:
                    if not isinstance(entry, dict):
                        continue
                    for key in ("transcript", "text", "value"):
                        value = entry.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
        return None

    @staticmethod
    def _format_error(payload: dict[str, Any]) -> str:
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            code = error_payload.get("code")
            message = error_payload.get("message") or error_payload.get("detail") or str(error_payload)
            if code:
                return f"Realtime ASR error {code}: {message}"
            return f"Realtime ASR error: {message}"
        return f"Realtime ASR error: {payload}"
