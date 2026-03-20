from __future__ import annotations

import base64
import json
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from urllib import parse

import websocket

from voice_realtime.audio import DEFAULT_TTS_SAMPLE_RATE, write_pcm16_wav
from voice_realtime.playback import Pcm16OutputStream, play_audio_file

from .models import SynthesizedSpeech
from .synthesizer import SpeechSynthesisError, SystemSayTtsProvider

PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]

DEFAULT_QWEN_REALTIME_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_QWEN_REALTIME_MODEL = "qwen3-tts-flash-realtime"
DEFAULT_QWEN_REALTIME_VOICE = "Cherry"
DEFAULT_QWEN_REALTIME_LANGUAGE_TYPE = "Chinese"
DEFAULT_QWEN_REALTIME_RESPONSE_FORMAT = "pcm"
DEFAULT_QWEN_REALTIME_MODE = "commit"
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
        raise QwenRealtimeTtsError(f"Invalid {field_name}: {raw_value}") from exc


def _normalize_ws_url(base_url: str, model: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/")
    if not normalized_base_url:
        return normalized_base_url
    return f"{normalized_base_url}?{parse.urlencode({'model': model})}"


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _new_event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class QwenRealtimeTtsError(RuntimeError):
    """Raised when the realtime TTS connection or synthesis fails."""


class _ConnectionPrewarmer:
    """Establishes a WebSocket connection in the background to avoid TLS/upgrade latency on the next call."""

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
class QwenRealtimeTtsConfig:
    api_key: str
    model: str
    ws_url: str
    voice: str
    language_type: str
    response_format: str
    mode: str
    sample_rate: int = DEFAULT_TTS_SAMPLE_RATE
    timeout_seconds: float = DEFAULT_QWEN_REALTIME_TIMEOUT_SECONDS
    provider_label: str = "Qwen realtime TTS"

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
        language_type_env_keys: tuple[str, ...],
        response_format_env_keys: tuple[str, ...],
        mode_env_keys: tuple[str, ...],
        timeout_env_keys: tuple[str, ...],
        default_base_url: str,
        default_model: str,
        default_voice: str,
        default_language_type: str,
        default_response_format: str,
        default_mode: str,
        default_timeout_seconds: float = DEFAULT_QWEN_REALTIME_TIMEOUT_SECONDS,
    ) -> "QwenRealtimeTtsConfig":
        api_key = _first_non_empty(env, api_key_env_keys)
        if not api_key:
            env_keys = _format_env_keys(api_key_env_keys)
            raise QwenRealtimeTtsError(f"Missing {provider_label} API key. Set one of: {env_keys}.")

        model = _first_non_empty(env, model_env_keys) or default_model
        if not model:
            env_keys = _format_env_keys(model_env_keys)
            raise QwenRealtimeTtsError(f"Missing {provider_label} model. Set one of: {env_keys}.")

        request_url = _first_non_empty(env, request_url_env_keys)
        if not request_url:
            base_url = _first_non_empty(env, base_url_env_keys) or default_base_url
            if not base_url:
                env_keys = _format_env_keys(base_url_env_keys)
                raise QwenRealtimeTtsError(
                    f"Missing {provider_label} base URL. Set one of: {env_keys}."
                )
            request_url = _normalize_ws_url(base_url, model)

        voice = _first_non_empty(env, voice_env_keys) or default_voice
        if not voice:
            env_keys = _format_env_keys(voice_env_keys)
            raise QwenRealtimeTtsError(f"Missing {provider_label} voice. Set one of: {env_keys}.")

        language_type = _first_non_empty(env, language_type_env_keys) or default_language_type
        response_format = _first_non_empty(env, response_format_env_keys) or default_response_format
        mode = _first_non_empty(env, mode_env_keys) or default_mode

        return cls(
            api_key=api_key,
            model=model,
            ws_url=request_url,
            voice=voice,
            language_type=language_type,
            response_format=response_format,
            mode=mode,
            timeout_seconds=_parse_float_env(
                env,
                timeout_env_keys,
                default_timeout_seconds,
                field_name=f"{provider_label} timeout",
            ),
            provider_label=provider_label,
        )


