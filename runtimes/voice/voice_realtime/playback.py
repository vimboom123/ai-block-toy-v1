from __future__ import annotations

from contextlib import AbstractContextManager
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import numpy as np
import sounddevice
import soundfile


class AudioPlaybackError(RuntimeError):
    """Raised when audio playback cannot be completed."""


def _scale_pcm16_audio(pcm16_audio: bytes, gain: float) -> bytes:
    if gain <= 0:
        raise AudioPlaybackError("gain must be greater than 0")
    if gain == 1.0:
        return pcm16_audio
    if len(pcm16_audio) % 2 != 0:
        raise AudioPlaybackError("pcm16_audio must contain whole samples")
    audio_array = np.frombuffer(pcm16_audio, dtype="<i2").astype(np.int32, copy=False)
    scaled_audio = np.clip(np.round(audio_array * float(gain)), -32768, 32767).astype("<i2")
    return scaled_audio.tobytes()


class Pcm16OutputStream(AbstractContextManager["Pcm16OutputStream"]):
    """Stream PCM16 audio directly to the current output device."""

    def __init__(
        self,
        *,
        sample_rate: int,
        device: str | int | None = None,
        gain: float = 1.0,
    ) -> None:
        if sample_rate <= 0:
            raise AudioPlaybackError("sample_rate must be greater than 0")
        if gain <= 0:
            raise AudioPlaybackError("gain must be greater than 0")
        self.sample_rate = sample_rate
        self.device = device
        self.gain = gain
        self._stream: sounddevice.RawOutputStream | None = None
        self._leftover = bytearray()

    def __enter__(self) -> "Pcm16OutputStream":
        if self._stream is not None:
            return self
        try:
            self._stream = sounddevice.RawOutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                device=self.device,
            )
            self._stream.__enter__()
        except Exception as exc:  # pragma: no cover - host audio backend dependent.
            raise AudioPlaybackError(f"sounddevice streaming playback failed: {exc}") from exc
        return self

    def write(self, pcm16_audio: bytes) -> None:
        if self._stream is None:
            raise AudioPlaybackError("stream is not open")
        if not pcm16_audio:
            return
        data = bytes(self._leftover) + pcm16_audio
        if len(data) % 2 != 0:
            self._leftover = bytearray(data[-1:])
            data = data[:-1]
        else:
            self._leftover.clear()
        if not data:
            return
        scaled_data = _scale_pcm16_audio(data, self.gain)
        try:
            self._stream.write(scaled_data)
        except Exception as exc:  # pragma: no cover - host audio backend dependent.
            raise AudioPlaybackError(f"sounddevice streaming playback failed: {exc}") from exc

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            if self._leftover:
                self._stream.write(_scale_pcm16_audio(bytes(self._leftover) + b"\x00", self.gain))
        except Exception:
            pass
        try:
            self._stream.__exit__(None, None, None)
        finally:
            self._stream = None
            self._leftover.clear()

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False


def play_audio_file(
    audio_path: str | Path,
    *,
    device: str | int | None = None,
    gain: float = 1.0,
) -> None:
    resolved_audio_path = Path(audio_path).expanduser().resolve()
    if not resolved_audio_path.is_file():
        raise AudioPlaybackError(f"Audio file not found: {resolved_audio_path}")
    if gain <= 0:
        raise AudioPlaybackError("gain must be greater than 0")

    try:
        audio_buffer, sample_rate = soundfile.read(
            str(resolved_audio_path),
            dtype="float32",
            always_2d=True,
        )
        if gain != 1.0:
            audio_buffer = audio_buffer * float(gain)
            audio_buffer = audio_buffer.clip(-1.0, 1.0)
        sounddevice.play(audio_buffer, samplerate=sample_rate, device=device)
        sounddevice.wait()
        return
    except Exception as exc:
        afplay_path = shutil.which("afplay")
        if afplay_path is None:
            raise AudioPlaybackError(f"sounddevice playback failed and afplay not found: {exc}") from exc

        try:
            playback_source_path = resolved_audio_path
            if gain != 1.0:
                audio_buffer, sample_rate = soundfile.read(
                    str(resolved_audio_path),
                    dtype="float32",
                    always_2d=True,
                )
                scaled_audio_buffer = (audio_buffer * float(gain)).clip(-1.0, 1.0)
                with tempfile.NamedTemporaryFile(prefix="ai-block-toy-playback-", suffix=".wav", delete=False) as temp_file:
                    temp_path = Path(temp_file.name)
                soundfile.write(
                    str(temp_path),
                    scaled_audio_buffer,
                    sample_rate,
                    subtype="PCM_16",
                    format="WAV",
                )
                playback_source_path = temp_path

            subprocess.run(
                [afplay_path, str(playback_source_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            if gain != 1.0 and playback_source_path != resolved_audio_path:
                try:
                    playback_source_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
                except Exception:
                    pass
        except subprocess.CalledProcessError as afplay_exc:
            error_message = afplay_exc.stderr.strip() or afplay_exc.stdout.strip() or str(afplay_exc)
            raise AudioPlaybackError(f"afplay failed: {error_message}") from afplay_exc
