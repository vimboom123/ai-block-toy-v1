from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
from scipy.signal import correlate as scipy_correlate
from scipy.signal import correlation_lags

from .audio import DEFAULT_ASR_SAMPLE_RATE, _resample_mono_audio

DEFAULT_ECHO_CANCEL_ANALYSIS_SECONDS = 1.25
DEFAULT_ECHO_CANCEL_MAX_LAG_SECONDS = 1.0
DEFAULT_ECHO_CANCEL_ENVELOPE_TARGET_RATE = 100
DEFAULT_ECHO_CANCEL_COARSE_STEP_SECONDS = 0.01
DEFAULT_ECHO_CANCEL_FINE_STEP_SECONDS = 0.001
DEFAULT_ECHO_CANCEL_MIN_SEGMENT_SECONDS = 0.25
DEFAULT_ECHO_CANCEL_MIN_SCORE = 0.05
DEFAULT_ECHO_CANCEL_EPSILON = 1e-9


class EchoCancellationError(RuntimeError):
    """Raised when the local echo cancellation helper cannot process audio."""


@dataclass(frozen=True)
class EchoCancellationResult:
    raw_audio_path: str
    processed_audio_path: str
    reference_audio_path: str
    sample_rate: int
    applied: bool
    delay_samples: int
    delay_seconds: float
    gain: float
    correlation_score: float
    analysis_seconds: float
    max_lag_seconds: float
    method: str = "cross_correlation_subtraction"

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_audio_path": self.raw_audio_path,
            "processed_audio_path": self.processed_audio_path,
            "reference_audio_path": self.reference_audio_path,
            "sample_rate": self.sample_rate,
            "applied": self.applied,
            "delay_samples": self.delay_samples,
            "delay_seconds": round(self.delay_seconds, 4),
            "gain": round(self.gain, 6),
            "correlation_score": round(self.correlation_score, 6),
            "analysis_seconds": round(self.analysis_seconds, 3),
            "max_lag_seconds": round(self.max_lag_seconds, 3),
            "method": self.method,
        }


@dataclass(frozen=True)
class _LagScore:
    delay_samples: int
    correlation_score: float
    gain: float


def _load_mono_audio(
    audio_path: str | Path,
    *,
    target_sample_rate: int = DEFAULT_ASR_SAMPLE_RATE,
) -> tuple[np.ndarray, int]:
    resolved_audio_path = Path(audio_path).expanduser().resolve()
    if not resolved_audio_path.is_file():
        raise EchoCancellationError(f"Audio file not found: {resolved_audio_path}")

    audio_buffer, sample_rate = soundfile.read(
        str(resolved_audio_path),
        dtype="float32",
        always_2d=True,
    )
    if audio_buffer.size == 0:
        raise EchoCancellationError(f"Audio file is empty: {resolved_audio_path}")

    mono_audio = audio_buffer.mean(axis=1).astype(np.float32, copy=False)
    if sample_rate != target_sample_rate:
        mono_audio = _resample_mono_audio(
            mono_audio,
            source_sample_rate=sample_rate,
            target_sample_rate=target_sample_rate,
        )
        sample_rate = target_sample_rate
    return mono_audio, int(sample_rate)


def _build_processed_audio_path(raw_audio_path: Path, output_path: str | Path | None) -> Path:
    if output_path is not None:
        resolved_output_path = Path(output_path).expanduser()
        if resolved_output_path.suffix.lower() != ".wav":
            resolved_output_path = resolved_output_path.with_suffix(".wav")
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        return resolved_output_path.resolve()

    return raw_audio_path.with_name(f"{raw_audio_path.stem}-echo-cancel.wav").resolve()


def _build_envelope_audio(
    audio: np.ndarray,
    *,
    sample_rate: int,
    target_rate: int = DEFAULT_ECHO_CANCEL_ENVELOPE_TARGET_RATE,
) -> tuple[np.ndarray, int]:
    if audio.size == 0:
        return audio.astype(np.float32, copy=False), sample_rate

    target_rate = max(1, int(target_rate))
    downsample_factor = max(1, int(round(sample_rate / target_rate)))
    kernel = np.ones(downsample_factor, dtype=np.float32) / float(downsample_factor)
    smoothed = np.convolve(np.abs(audio).astype(np.float32, copy=False), kernel, mode="same")
    envelope_audio = smoothed[::downsample_factor].astype(np.float32, copy=False)
    effective_sample_rate = max(1, int(round(sample_rate / downsample_factor)))
    return envelope_audio, effective_sample_rate