class QwenRealtimeTtsProvider:
    provider_name = "qwen_tts_realtime"
    provider_label = "Qwen realtime TTS"
    api_key_env_keys = ("QWEN_RT_API_KEY", "DASHSCOPE_RT_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    model_env_keys = ("QWEN_RT_TTS_MODEL", "DASHSCOPE_RT_TTS_MODEL", "QWEN_TTS_REALTIME_MODEL")
    base_url_env_keys = (
        "QWEN_RT_BASE_URL",
        "DASHSCOPE_RT_BASE_URL",
        "QWEN_REALTIME_BASE_URL",
        "DASHSCOPE_REALTIME_BASE_URL",
    )
    request_url_env_keys = ("QWEN_RT_TTS_REQUEST_URL", "DASHSCOPE_RT_TTS_REQUEST_URL")
    voice_env_keys = ("QWEN_RT_TTS_VOICE", "DASHSCOPE_RT_TTS_VOICE")
    language_type_env_keys = ("QWEN_RT_TTS_LANGUAGE_TYPE", "DASHSCOPE_RT_TTS_LANGUAGE_TYPE")
    response_format_env_keys = ("QWEN_RT_TTS_RESPONSE_FORMAT", "DASHSCOPE_RT_TTS_RESPONSE_FORMAT")
    mode_env_keys = ("QWEN_RT_TTS_MODE", "DASHSCOPE_RT_TTS_MODE")
    timeout_env_keys = ("QWEN_RT_TTS_TIMEOUT_SECONDS", "DASHSCOPE_RT_TTS_TIMEOUT_SECONDS")

    def __init__(
        self,
        *,
        root_dir: Path = PHASE5_ROOT_DIR,
        dotenv_file: str = ".env.local",
        model: str | None = None,
        voice: str | None = DEFAULT_QWEN_REALTIME_VOICE,
        language_type: str | None = DEFAULT_QWEN_REALTIME_LANGUAGE_TYPE,
        response_format: str | None = DEFAULT_QWEN_REALTIME_RESPONSE_FORMAT,
        mode: str | None = DEFAULT_QWEN_REALTIME_MODE,
        base_url: str | None = None,
        request_url: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        runtime_env = build_runtime_env(root_dir / dotenv_file)
        if model:
            runtime_env["QWEN_RT_TTS_MODEL"] = model
        if voice is not None:
            runtime_env["QWEN_RT_TTS_VOICE"] = voice
        if language_type is not None:
            runtime_env["QWEN_RT_TTS_LANGUAGE_TYPE"] = language_type
        if response_format is not None:
            runtime_env["QWEN_RT_TTS_RESPONSE_FORMAT"] = response_format
        if mode is not None:
            runtime_env["QWEN_RT_TTS_MODE"] = mode
        if base_url:
            runtime_env["QWEN_RT_BASE_URL"] = base_url
        if request_url:
            runtime_env["QWEN_RT_TTS_REQUEST_URL"] = request_url
        if request_timeout_seconds is not None:
            runtime_env["QWEN_RT_TTS_TIMEOUT_SECONDS"] = str(request_timeout_seconds)

        try:
            self.config = QwenRealtimeTtsConfig.from_env(
                runtime_env,
                provider_label=self.provider_label,
                api_key_env_keys=self.api_key_env_keys,
                model_env_keys=self.model_env_keys,
                base_url_env_keys=self.base_url_env_keys,
                request_url_env_keys=self.request_url_env_keys,
                voice_env_keys=self.voice_env_keys,
                language_type_env_keys=self.language_type_env_keys,
                response_format_env_keys=self.response_format_env_keys,
                mode_env_keys=self.mode_env_keys,
                timeout_env_keys=self.timeout_env_keys,
                default_base_url=DEFAULT_QWEN_REALTIME_BASE_URL,
                default_model=DEFAULT_QWEN_REALTIME_MODEL,
                default_voice=DEFAULT_QWEN_REALTIME_VOICE,
                default_language_type=DEFAULT_QWEN_REALTIME_LANGUAGE_TYPE,
                default_response_format=DEFAULT_QWEN_REALTIME_RESPONSE_FORMAT,
                default_mode=DEFAULT_QWEN_REALTIME_MODE,
            )
        except Exception as exc:
            raise QwenRealtimeTtsError(str(exc)) from exc
        self._prewarmer = _ConnectionPrewarmer()
        self._prewarm_next_connection()

    def synthesize(
        self,
        *,
        text: str,
        output_path: str | Path | None = None,
        playback_device: str | int | None = None,
        playback_gain: float = 1.0,
        stream_playback: bool = False,
        reply_ready_at: float | None = None,
    ) -> SynthesizedSpeech:
        normalized_text = text.strip()
        if not normalized_text:
            raise QwenRealtimeTtsError("text must not be empty")

        headers = self._build_headers()

        ws = None
        audio_bytes = bytearray()
        session_finished = False
        first_audio_logged = False
        started_at = time.monotonic()
        try:
            # P2: use pre-warmed connection to avoid TLS/upgrade latency on every turn
            ws = self._prewarmer.take()
            if ws is None:
                ws = websocket.create_connection(
                    self.config.ws_url,
                    header=headers,
                    timeout=self.config.timeout_seconds,
                )
            ws.settimeout(self.config.timeout_seconds)
            playback_context = (
                Pcm16OutputStream(
                    sample_rate=DEFAULT_TTS_SAMPLE_RATE,
                    device=playback_device,
                    gain=playback_gain,
                )
                if stream_playback
                else nullcontext(None)
            )
            with playback_context as playback_stream:
                self._send_json(
                    ws,
                    {
                        "type": "session.update",
                        "event_id": _new_event_id("session"),
                        "session": {
                            "mode": self.config.mode,
                            "voice": self.config.voice,
                            "language_type": self.config.language_type,
                            "response_format": self.config.response_format,
                            "sample_rate": DEFAULT_TTS_SAMPLE_RATE,
                            "instructions": "",
                            "optimize_instructions": False,
                        },
                    },
                )
                self._send_json(
                    ws,
                    {
                        "type": "input_text_buffer.append",
                        "event_id": _new_event_id("text"),
                        "text": normalized_text,
                    },
                )
                self._send_json(
                    ws,
                    {
                        "type": "input_text_buffer.commit",
                        "event_id": _new_event_id("text"),
                    },
                )
                self._send_json(
                    ws,
                    {
                        "type": "session.finish",
                        "event_id": _new_event_id("session"),
                    },
                )

                while True:
                    raw_message = ws.recv()
                    payload = self._parse_message(raw_message)
                    event_type = (payload.get("type") or "").strip()
                    if event_type == "error":
                        raise QwenRealtimeTtsError(self._format_error(payload))
                    if event_type == "response.audio.delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta.strip():
                            audio_chunk = base64.b64decode(delta)
                            audio_bytes.extend(audio_chunk)
                            if playback_stream is not None:
                                if not first_audio_logged:
                                    if reply_ready_at is not None:
                                        print(
                                            f"  [播报间隔] {(time.monotonic() - reply_ready_at) * 1000:.0f}ms -> 开始播放",
                                            file=sys.stderr,
                                        )
                                    print(
                                        f"  [TTS首包] {(time.monotonic() - started_at) * 1000:.0f}ms -> 边播边出",
                                        file=sys.stderr,
                                    )
                                    first_audio_logged = True
                                playback_stream.write(audio_chunk)
                    elif event_type == "session.finished":
                        session_finished = True
                    if session_finished:
                        break
        except Exception as exc:
            if isinstance(exc, QwenRealtimeTtsError):
                raise
            raise QwenRealtimeTtsError(f"Qwen realtime TTS failed: {exc}") from exc
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        if not audio_bytes:
            raise QwenRealtimeTtsError("Qwen realtime TTS returned no audio")

        # P2: pre-warm next connection while caller processes/plays the audio
        self._prewarm_next_connection()

        resolved_output_path = write_pcm16_wav(
            output_path,
            bytes(audio_bytes),
            sample_rate=DEFAULT_TTS_SAMPLE_RATE,
        )
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=normalized_text,
            input_mode="reply_text",
            provider_name=self.provider_name,
            model_name=self.config.model,
            audio_format="wav",
            voice=self.config.voice,
        )

    @staticmethod
    def _send_json(ws: Any, payload: dict[str, Any]) -> None:
        ws.send(_json_dumps(payload))

    @staticmethod
    def _parse_message(raw_message: Any) -> dict[str, Any]:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="replace")
        if not isinstance(raw_message, str):
            raise QwenRealtimeTtsError(f"Unexpected websocket payload type: {type(raw_message).__name__}")
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise QwenRealtimeTtsError(f"Realtime TTS response is not valid JSON: {raw_message}") from exc
        if not isinstance(payload, dict):
            raise QwenRealtimeTtsError("Realtime TTS response JSON must decode to an object")
        return payload

    @staticmethod
    def _format_error(payload: dict[str, Any]) -> str:
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            code = error_payload.get("code")
            message = error_payload.get("message") or error_payload.get("detail") or str(error_payload)
            if code:
                return f"Realtime TTS error {code}: {message}"
            return f"Realtime TTS error: {message}"
        return f"Realtime TTS error: {payload}"

    def _build_headers(self) -> list[str]:
        return [f"Authorization: Bearer {self.config.api_key}"]

    def _prewarm_next_connection(self) -> None:
        self._prewarmer.prewarm(
            self.config.ws_url,
            self._build_headers(),
            self.config.timeout_seconds,
        )


