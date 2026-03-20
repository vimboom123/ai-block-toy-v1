from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile

DEFAULT_ASR_SAMPLE_RATE = 16000
DEFAULT_ASR_CHUNK_DURATION_SECONDS = 0.1
DEFAULT_ASR_CHUNK_BYTES = int(DEFAULT_ASR_SAMPLE_RATE * 2 * DEFAULT_ASR_CHUNK_DURATION_SECONDS)
DEFAULT_TTS_SAMPLE_RATE = 24000


def normalize_output_path(
    output_path: str | Path | None,
    *,
    audio_format: str,
    prefix: str,
) -> Path:
    if output_path is None:
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        resolved_path = Path("/tmp") / f"{prefix}-{timestamp}.{audio_format}"
    else:
        resolved_path = Path(output_path).expanduser()
        expected_suffix = f".{audio_format.lower()}"
        if resolved_path.suffix.lower() != expected_suffix:
            resolved_path = resolved_path.with_suffix(expected_suffix)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return resolved_path.resolve()


def _resample_mono_audio(
    mono_audio: np.ndarray,
    *,
    source_sample_rate: int,
    target_sample_rate: int,
) -> np.ndarray:
    if mono_audio.size == 0:
        return mono_audio.astype(np.float32, copy=False)
    if source_sample_rate <= 0 or target_sample_rate <= 0:
        raise ValueError("sample rates must be greater than 0")
    if source_sample_rate == target_sample_rate:
        return mono_audio.astype(np.float32, copy=False)

    source_positions = np.arange(mono_audio.shape[0], dtype=np.float64)
    target_length = max(1, int(round(mono_audio.shape[0] * target_sample_rate / source_sample_rate)))
    target_positions = np.linspace(0.0, mono_audio.shape[0] - 1, num=target_length, dtype=np.float64)
    return np.interp(target_positions, source_positions, mono_audio).astype(np.float32)


def load_audio_pcm16_mono(
    audio_path: str | Path,
    *,
    target_sample_rate: int = DEFAULT_ASR_SAMPLE_RATE,
) -> tuple[bytes, int, float]:
    resolved_audio_path = Path(audio_path).expanduser().resolve()
    if not resolved_audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {resolved_audio_path}")

    audio_buffer, sample_rate = soundfile.read(
        str(resolved_audio_path),
        dtype="float32",
        always_2d=True,
    )
    if audio_buffer.size == 0:
        raise ValueError(f"Audio file is empty: {resolved_audio_path}")

    mono_audio = audio_buffer.mean(axis=1).astype(np.float32, copy=False)
    if sample_rate != target_sample_rate:
        mono_audio = _resample_mono_audio(
            mono_audio,
            source_sample_rate=sample_rate,
            target_sample_rate=target_sample_rate,
        )
        sample_rate = target_sample_rate

    clipped_audio = np.clip(mono_audio, -1.0, 1.0)
    pcm16_audio = np.round(clipped_audio * 32767.0).astype(np.int16).astype("<i2", copy=False)
    duration_seconds = pcm16_audio.shape[0] / float(sample_rate)
    return pcm16_audio.tobytes(), int(sample_rate), duration_seconds


def iter_pcm16_base64_chunks(
    pcm16_audio: bytes,
    *,
    chunk_bytes: int = DEFAULT_ASR_CHUNK_BYTES,
) -> Iterator[str]:
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be greater than 0")
    normalized_chunk_bytes = max(2, chunk_bytes - (chunk_bytes % 2))
    for offset in range(0, len(pcm16_audio), normalized_chunk_bytes):
        chunk = pcm16_audio[offset : offset + normalized_chunk_bytes]
        if chunk:
            yield base64.b64encode(chunk).decode("ascii")


def write_pcm16_wav(
    output_path: str | Path | None,
    pcm16_audio: bytes,
    *,
    sample_rate: int = DEFAULT_TTS_SAMPLE_RATE,
    prefix: str = "ai-block-toy-phase7-tts",
) -> Path:
    resolved_output_path = normalize_output_path(output_path, audio_format="wav", prefix=prefix)
    audio_array = np.frombuffer(pcm16_audio, dtype="<i2")
    if audio_array.size == 0:
        raise ValueError("pcm16_audio must not be empty")
    soundfile.write(
        str(resolved_output_path),
        audio_array,
        sample_rate,
        subtype="PCM_16",
        format="WAV",
    )
    return resolved_output_path
