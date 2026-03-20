from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _ensure_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio_path: str
    text: str
    input_mode: str
    provider_name: str
    model_name: str
    audio_format: str
    voice: str | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "audio_path", _ensure_non_empty(self.audio_path, "audio_path"))
        object.__setattr__(self, "text", _ensure_non_empty(self.text, "text"))
        object.__setattr__(self, "input_mode", _ensure_non_empty(self.input_mode, "input_mode"))
        object.__setattr__(self, "provider_name", _ensure_non_empty(self.provider_name, "provider_name"))
        object.__setattr__(self, "model_name", _ensure_non_empty(self.model_name, "model_name"))
        object.__setattr__(self, "audio_format", _ensure_non_empty(self.audio_format, "audio_format"))
        if self.voice is not None:
            object.__setattr__(self, "voice", _ensure_non_empty(self.voice, "voice"))
        if self.fallback_reason is not None:
            object.__setattr__(
                self,
                "fallback_reason",
                _ensure_non_empty(self.fallback_reason, "fallback_reason"),
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "audio_path": self.audio_path,
            "text": self.text,
            "input_mode": self.input_mode,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "audio_format": self.audio_format,
        }
        if self.voice is not None:
            payload["voice"] = self.voice
        if self.fallback_reason is not None:
            payload["fallback_reason"] = self.fallback_reason
        return payload
