from __future__ import annotations

import queue
import time
from pathlib import Path

import numpy as np
import sounddevice
import soundfile

from .models import RecordedAudioClip


class AudioRecordingError(RuntimeError):
    pass


class OneShotWavRecorder:
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_SUBTYPE = "PCM_16"

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: str | int | None = None,
        subtype: str = DEFAULT_SUBTYPE,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than 0")
        if channels <= 0:
            raise ValueError("channels must be greater than 0")
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.subtype = subtype

    def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
        if seconds <= 0:
            raise ValueError("seconds must be greater than 0")

        resolved_output_path = self._normalize_output_path(output_path)
        frame_count = max(1, int(round(seconds * self.sample_rate)))

        try:
            audio_buffer = sounddevice.rec(
                frame_count,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=self.device,
            )
            sounddevice.wait()
        except Exception as exc:  # pragma: no cover - PortAudio error shape depends on host runtime.
            raise AudioRecordingError(f"sounddevice recording failed: {exc}") from exc

        try:
            soundfile.write(
                str(resolved_output_path),
                audio_buffer,
                self.sample_rate,
                subtype=self.subtype,
                format="WAV",
            )
        except Exception as exc:  # pragma: no cover - libsndfile error shape depends on host runtime.
            raise AudioRecordingError(f"soundfile write failed: {exc}") from exc

        return RecordedAudioClip(
            audio_path=str(resolved_output_path),
            input_mode="mic_record_once",
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_seconds=seconds,
            device=self.device,
        )

    @staticmethod
    def _normalize_output_path(output_path: str | Path) -> Path:
        resolved_output_path = Path(output_path).expanduser()
        if resolved_output_path.suffix.lower() != ".wav":
            resolved_output_path = resolved_output_path.with_suffix(".wav")
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        return resolved_output_path.resolve()