def _normalize_for_correlation(audio: np.ndarray) -> tuple[np.ndarray, float]:
    centered_audio = audio.astype(np.float32, copy=False) - float(audio.mean())
    energy = float(np.dot(centered_audio, centered_audio))
    return centered_audio, energy


def _search_delay_via_fft(
    recorded_audio: np.ndarray,
    reference_audio: np.ndarray,
    *,
    sample_rate: int,
    max_lag_seconds: float,
    min_segment_seconds: float = DEFAULT_ECHO_CANCEL_MIN_SEGMENT_SECONDS,
) -> _LagScore | None:
    if recorded_audio.size == 0 or reference_audio.size == 0:
        return None

    max_lag_samples = max(0, int(round(max_lag_seconds * sample_rate)))
    min_segment_samples = max(1, int(round(min_segment_seconds * sample_rate)))
    recorded_centered, recorded_energy = _normalize_for_correlation(recorded_audio)
    reference_centered, reference_energy = _normalize_for_correlation(reference_audio)
    if recorded_energy <= DEFAULT_ECHO_CANCEL_EPSILON or reference_energy <= DEFAULT_ECHO_CANCEL_EPSILON:
        return None

    correlation = scipy_correlate(recorded_centered, reference_centered, mode="full", method="fft")
    lags = correlation_lags(recorded_centered.size, reference_centered.size, mode="full")
    valid_indexes = np.flatnonzero((lags >= 0) & (lags <= max_lag_samples))
    if valid_indexes.size == 0:
        return None

    normalized_correlation = correlation / float(np.sqrt(recorded_energy * reference_energy))
    best_index = valid_indexes[int(np.argmax(normalized_correlation[valid_indexes]))]
    delay_samples = int(lags[best_index])
    available_samples = min(recorded_audio.size - delay_samples, reference_audio.size)
    if available_samples < min_segment_samples:
        return None

    recorded_segment = recorded_centered[delay_samples : delay_samples + available_samples]
    reference_segment = reference_centered[:available_samples]
    reference_segment_energy = float(np.dot(reference_segment, reference_segment))
    if reference_segment_energy <= DEFAULT_ECHO_CANCEL_EPSILON:
        return None

    cross_energy = float(np.dot(recorded_segment, reference_segment))
    correlation_score = float(normalized_correlation[best_index])
    gain = cross_energy / reference_segment_energy
    return _LagScore(
        delay_samples=delay_samples,
        correlation_score=correlation_score,
        gain=gain,
    )


def _score_delay(
    recorded_audio: np.ndarray,
    reference_audio: np.ndarray,
    *,
    delay_samples: int,
    min_segment_samples: int,
) -> _LagScore | None:
    available_samples = min(recorded_audio.size - delay_samples, reference_audio.size)
    if available_samples < min_segment_samples:
        return None

    recorded_segment = recorded_audio[delay_samples : delay_samples + available_samples]
    reference_segment = reference_audio[:available_samples]

    recorded_segment = recorded_segment - float(recorded_segment.mean())
    reference_segment = reference_segment - float(reference_segment.mean())

    reference_energy = float(np.dot(reference_segment, reference_segment))
    recorded_energy = float(np.dot(recorded_segment, recorded_segment))
    if reference_energy <= DEFAULT_ECHO_CANCEL_EPSILON or recorded_energy <= DEFAULT_ECHO_CANCEL_EPSILON:
        return None

    cross_energy = float(np.dot(recorded_segment, reference_segment))
    correlation_score = cross_energy / float(np.sqrt(reference_energy * recorded_energy))
    gain = cross_energy / reference_energy
    return _LagScore(
        delay_samples=delay_samples,
        correlation_score=correlation_score,
        gain=gain,
    )