def synthesize_and_play_realtime_reply_audio(
    *,
    text: str,
    provider_mode: str = "auto",
    output_path: str | Path | None = None,
    qwen_model: str | None = None,
    qwen_voice: str | None = None,
    qwen_audio_format: str | None = None,
    tts_timeout_seconds: float | None = None,
    say_voice: str | None = None,
    playback_device: str | int | None = None,
    playback_gain: float = 1.0,
    reply_ready_at: float | None = None,
) -> SynthesizedSpeech | None:
    normalized_provider_mode = provider_mode.strip().lower()
    if normalized_provider_mode == "none":
        return None

    if normalized_provider_mode == "qwen":
        try:
            provider = QwenRealtimeTtsProvider(
                model=qwen_model,
                voice=qwen_voice,
                response_format=qwen_audio_format,
                request_timeout_seconds=tts_timeout_seconds,
            )
            return provider.synthesize(
                text=text,
                output_path=output_path,
                playback_device=playback_device,
                playback_gain=playback_gain,
                stream_playback=True,
                reply_ready_at=reply_ready_at,
            )
        except Exception as exc:
            raise SpeechSynthesisError(str(exc)) from exc

    if normalized_provider_mode == "say":
        speech = SystemSayTtsProvider(voice=say_voice).synthesize(text=text, output_path=output_path)
        play_audio_file(speech.audio_path, device=playback_device, gain=playback_gain)
        return speech

    if normalized_provider_mode != "auto":
        raise ValueError("provider_mode must be one of: auto, qwen, say, none")

    qwen_error: SpeechSynthesisError | None = None
    try:
        provider = QwenRealtimeTtsProvider(
            model=qwen_model,
            voice=qwen_voice,
            response_format=qwen_audio_format,
            request_timeout_seconds=tts_timeout_seconds,
        )
        return provider.synthesize(
            text=text,
            output_path=output_path,
            playback_device=playback_device,
            playback_gain=playback_gain,
            stream_playback=True,
        )
    except SpeechSynthesisError as exc:
        qwen_error = exc
    except Exception as exc:
        qwen_error = SpeechSynthesisError(str(exc))

    fallback_speech = SystemSayTtsProvider(voice=say_voice).synthesize(
        text=text,
        output_path=output_path,
    )
    if reply_ready_at is not None:
        print(
            f"  [播报间隔] {(time.monotonic() - reply_ready_at) * 1000:.0f}ms -> 开始播放",
            file=sys.stderr,
        )
    play_audio_file(fallback_speech.audio_path, device=playback_device, gain=playback_gain)
    if qwen_error is not None:
        fallback_speech = replace(fallback_speech, fallback_reason=str(qwen_error))
    return fallback_speech


