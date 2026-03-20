from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _ensure_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True)
class AudioTranscription:
    audio_path: str
    transcript: str
    input_mode: str
    asr_source: str
    model_name: str
    language: str | None = None
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "audio_path", _ensure_non_empty(self.audio_path, "audio_path"))
        object.__setattr__(self, "transcript", _ensure_non_empty(self.transcript, "transcript"))
        object.__setattr__(self, "input_mode", _ensure_non_empty(self.input_mode, "input_mode"))
        object.__setattr__(self, "asr_source", _ensure_non_empty(self.asr_source, "asr_source"))
        object.__setattr__(self, "model_name", _ensure_non_empty(self.model_name, "model_name"))
        if self.language is not None:
            object.__setattr__(self, "language", _ensure_non_empty(self.language, "language"))
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must be greater than or equal to 0")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "audio_path": self.audio_path,
            "transcript": self.transcript,
            "input_mode": self.input_mode,
            "asr_source": self.asr_source,
            "model_name": self.model_name,
        }
        if self.language is not None:
            payload["language"] = self.language
        if self.duration_seconds is not None:
            payload["duration_seconds"] = round(self.duration_seconds, 3)
        return payload


@dataclass(frozen=True)
class RecordedAudioClip:
    audio_path: str
    input_mode: str
    sample_rate: int
    channels: int
    duration_seconds: float
    device: str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "audio_path", _ensure_non_empty(self.audio_path, "audio_path"))
        object.__setattr__(self, "input_mode", _ensure_non_empty(self.input_mode, "input_mode"))
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be greater than 0")
        if self.channels <= 0:
            raise ValueError("channels must be greater than 0")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be greater than 0")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "audio_path": self.audio_path,
            "input_mode": self.input_mode,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_seconds": round(self.duration_seconds, 3),
        }
        if self.device is not None:
            payload["device"] = self.device
        return payload