def _search_delay(
    recorded_audio: np.ndarray,
    reference_audio: np.ndarray,
    *,
    sample_rate: int,
    max_lag_seconds: float,
) -> _LagScore | None:
    max_lag_samples = max(0, int(round(max_lag_seconds * sample_rate)))
    min_segment_samples = max(1, int(round(DEFAULT_ECHO_CANCEL_MIN_SEGMENT_SECONDS * sample_rate)))
    coarse_step_samples = max(1, int(round(sample_rate * DEFAULT_ECHO_CANCEL_COARSE_STEP_SECONDS)))
    fine_step_samples = max(1, int(round(sample_rate * DEFAULT_ECHO_CANCEL_FINE_STEP_SECONDS)))

    best_score: _LagScore | None = None
    for delay_samples in range(0, max_lag_samples + 1, coarse_step_samples):
        score = _score_delay(
            recorded_audio,
            reference_audio,
            delay_samples=delay_samples,
            min_segment_samples=min_segment_samples,
        )
        if score is not None and (best_score is None or score.correlation_score > best_score.correlation_score):
            best_score = score

    if best_score is None:
        return None

    refined_start = max(0, best_score.delay_samples - coarse_step_samples)
    refined_stop = min(max_lag_samples, best_score.delay_samples + coarse_step_samples)
    for delay_samples in range(refined_start, refined_stop + 1, fine_step_samples):
        score = _score_delay(
            recorded_audio,
            reference_audio,
            delay_samples=delay_samples,
            min_segment_samples=min_segment_samples,
        )
        if score is not None and score.correlation_score > best_score.correlation_score:
            best_score = score

    return best_score


