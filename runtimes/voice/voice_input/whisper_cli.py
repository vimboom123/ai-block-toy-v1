from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .models import AudioTranscription


class WhisperCliError(RuntimeError):
    pass


class WhisperCliTranscriber:
    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(
        self,
        *,
        command: str = "whisper",
        model: str = DEFAULT_MODEL,
        language: str | None = "zh",
        device: str = "cpu",
        task: str = "transcribe",
        threads: int | None = None,
    ) -> None:
        self.command = command
        self.model = model
        self.language = language
        self.device = device
        self.task = task
        self.threads = threads

    def transcribe(self, audio_path: str | Path) -> AudioTranscription:
        resolved_audio_path = Path(audio_path).expanduser().resolve()
        if not resolved_audio_path.is_file():
            raise WhisperCliError(f"Audio file not found: {resolved_audio_path}")

        command_path = self._resolve_command_path()

        with TemporaryDirectory(prefix="phase7-whisper-") as output_dir:
            command = [
                command_path,
                "--model",
                self.model,
                "--output_dir",
                output_dir,
                "--output_format",
                "json",
                "--verbose",
                "False",
                "--task",
                self.task,
                "--device",
                self.device,
                "--fp16",
                "False",
            ]
            if self.language:
                command.extend(["--language", self.language])
            if self.threads is not None:
                command.extend(["--threads", str(self.threads)])
            command.append(str(resolved_audio_path))

            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                error_message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
                raise WhisperCliError(f"whisper CLI failed: {error_message}") from exc

            output_path = Path(output_dir) / f"{resolved_audio_path.stem}.json"
            if not output_path.is_file():
                raise WhisperCliError(f"whisper CLI did not produce JSON output: {output_path}")

            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise WhisperCliError(f"whisper JSON output is not valid JSON: {output_path}") from exc

        transcript = self._extract_transcript(payload)
        if not transcript:
            raise WhisperCliError("whisper CLI returned an empty transcript")

        return AudioTranscription(
            audio_path=str(resolved_audio_path),
            transcript=transcript,
            input_mode="audio_file",
            asr_source="whisper_cli",
            model_name=self.model,
            language=self._normalize_optional_string(payload.get("language")),
            duration_seconds=self._extract_duration_seconds(payload),
        )

    def _resolve_command_path(self) -> str:
        if "/" in self.command:
            command_path = Path(self.command).expanduser().resolve()
            if not command_path.is_file():
                raise WhisperCliError(f"whisper command not found: {command_path}")
            return str(command_path)

        resolved_command = shutil.which(self.command)
        if resolved_command is None:
            raise WhisperCliError(f"whisper command not found in PATH: {self.command}")
        return resolved_command

    @staticmethod
    def _extract_transcript(payload: dict[str, Any]) -> str:
        transcript = WhisperCliTranscriber._normalize_optional_string(payload.get("text"))
        if transcript:
            return transcript

        segments = payload.get("segments")
        if not isinstance(segments, list):
            return ""

        parts = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_text = WhisperCliTranscriber._normalize_optional_string(segment.get("text"))
            if segment_text:
                parts.append(segment_text)
        return " ".join(parts).strip()

    @staticmethod
    def _extract_duration_seconds(payload: dict[str, Any]) -> float | None:
        raw_duration = payload.get("duration")
        if isinstance(raw_duration, (int, float)):
            return float(raw_duration)

        segments = payload.get("segments")
        if not isinstance(segments, list) or not segments:
            return None

        last_segment = segments[-1]
        if not isinstance(last_segment, dict):
            return None
        end_value = last_segment.get("end")
        if isinstance(end_value, (int, float)):
            return float(end_value)
        return None

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
