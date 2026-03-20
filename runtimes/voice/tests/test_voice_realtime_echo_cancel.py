from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from voice_realtime.echo_cancel import cancel_playback_echo  # noqa: E402


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(
        str(path),
        audio.astype(np.float32, copy=False),
        sample_rate,
        subtype="PCM_16",
        format="WAV",
    )


def test_cancel_playback_echo_reduces_reference_contamination(tmp_path: Path) -> None:
    sample_rate = 16000
    rng = np.random.default_rng(7)

    reference_duration_seconds = 1.4
    reference_frames = int(round(reference_duration_seconds * sample_rate))
    reference_time = np.arange(reference_frames, dtype=np.float32) / float(sample_rate)
    reference_audio = (
        0.50 * np.sin(2.0 * np.pi * 180.0 * reference_time)
        + 0.35 * np.sin(2.0 * np.pi * 310.0 * reference_time + 0.2)
        + 0.18 * np.sin(2.0 * np.pi * 520.0 * reference_time + 0.5)
    )
    reference_audio *= np.hanning(reference_frames).astype(np.float32, copy=False)

    raw_duration_seconds = 2.0
    raw_frames = int(round(raw_duration_seconds * sample_rate))
    raw_time = np.arange(raw_frames, dtype=np.float32) / float(sample_rate)
    user_audio = (
        0.16 * np.sin(2.0 * np.pi * 760.0 * raw_time + 0.1)
        + 0.09 * np.sin(2.0 * np.pi * 1040.0 * raw_time + 0.7)
        + 0.02 * rng.normal(size=raw_frames).astype(np.float32)
    )
    user_audio *= np.hanning(raw_frames).astype(np.float32, copy=False)

    delay_samples = int(round(0.15 * sample_rate))
    echo_audio = np.zeros(raw_frames, dtype=np.float32)
    echo_span = min(reference_frames, raw_frames - delay_samples)
    echo_audio[delay_samples : delay_samples + echo_span] = 0.65 * reference_audio[:echo_span]
    recorded_audio = np.clip(user_audio + echo_audio, -1.0, 1.0)

    raw_audio_path = tmp_path / "recorded.wav"
    reference_audio_path = tmp_path / "reply.wav"
    processed_audio_path = tmp_path / "cleaned.wav"
    _write_wav(raw_audio_path, recorded_audio, sample_rate)
    _write_wav(reference_audio_path, reference_audio, sample_rate)

    result = cancel_playback_echo(
        raw_audio_path,
        reference_audio_path,
        output_path=processed_audio_path,
    )

    cleaned_audio, cleaned_sample_rate = soundfile.read(
        str(processed_audio_path),
        dtype="float32",
        always_2d=False,
    )

    assert cleaned_sample_rate == sample_rate
    assert result.applied is True
    assert Path(result.processed_audio_path) == processed_audio_path.resolve()
    assert delay_samples <= result.delay_samples <= delay_samples + int(sample_rate * 0.1)
    assert result.correlation_score > 0.75
    assert result.gain == pytest.approx(0.65, abs=0.18)

    raw_error = float(np.mean((recorded_audio - user_audio) ** 2))
    cleaned_error = float(np.mean((cleaned_audio - user_audio) ** 2))
    assert cleaned_error < raw_error * 0.55