def synthesize_realtime_reply_audio(
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
        try:
            provider = QwenRealtimeTtsProvider(
                model=qwen_model,
                voice=qwen_voice,
                response_format=qwen_audio_format,
                request_timeout_seconds=tts_timeout_seconds,
            )
            return provider.synthesize(text=text, output_path=output_path)
        except Exception as exc:
            raise SpeechSynthesisError(str(exc)) from exc

    if normalized_provider_mode == "say":
        provider = SystemSayTtsProvider(voice=say_voice)
        return provider.synthesize(text=text, output_path=output_path)

    if normalized_provider_mode != "auto":
        raise ValueError("provider_mode must be one of: auto, qwen, say, none")

    qwen_error: SpeechSynthesisError | None = None
    try:
        provider = QwenRealtimeTtsProvider(
            model=qwen_model,
            voice=qwen_voice,
            response_format=qwen_audio_format,
            request_timeout_seconds=tts_timeout_seconds,
        )
        return provider.synthesize(text=text, output_path=output_path)
    except SpeechSynthesisError as exc:
        qwen_error = exc
    except Exception as exc:
        qwen_error = SpeechSynthesisError(str(exc))

    fallback_speech = SystemSayTtsProvider(voice=say_voice).synthesize(
        text=text,
        output_path=output_path,
    )
    if qwen_error is not None:
        fallback_speech = replace(fallback_speech, fallback_reason=str(qwen_error))
    return fallback_speech