def cancel_playback_echo(
    raw_audio_path: str | Path,
    reference_audio_path: str | Path,
    *,
    output_path: str | Path | None = None,
    target_sample_rate: int = DEFAULT_ASR_SAMPLE_RATE,
    analysis_seconds: float = DEFAULT_ECHO_CANCEL_ANALYSIS_SECONDS,
    max_lag_seconds: float = DEFAULT_ECHO_CANCEL_MAX_LAG_SECONDS,
) -> EchoCancellationResult:
    resolved_raw_audio_path = Path(raw_audio_path).expanduser().resolve()
    resolved_reference_audio_path = Path(reference_audio_path).expanduser().resolve()
    if not resolved_raw_audio_path.is_file():
        raise EchoCancellationError(f"Audio file not found: {resolved_raw_audio_path}")
    if not resolved_reference_audio_path.is_file():
        raise EchoCancellationError(f"Reference audio file not found: {resolved_reference_audio_path}")

    recorded_audio, sample_rate = _load_mono_audio(
        resolved_raw_audio_path,
        target_sample_rate=target_sample_rate,
    )
    reference_audio, reference_sample_rate = _load_mono_audio(
        resolved_reference_audio_path,
        target_sample_rate=target_sample_rate,
    )
    if reference_sample_rate != sample_rate:
        raise EchoCancellationError(
            f"Sample rate mismatch after normalization: {reference_sample_rate} != {sample_rate}"
        )

    analysis_limit_samples = min(
        recorded_audio.size,
        reference_audio.size,
        max(1, int(round(analysis_seconds * sample_rate))),
    )
    analysis_recorded_audio = recorded_audio[:analysis_limit_samples]
    analysis_reference_audio = reference_audio[:analysis_limit_samples]

    reference_audio = reference_audio - float(reference_audio.mean())
    recorded_audio = recorded_audio - float(recorded_audio.mean())
    analysis_recorded_audio = analysis_recorded_audio - float(analysis_recorded_audio.mean())
    analysis_reference_audio = analysis_reference_audio - float(analysis_reference_audio.mean())

    raw_score = _search_delay_via_fft(
        analysis_recorded_audio,
        analysis_reference_audio,
        sample_rate=sample_rate,
        max_lag_seconds=max_lag_seconds,
    )
    envelope_recorded_audio, envelope_sample_rate = _build_envelope_audio(
        analysis_recorded_audio,
        sample_rate=sample_rate,
    )
    envelope_reference_audio, envelope_reference_sample_rate = _build_envelope_audio(
        analysis_reference_audio,
        sample_rate=sample_rate,
    )
    if envelope_reference_sample_rate != envelope_sample_rate:
        raise EchoCancellationError(
            "Envelope sample rate mismatch after normalization: "
            f"{envelope_reference_sample_rate} != {envelope_sample_rate}"
        )
    envelope_score = _search_delay_via_fft(
        envelope_recorded_audio,
        envelope_reference_audio,
        sample_rate=envelope_sample_rate,
        max_lag_seconds=max_lag_seconds,
    )

    candidate_scores = [score for score in (raw_score, envelope_score) if score is not None]
    best_score = max(candidate_scores, key=lambda score: score.correlation_score) if candidate_scores else None

    if best_score is not None:
        refine_step_samples = max(1, int(round(sample_rate * DEFAULT_ECHO_CANCEL_FINE_STEP_SECONDS)))
        refine_range_samples = max(
            refine_step_samples,
            int(round(sample_rate * DEFAULT_ECHO_CANCEL_COARSE_STEP_SECONDS)),
        )
        refine_start = max(0, best_score.delay_samples - refine_range_samples)
        refine_stop = min(
            int(round(max_lag_seconds * sample_rate)),
            best_score.delay_samples + refine_range_samples,
        )
        refined_best: _LagScore | None = None
        for delay_samples in range(refine_start, refine_stop + 1, refine_step_samples):
            score = _score_delay(
                recorded_audio,
                reference_audio,
                delay_samples=delay_samples,
                min_segment_samples=max(1, int(round(DEFAULT_ECHO_CANCEL_MIN_SEGMENT_SECONDS * sample_rate))),
            )
            if score is not None and (refined_best is None or score.correlation_score > refined_best.correlation_score):
                refined_best = score
        if refined_best is not None and refined_best.correlation_score >= best_score.correlation_score:
            best_score = refined_best

    processed_audio_path = _build_processed_audio_path(resolved_raw_audio_path, output_path)
    if best_score is None or best_score.correlation_score < DEFAULT_ECHO_CANCEL_MIN_SCORE:
        soundfile.write(
            str(processed_audio_path),
            recorded_audio,
            sample_rate,
            subtype="PCM_16",
            format="WAV",
        )
        return EchoCancellationResult(
            raw_audio_path=str(resolved_raw_audio_path),
            processed_audio_path=str(processed_audio_path),
            reference_audio_path=str(resolved_reference_audio_path),
            sample_rate=sample_rate,
            applied=False,
            delay_samples=0,
            delay_seconds=0.0,
            gain=0.0,
            correlation_score=best_score.correlation_score if best_score is not None else 0.0,
            analysis_seconds=min(
                analysis_seconds,
                recorded_audio.size / float(sample_rate),
                reference_audio.size / float(sample_rate),
            ),
            max_lag_seconds=max_lag_seconds,
        )

    effective_delay_samples = best_score.delay_samples
    echo_audio = np.zeros_like(recorded_audio)
    echo_length = min(reference_audio.size, max(0, recorded_audio.size - effective_delay_samples))
    if echo_length > 0:
        echo_audio[effective_delay_samples : effective_delay_samples + echo_length] = (
            best_score.gain * reference_audio[:echo_length]
        )

    cleaned_audio = np.clip(recorded_audio - echo_audio, -1.0, 1.0)
    soundfile.write(
        str(processed_audio_path),
        cleaned_audio,
        sample_rate,
        subtype="PCM_16",
        format="WAV",
    )
    return EchoCancellationResult(
        raw_audio_path=str(resolved_raw_audio_path),
        processed_audio_path=str(processed_audio_path),
        reference_audio_path=str(resolved_reference_audio_path),
        sample_rate=sample_rate,
        applied=True,
        delay_samples=effective_delay_samples,
        delay_seconds=effective_delay_samples / float(sample_rate),
        gain=best_score.gain,
        correlation_score=best_score.correlation_score,
        analysis_seconds=min(
            analysis_seconds,
            recorded_audio.size / float(sample_rate),
            reference_audio.size / float(sample_rate),
        ),
        max_lag_seconds=max_lag_seconds,
    )