class SilenceAwareWavRecorder(OneShotWavRecorder):
    DEFAULT_BLOCK_DURATION_SECONDS = 0.05
    DEFAULT_START_RMS_THRESHOLD = 0.008
    DEFAULT_STOP_RMS_THRESHOLD = 0.008
    DEFAULT_MIN_SPEECH_SECONDS = 0.16
    DEFAULT_SILENCE_SECONDS = 1.0
    DEFAULT_NOISE_FLOOR_CALIBRATION_SECONDS = 0.35
    DEFAULT_START_THRESHOLD_MULTIPLIER = 2.2
    DEFAULT_STOP_THRESHOLD_MULTIPLIER = 1.9
    DEFAULT_MIN_START_THRESHOLD_DELTA = 0.003
    DEFAULT_MIN_STOP_THRESHOLD_DELTA = 0.0025

    def __init__(
        self,
        *,
        sample_rate: int = OneShotWavRecorder.DEFAULT_SAMPLE_RATE,
        channels: int = OneShotWavRecorder.DEFAULT_CHANNELS,
        device: str | int | None = None,
        subtype: str = OneShotWavRecorder.DEFAULT_SUBTYPE,
        block_duration_seconds: float = DEFAULT_BLOCK_DURATION_SECONDS,
        start_rms_threshold: float = DEFAULT_START_RMS_THRESHOLD,
        stop_rms_threshold: float = DEFAULT_STOP_RMS_THRESHOLD,
        min_speech_seconds: float = DEFAULT_MIN_SPEECH_SECONDS,
        silence_seconds: float = DEFAULT_SILENCE_SECONDS,
        noise_floor_calibration_seconds: float = DEFAULT_NOISE_FLOOR_CALIBRATION_SECONDS,
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            channels=channels,
            device=device,
            subtype=subtype,
        )
        if block_duration_seconds <= 0:
            raise ValueError("block_duration_seconds must be greater than 0")
        if start_rms_threshold <= 0:
            raise ValueError("start_rms_threshold must be greater than 0")
        if stop_rms_threshold <= 0:
            raise ValueError("stop_rms_threshold must be greater than 0")
        if min_speech_seconds <= 0:
            raise ValueError("min_speech_seconds must be greater than 0")
        if silence_seconds <= 0:
            raise ValueError("silence_seconds must be greater than 0")
        if noise_floor_calibration_seconds < 0:
            raise ValueError("noise_floor_calibration_seconds must be greater than or equal to 0")
        self.block_duration_seconds = block_duration_seconds
        self.start_rms_threshold = start_rms_threshold
        self.stop_rms_threshold = stop_rms_threshold
        self.min_speech_seconds = min_speech_seconds
        self.silence_seconds = silence_seconds
        self.noise_floor_calibration_seconds = noise_floor_calibration_seconds

    @classmethod
    def _derive_thresholds(cls, noise_floor_rms: float | None, *, base_start: float, base_stop: float) -> tuple[float, float]:
        effective_start = max(base_start, base_stop)
        effective_stop = min(base_stop, effective_start * 0.9)
        if noise_floor_rms is None or noise_floor_rms <= 0:
            return effective_start, max(1e-6, effective_stop)

        effective_start = max(
            effective_start,
            noise_floor_rms * cls.DEFAULT_START_THRESHOLD_MULTIPLIER,
            noise_floor_rms + cls.DEFAULT_MIN_START_THRESHOLD_DELTA,
        )
        effective_stop = max(
            base_stop,
            noise_floor_rms * cls.DEFAULT_STOP_THRESHOLD_MULTIPLIER,
            noise_floor_rms + cls.DEFAULT_MIN_STOP_THRESHOLD_DELTA,
        )
        effective_stop = min(effective_stop, effective_start * 0.9)
        return effective_start, max(1e-6, effective_stop)

    def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
        if seconds <= 0:
            raise ValueError("seconds must be greater than 0")

        resolved_output_path = self._normalize_output_path(output_path)
        blocksize = max(1, int(round(self.sample_rate * self.block_duration_seconds)))
        captured_chunks: list[np.ndarray] = []
        audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        listening_started = False
        speech_seconds = 0.0
        silence_seconds = 0.0
        started_at = time.monotonic()
        pre_speech_rms_samples: list[float] = []
        effective_start_threshold = self.start_rms_threshold
        effective_stop_threshold = self.stop_rms_threshold

        def _callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
            del frames, time_info, status
            audio_queue.put(indata.copy())

        try:
            with sounddevice.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=self.device,
                blocksize=blocksize,
                callback=_callback,
            ):
                while True:
                    elapsed = time.monotonic() - started_at
                    if elapsed >= seconds:
                        break
                    timeout_seconds = max(0.01, min(0.1, seconds - elapsed))
                    try:
                        chunk = audio_queue.get(timeout=timeout_seconds)
                    except queue.Empty:
                        continue

                    captured_chunks.append(chunk)
                    chunk_duration = chunk.shape[0] / float(self.sample_rate)
                    chunk_buffer = chunk.astype(np.float32, copy=False)
                    rms = float(np.sqrt(np.mean(chunk_buffer * chunk_buffer)))

                    if not listening_started:
                        pre_speech_rms_samples.append(rms)
                        noise_floor_rms = None
                        calibration_chunk_count = int(
                            self.noise_floor_calibration_seconds / self.block_duration_seconds
                        )
                        calibration_complete = calibration_chunk_count <= 0 or len(pre_speech_rms_samples) >= calibration_chunk_count
                        if calibration_complete:
                            noise_floor_rms = float(np.median(np.asarray(pre_speech_rms_samples, dtype=np.float32)))
                        effective_start_threshold, effective_stop_threshold = self._derive_thresholds(
                            noise_floor_rms,
                            base_start=self.start_rms_threshold,
                            base_stop=self.stop_rms_threshold,
                        )
                        if not calibration_complete and rms < self.start_rms_threshold * 2.5:
                            continue
                        if rms >= effective_start_threshold:
                            listening_started = True

                    if listening_started:
                        speech_seconds += chunk_duration
                        if rms < effective_stop_threshold:
                            silence_seconds += chunk_duration
                        else:
                            silence_seconds = 0.0
                        if speech_seconds >= self.min_speech_seconds and silence_seconds >= self.silence_seconds:
                            break
        except Exception as exc:  # pragma: no cover - PortAudio error shape depends on host runtime.
            raise AudioRecordingError(f"sounddevice listening failed: {exc}") from exc

        if not captured_chunks:
            raise AudioRecordingError("sounddevice listening failed: no audio captured")

        audio_buffer = np.concatenate(captured_chunks, axis=0)
        try:
            soundfile.write(
                str(resolved_output_path),
                audio_buffer,
                self.sample_rate,
                subtype=self.subtype,
                format="WAV",
            )
        except Exception as exc:  # pragma: no cover - libsndfile error shape depends on host runtime.
            raise AudioRecordingError(f"soundfile write failed: {exc}") from exc

        return RecordedAudioClip(
            audio_path=str(resolved_output_path),
            input_mode="mic_listen_until_silence",
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_seconds=audio_buffer.shape[0] / float(self.sample_rate),
            device=self.device,
        )
