from __future__ import annotations

import base64
import json
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest
import numpy as np
import soundfile

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from voice_input import (  # noqa: E402
    AudioTranscription,
    OneShotWavRecorder,
    QwenRealtimeAsrTranscriber,
    RecordedAudioClip,
    SilenceAwareWavRecorder,
    WhisperCliTranscriber,
)
from voice_output import (  # noqa: E402
    QwenRealtimeTtsProvider,
    QwenTtsProvider,
    SpeechSynthesisError,
    SynthesizedSpeech,
)
from input_understanding import CompletionPoint, RuleFirstSignalResolver, TaskContext  # noqa: E402


def load_script_module(script_name: str):
    script_path = ROOT_DIR / "scripts" / script_name
    module_name = f"phase7_voice_{script_name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fake_whisper_script(tmp_path: Path, transcript: str = "我要开消防车去救火") -> Path:
    script_path = tmp_path / "fake_whisper.py"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "from pathlib import Path",
                "",
                "args = sys.argv[1:]",
                "output_dir = Path(args[args.index('--output_dir') + 1])",
                "audio_path = Path(args[-1])",
                "payload = {",
                f"    'text': {transcript!r},",
                "    'language': 'zh',",
                "    'duration': 1.25,",
                "}",
                "output_dir.mkdir(parents=True, exist_ok=True)",
                "(output_dir / f'{audio_path.stem}.json').write_text(",
                "    json.dumps(payload, ensure_ascii=False),",
                "    encoding='utf-8',",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return script_path


def test_whisper_cli_transcriber_reads_json_output(tmp_path: Path) -> None:
    fake_whisper = _write_fake_whisper_script(tmp_path)
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"not-real-audio")

    transcriber = WhisperCliTranscriber(
        command=str(fake_whisper),
        model="fake-large-v3-turbo",
        language="zh",
    )
    transcription = transcriber.transcribe(audio_file)

    assert transcription.audio_path == str(audio_file.resolve())
    assert transcription.transcript == "我要开消防车去救火"
    assert transcription.asr_source == "whisper_cli"
    assert transcription.model_name == "fake-large-v3-turbo"
    assert transcription.language == "zh"
    assert transcription.duration_seconds == 1.25


def test_run_voice_input_script_bridges_transcript_to_phase7_pipeline(tmp_path: Path) -> None:
    fake_whisper = _write_fake_whisper_script(tmp_path, transcript="我要开消防车去灭火")
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"not-real-audio")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "scripts" / "run_voice_input.py"),
            "--audio-file",
            str(audio_file),
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--completion-point",
            "救火:救火,灭火",
            "--runtime-mode",
            "legacy",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "none",
            "--no-playback",
            "--whisper-command",
            str(fake_whisper),
            "--whisper-model",
            "fake-large-v3-turbo",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )

    payload = json.loads(completed.stdout)

    assert payload["audio_transcription"]["transcript"] == "我要开消防车去灭火"
    assert payload["child_input_text"] == "我要开消防车去灭火"
    assert payload["signal_resolution"]["task_signal"] == "task_completed"
    assert payload["phase6_turn_payload"]["child_input_text"] == "我要开消防车去灭火"
    assert payload["phase6_turn_payload"]["task_signal"] == "task_completed"
    assert payload["phase6_turn_payload"]["signal_reason"] == payload["signal_resolution"]["reason"]
    assert payload["phase6_turn_payload"]["interaction_mode"] == payload["interaction_generation"]["interaction_mode"]
    assert payload["interaction_context"]["task_signal"] == "task_completed"


def test_run_voice_fast_uses_terminal_declarative_reply_when_phase6_session_ends() -> None:
    script_module = load_script_module("run_voice_fast.py")
    current_task = TaskContext(
        task_id="fs_006",
        task_name="回站总结",
        task_goal="复述刚才发生了什么",
        expected_child_action="用自己的话按顺序总结",
        completion_points=(CompletionPoint.parse("回站总结:总结,刚才,先,然后,最后,回站"),),
        completion_match_mode="any",
    )

    reply_text = script_module._compose_phase6_guided_reply(
        base_reply="说得真清楚！那下次如果火源出现在厨房或者卧室，你会怎么做呢？",
        current_task=current_task,
        next_current_task=current_task,
        phase6_response={
            "session": {
                "status": "ended",
            }
        },
    )

    assert reply_text == "说得真清楚，这次我们先找到线索、再让角色动起来、最后把任务处理好了，今天顺利完成啦。"
    assert "？" not in reply_text


def test_run_voice_fast_uses_assistant_led_terminal_summary_when_phase6_auto_closes() -> None:
    script_module = load_script_module("run_voice_fast.py")
    current_task = TaskContext(
        task_id="fs_005",
        task_name="救援执行",
        task_goal="把消防车和消防员摆过去处理火点",
        expected_child_action="把消防车和消防员摆过去",
        completion_points=(CompletionPoint.parse("救援执行:消防车,消防员,救援"),),
        completion_match_mode="any",
    )

    reply_text = script_module._compose_phase6_guided_reply(
        base_reply="太好了，我们已经把火扑住啦。",
        current_task=current_task,
        next_current_task=current_task,
        phase6_response={
            "session": {
                "status": "ended",
                "story_title": "停机坪边的值班警情",
            },
            "tasks": [
                {
                    "task_id": "fs_001",
                    "status": "completed",
                    "selected_entities": ["铃铛", "大火"],
                },
                {
                    "task_id": "fs_003",
                    "status": "completed",
                    "selected_entities": ["消防直升机", "两位消防小人"],
                },
                {
                    "task_id": "fs_005",
                    "status": "completed",
                    "selected_entities": ["消防直升机", "大火"],
                },
                {
                    "task_id": "fs_006",
                    "status": "completed",
                    "assistant_led_summary": True,
                },
            ],
        },
    )

    assert "停机坪边的值班警情" in reply_text
    assert "消防站这轮顺利收尾啦" in reply_text
    assert "？" not in reply_text


def test_start_voice_ui_demo_reuses_running_phase6_and_launches_voice_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_module = load_script_module("start_voice_ui_demo.py")
    browser_urls: list[str] = []
    commands_seen: list[list[str]] = []

    monkeypatch.setattr(
        script_module,
        "_probe_phase6_server",
        lambda port, timeout_seconds=1.0: {
            "ok": True,
            "state_machine_version": "ai_block_toy_state_machine_v1",
            "latest_session_id": "ses_demo_001",
            "default_task_ids": ["fs_001", "fs_002", "fs_003", "fs_004", "fs_005", "fs_006"],
        },
    )
    monkeypatch.setattr(
        script_module,
        "_start_phase6_server",
        lambda port: (_ for _ in ()).throw(AssertionError("should not start phase6 when already healthy")),
    )
    monkeypatch.setattr(script_module.webbrowser, "open", lambda url, new=1: browser_urls.append(url) or True)

    def fake_run(command, cwd=None):  # type: ignore[no-untyped-def]
        commands_seen.append(command)
        assert cwd == script_module.ROOT_DIR
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(script_module.subprocess, "run", fake_run)

    exit_code = script_module.main([])

    assert exit_code == 0
    assert browser_urls == ["http://127.0.0.1:4183/"]
    assert commands_seen
    command = commands_seen[0]
    assert command[0] == sys.executable
    assert str(script_module.VOICE_FAST_SCRIPT_PATH) in command
    assert "--submit-phase6" in command
    assert "--phase6-api-base" in command
    assert "http://127.0.0.1:4183/api/session-runtime" in command
    assert "--stream-tts" in command
    assert "--task-id" in command and "fs_001" in command


def test_start_voice_ui_demo_can_boot_phase6_before_launching_voice_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script_module = load_script_module("start_voice_ui_demo.py")
    browser_urls: list[str] = []
    commands_seen: list[list[str]] = []
    probe_calls = {"count": 0}

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):  # type: ignore[no-untyped-def]
            return None

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):  # type: ignore[no-untyped-def]
            return 0

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    fake_process = FakeProcess()

    def fake_probe(port, timeout_seconds=1.0):  # type: ignore[no-untyped-def]
        del port, timeout_seconds
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return None
        return {"ok": True, "state_machine_version": "ai_block_toy_state_machine_v1"}

    log_path = tmp_path / "phase6.log"
    log_file = log_path.open("a", encoding="utf-8")

    monkeypatch.setattr(script_module, "_probe_phase6_server", fake_probe)
    monkeypatch.setattr(
        script_module,
        "_start_phase6_server",
        lambda port: (fake_process, log_path, log_file),
    )
    monkeypatch.setattr(script_module.webbrowser, "open", lambda url, new=1: browser_urls.append(url) or True)

    def fake_run(command, cwd=None):  # type: ignore[no-untyped-def]
        commands_seen.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(script_module.subprocess, "run", fake_run)

    exit_code = script_module.main(["--shutdown-started-phase6-on-exit"])

    assert exit_code == 0
    assert probe_calls["count"] >= 2
    assert browser_urls == ["http://127.0.0.1:4183/"]
    assert commands_seen
    assert fake_process.terminated is True
    log_file.close()


def test_start_voice_ui_demo_replaces_stale_phase6_without_state_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script_module = load_script_module("start_voice_ui_demo.py")
    browser_urls: list[str] = []
    commands_seen: list[list[str]] = []
    probe_calls = {"count": 0}

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None

        def poll(self):  # type: ignore[no-untyped-def]
            return None

    fake_process = FakeProcess()
    log_path = tmp_path / "phase6-replaced.log"
    log_file = log_path.open("a", encoding="utf-8")

    def fake_probe(port, timeout_seconds=1.0):  # type: ignore[no-untyped-def]
        del port, timeout_seconds
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return {
                "ok": True,
                "latest_session_id": "ses_demo_legacy",
            }
        return {
            "ok": True,
            "state_machine_version": "ai_block_toy_state_machine_v1",
            "latest_session_id": "ses_demo_new",
        }

    monkeypatch.setattr(script_module, "_probe_phase6_server", fake_probe)
    monkeypatch.setattr(script_module, "_stop_listening_processes_on_port", lambda port: [4321])
    monkeypatch.setattr(
        script_module,
        "_start_phase6_server",
        lambda port: (fake_process, log_path, log_file),
    )
    monkeypatch.setattr(script_module.webbrowser, "open", lambda url, new=1: browser_urls.append(url) or True)

    def fake_run(command, cwd=None):  # type: ignore[no-untyped-def]
        commands_seen.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(script_module.subprocess, "run", fake_run)

    exit_code = script_module.main(["--no-open-browser"])

    assert exit_code == 0
    assert probe_calls["count"] >= 2
    assert browser_urls == []
    assert commands_seen
    log_file.close()


def test_start_voice_ui_demo_raises_when_stale_phase6_cannot_be_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_module = load_script_module("start_voice_ui_demo.py")

    monkeypatch.setattr(
        script_module,
        "_probe_phase6_server",
        lambda port, timeout_seconds=1.0: {
            "ok": True,
            "latest_session_id": "ses_demo_legacy",
        },
    )
    monkeypatch.setattr(script_module, "_stop_listening_processes_on_port", lambda port: [])

    with pytest.raises(RuntimeError, match="could not be replaced automatically"):
        script_module.main(["--no-open-browser"])


def test_silence_aware_recorder_derives_higher_thresholds_for_noisy_rooms() -> None:
    quiet_start, quiet_stop = SilenceAwareWavRecorder._derive_thresholds(
        None,
        base_start=SilenceAwareWavRecorder.DEFAULT_START_RMS_THRESHOLD,
        base_stop=SilenceAwareWavRecorder.DEFAULT_STOP_RMS_THRESHOLD,
    )
    noisy_start, noisy_stop = SilenceAwareWavRecorder._derive_thresholds(
        0.009,
        base_start=SilenceAwareWavRecorder.DEFAULT_START_RMS_THRESHOLD,
        base_stop=SilenceAwareWavRecorder.DEFAULT_STOP_RMS_THRESHOLD,
    )

    assert quiet_start == pytest.approx(SilenceAwareWavRecorder.DEFAULT_START_RMS_THRESHOLD)
    assert quiet_stop == pytest.approx(quiet_start * 0.9)
    assert noisy_start > quiet_start
    assert noisy_stop > quiet_stop
    assert noisy_stop < noisy_start


def test_qwen_tts_provider_writes_audio_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env_key in (
        "QWEN_TTS_API_KEY",
        "DASHSCOPE_TTS_API_KEY",
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_TTS_MODEL",
        "DASHSCOPE_TTS_MODEL",
        "QWEN_TTS_BASE_URL",
        "DASHSCOPE_TTS_BASE_URL",
        "QWEN_BASE_URL",
        "DASHSCOPE_BASE_URL",
        "QWEN_TTS_VOICE",
        "DASHSCOPE_TTS_VOICE",
    ):
        monkeypatch.delenv(env_key, raising=False)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_TTS_MODEL=qwen-tts-test",
                "QWEN_TTS_VOICE=Cherry",
            )
        ),
        encoding="utf-8",
    )

    class FakeApiHeaders:
        def get_content_type(self) -> str:
            return "application/json"

    class FakeAudioHeaders:
        def get_content_type(self) -> str:
            return "audio/wav"

    class FakeApiResponse:
        headers = FakeApiHeaders()

        def __enter__(self) -> "FakeApiResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "output": {
                        "audio": {
                            "url": "https://example.com/reply.wav",
                        }
                    }
                }
            ).encode("utf-8")

    class FakeAudioResponse:
        headers = FakeAudioHeaders()

        def __enter__(self) -> "FakeAudioResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return b"RIFFfake-wav-data"

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        assert timeout == 30.0
        if isinstance(req, str):
            assert req == "https://example.com/reply.wav"
            return FakeAudioResponse()

        payload = json.loads(req.data.decode("utf-8"))
        assert req.full_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        assert payload == {
            "model": "qwen-tts-test",
            "input": {
                "text": "对，就是去救火。",
                "voice": "Cherry",
                "language_type": "Chinese",
            },
            "parameters": {
                "response_format": "wav",
            },
        }
        return FakeApiResponse()

    monkeypatch.setattr("voice_output.synthesizer.request.urlopen", fake_urlopen)

    provider = QwenTtsProvider(root_dir=tmp_path)
    synthesized = provider.synthesize(
        text="对，就是去救火。",
        output_path=tmp_path / "phase7-reply",
    )

    synthesized_path = Path(synthesized.audio_path)
    assert synthesized.provider_name == "qwen_tts"
    assert synthesized.model_name == "qwen-tts-test"
    assert synthesized.voice == "Cherry"
    assert synthesized.audio_format == "wav"
    assert synthesized_path.suffix == ".wav"
    assert synthesized_path.read_bytes() == b"RIFFfake-wav-data"


def test_qwen_tts_provider_rewrites_legacy_compatible_mode_request_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_TTS_MODEL=qwen-tts-test",
                "QWEN_TTS_VOICE=Cherry",
                "QWEN_TTS_REQUEST_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/audio/speech",
            )
        ),
        encoding="utf-8",
    )

    provider = QwenTtsProvider(root_dir=tmp_path)

    assert provider.config.request_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def test_qwen_tts_provider_prefers_audio_url_over_stray_base64(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env_key in (
        "QWEN_TTS_API_KEY",
        "DASHSCOPE_TTS_API_KEY",
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_TTS_MODEL",
        "DASHSCOPE_TTS_MODEL",
        "QWEN_TTS_BASE_URL",
        "DASHSCOPE_TTS_BASE_URL",
        "QWEN_BASE_URL",
        "DASHSCOPE_BASE_URL",
        "QWEN_TTS_VOICE",
        "DASHSCOPE_TTS_VOICE",
    ):
        monkeypatch.delenv(env_key, raising=False)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_TTS_MODEL=qwen-tts-test",
                "QWEN_TTS_VOICE=Cherry",
            )
        ),
        encoding="utf-8",
    )

    class FakeApiHeaders:
        def get_content_type(self) -> str:
            return "application/json"

    class FakeAudioHeaders:
        def get_content_type(self) -> str:
            return "audio/wav"

    class FakeApiResponse:
        headers = FakeApiHeaders()

        def __enter__(self) -> "FakeApiResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "id": "YWJj",  # valid base64 for "abc", but not audio
                    "output": {
                        "audio": {
                            "url": "https://example.com/reply.wav",
                        }
                    },
                }
            ).encode("utf-8")

    class FakeAudioResponse:
        headers = FakeAudioHeaders()

        def __enter__(self) -> "FakeAudioResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return b"RIFFfake-wav-data"

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        assert timeout == 30.0
        if isinstance(req, str):
            assert req == "https://example.com/reply.wav"
            return FakeAudioResponse()

        payload = json.loads(req.data.decode("utf-8"))
        assert req.full_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        assert payload["model"] == "qwen-tts-test"
        return FakeApiResponse()

    monkeypatch.setattr("voice_output.synthesizer.request.urlopen", fake_urlopen)

    provider = QwenTtsProvider(root_dir=tmp_path)
    synthesized = provider.synthesize(
        text="对，就是去救火。",
        output_path=tmp_path / "phase7-reply",
    )

    synthesized_path = Path(synthesized.audio_path)
    assert synthesized.audio_format == "wav"
    assert synthesized_path.suffix == ".wav"
    assert synthesized_path.read_bytes() == b"RIFFfake-wav-data"


def test_auto_tts_falls_back_to_macos_say(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voice_output.synthesizer as synthesizer_module

    class FakeQwenTtsProvider:
        def __init__(self, **_: object) -> None:
            pass

        def synthesize(self, *, text: str, output_path: str | Path | None = None) -> SynthesizedSpeech:
            del text, output_path
            raise SpeechSynthesisError("Qwen TTS API connection failed")

    class FakeSystemSayTtsProvider:
        def __init__(self, *, voice: str | None = None) -> None:
            self.voice = voice

        def synthesize(self, *, text: str, output_path: str | Path | None = None) -> SynthesizedSpeech:
            return SynthesizedSpeech(
                audio_path=str((Path(output_path or tmp_path / "phase7-reply.aiff")).resolve()),
                text=text,
                input_mode="reply_text",
                provider_name="macos_say",
                model_name="say",
                audio_format="aiff",
                voice=self.voice,
            )

    monkeypatch.setattr(synthesizer_module, "QwenTtsProvider", FakeQwenTtsProvider)
    monkeypatch.setattr(synthesizer_module, "SystemSayTtsProvider", FakeSystemSayTtsProvider)

    synthesized = synthesizer_module.synthesize_reply_audio(
        text="对，就是去救火。",
        provider_mode="auto",
        output_path=tmp_path / "phase7-reply",
        say_voice="Tingting",
    )

    assert synthesized is not None
    assert synthesized.provider_name == "macos_say"
    assert synthesized.voice == "Tingting"
    assert synthesized.audio_format == "aiff"
    assert synthesized.fallback_reason == "Qwen TTS API connection failed"


def test_one_shot_wav_recorder_uses_sounddevice_and_soundfile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voice_input.recorder as recorder_module

    record_calls: dict[str, object] = {}
    wait_calls: list[str] = []
    write_calls: dict[str, object] = {}

    def fake_rec(
        frames: int,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        device: str | int | None,
    ) -> str:
        record_calls.update(
            {
                "frames": frames,
                "samplerate": samplerate,
                "channels": channels,
                "dtype": dtype,
                "device": device,
            }
        )
        return "fake-audio-buffer"

    def fake_wait() -> None:
        wait_calls.append("waited")

    def fake_write(
        path: str,
        data: str,
        samplerate: int,
        *,
        subtype: str,
        format: str,
    ) -> None:
        write_calls.update(
            {
                "path": path,
                "data": data,
                "samplerate": samplerate,
                "subtype": subtype,
                "format": format,
            }
        )

    monkeypatch.setattr(recorder_module.sounddevice, "rec", fake_rec)
    monkeypatch.setattr(recorder_module.sounddevice, "wait", fake_wait)
    monkeypatch.setattr(recorder_module.soundfile, "write", fake_write)

    recorder = OneShotWavRecorder(sample_rate=16000, channels=1, device="Fake Mic")
    recording = recorder.record(seconds=1.25, output_path=tmp_path / "phase7-take")

    expected_path = (tmp_path / "phase7-take.wav").resolve()

    assert recording.audio_path == str(expected_path)
    assert recording.input_mode == "mic_record_once"
    assert recording.sample_rate == 16000
    assert recording.channels == 1
    assert recording.duration_seconds == 1.25
    assert recording.device == "Fake Mic"
    assert record_calls == {
        "frames": 20000,
        "samplerate": 16000,
        "channels": 1,
        "dtype": "float32",
        "device": "Fake Mic",
    }
    assert wait_calls == ["waited"]
    assert write_calls == {
        "path": str(expected_path),
        "data": "fake-audio-buffer",
        "samplerate": 16000,
        "subtype": "PCM_16",
        "format": "WAV",
    }


def test_run_voice_input_script_can_record_then_bridge_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_input.py")
    recorded_audio_path = (tmp_path / "recorded.wav").resolve()
    tts_audio_path = (tmp_path / "reply.wav").resolve()

    class FakeRecorder:
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1

        def __init__(self, *, sample_rate: int, channels: int, device: str | None = None) -> None:
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            Path(output_path).expanduser().resolve().write_bytes(b"fake-mic-audio")
            return RecordedAudioClip(
                audio_path=str(Path(output_path).expanduser().resolve()),
                input_mode="mic_record_once",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=seconds,
                device=self.device,
            )

    class FakeTranscriber:
        DEFAULT_MODEL = "fake-large-v3-turbo"

        def __init__(
            self,
            *,
            command: str,
            model: str,
            language: str | None,
            device: str,
            task: str,
            threads: int | None,
        ) -> None:
            del command, language, device, task, threads
            self.model = model

        def transcribe(self, audio_path: str | Path) -> AudioTranscription:
            return AudioTranscription(
                audio_path=str(Path(audio_path).expanduser().resolve()),
                transcript="我要开消防车去灭火",
                input_mode="audio_file",
                asr_source="whisper_cli",
                model_name=self.model,
                language="zh",
                duration_seconds=1.0,
            )

    def fake_synthesize_reply_audio(
        *,
        text: str,
        provider_mode: str,
        output_path: str | Path | None,
        qwen_model: str | None,
        qwen_voice: str | None,
        qwen_audio_format: str | None,
        tts_timeout_seconds: float | None,
        say_voice: str | None,
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice
        assert "救火" in text
        assert provider_mode == "auto"
        assert output_path == str(tts_audio_path)
        return SynthesizedSpeech(
            audio_path=str(tts_audio_path),
            text=text,
            input_mode="reply_text",
            provider_name="qwen_tts",
            model_name="qwen-tts-test",
            audio_format="wav",
        )

    monkeypatch.setattr(script_module, "OneShotWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "WhisperCliTranscriber", FakeTranscriber)
    monkeypatch.setattr(script_module, "synthesize_reply_audio", fake_synthesize_reply_audio)

    exit_code = script_module.main(
        [
            "--record-seconds",
            "1.5",
            "--record-output-file",
            str(recorded_audio_path),
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--completion-point",
            "救火:救火,灭火",
            "--runtime-mode",
            "legacy",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "auto",
            "--no-playback",
            "--tts-output-file",
            str(tts_audio_path),
            "--whisper-model",
            "fake-large-v3-turbo",
        ]
    )

    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["audio_recording"] == {
        "audio_path": str(recorded_audio_path),
        "input_mode": "mic_record_once",
        "sample_rate": 16000,
        "channels": 1,
        "duration_seconds": 1.5,
    }
    assert payload["audio_transcription"]["audio_path"] == str(recorded_audio_path)
    assert payload["audio_transcription"]["transcript"] == "我要开消防车去灭火"
    assert payload["signal_resolution"]["task_signal"] == "task_completed"
    assert payload["phase6_turn_payload"]["task_signal"] == "task_completed"
    assert payload["child_input_text"] == "我要开消防车去灭火"
    assert payload["tts_output"]["ok"] is True
    assert payload["tts_output"]["audio_path"] == str(tts_audio_path)
    assert "救火" in payload["tts_output"]["text"]
    assert payload["tts_output"]["input_mode"] == "reply_text"
    assert payload["tts_output"]["provider_name"] == "qwen_tts"
    assert payload["tts_output"]["model_name"] == "qwen-tts-test"
    assert payload["tts_output"]["audio_format"] == "wav"


def test_qwen_realtime_asr_transcriber_uses_websocket_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voice_input.realtime_asr as realtime_asr_module

    class FakeWebSocket:
        def __init__(self, messages: list[str]) -> None:
            self.messages = messages
            self.sent_messages: list[str] = []
            self.timeout_seconds: float | None = None

        def settimeout(self, timeout_seconds: float) -> None:
            self.timeout_seconds = timeout_seconds

        def send(self, message: str) -> None:
            self.sent_messages.append(message)

        def recv(self) -> str:
            if not self.messages:
                raise AssertionError("recv called too many times")
            return self.messages.pop(0)

        def close(self) -> None:
            return None

    audio_path = tmp_path / "asr.wav"
    samples = np.zeros(1600, dtype=np.float32)
    soundfile.write(str(audio_path), samples, 16000, subtype="PCM_16", format="WAV")

    fake_ws = FakeWebSocket(
        [
            json.dumps({"type": "session.updated"}),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "我要开消防车去救火",
                }
            ),
            json.dumps({"type": "session.finished"}),
        ]
    )
    monkeypatch.setattr(realtime_asr_module.websocket, "create_connection", lambda *args, **kwargs: fake_ws)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_RT_ASR_MODEL=qwen-asr-test",
            )
        ),
        encoding="utf-8",
    )

    transcriber = QwenRealtimeAsrTranscriber(root_dir=tmp_path)
    transcription = transcriber.transcribe(audio_path)

    assert transcription.transcript == "我要开消防车去救火"
    assert transcription.asr_source == "qwen_asr_realtime"
    assert transcription.model_name == "qwen-asr-test"
    assert transcription.input_mode == "audio_file"
    assert transcription.duration_seconds is not None
    assert fake_ws.timeout_seconds == pytest.approx(transcriber.config.timeout_seconds, rel=1e-3)

    sent_types = [json.loads(message)["type"] for message in fake_ws.sent_messages]
    assert sent_types[:4] == [
        "session.update",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "session.finish",
    ]


def test_qwen_realtime_tts_provider_writes_pcm_wav(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voice_output.realtime_tts as realtime_tts_module

    class FakeWebSocket:
        def __init__(self, messages: list[str]) -> None:
            self.messages = messages
            self.sent_messages: list[str] = []
            self.timeout_seconds: float | None = None

        def settimeout(self, timeout_seconds: float) -> None:
            self.timeout_seconds = timeout_seconds

        def send(self, message: str) -> None:
            self.sent_messages.append(message)

        def recv(self) -> str:
            if not self.messages:
                raise AssertionError("recv called too many times")
            return self.messages.pop(0)

        def close(self) -> None:
            return None

    pcm16_audio = np.array([0, 32767, -32768, 12000], dtype="<i2").tobytes()
    fake_ws = FakeWebSocket(
        [
            json.dumps({"type": "session.updated"}),
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm16_audio).decode("ascii"),
                }
            ),
            json.dumps({"type": "response.done"}),
            json.dumps({"type": "session.finished"}),
        ]
    )
    monkeypatch.setattr(realtime_tts_module.websocket, "create_connection", lambda *args, **kwargs: fake_ws)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_RT_TTS_MODEL=qwen-tts-test",
                "QWEN_RT_TTS_VOICE=Cherry",
            )
        ),
        encoding="utf-8",
    )

    provider = QwenRealtimeTtsProvider(root_dir=tmp_path)
    synthesized = provider.synthesize(text="对，就是去救火。", output_path=tmp_path / "reply")

    synthesized_path = Path(synthesized.audio_path)
    assert synthesized.provider_name == "qwen_tts_realtime"
    assert synthesized.model_name == "qwen-tts-test"
    assert synthesized.voice == "Cherry"
    assert synthesized.audio_format == "wav"
    assert synthesized_path.suffix == ".wav"
    assert synthesized_path.is_file()
    assert fake_ws.timeout_seconds == provider.config.timeout_seconds

    read_audio, read_sample_rate = soundfile.read(str(synthesized_path), dtype="int16")
    assert read_sample_rate == 24000
    assert read_audio.shape[0] >= 4

    sent_types = [json.loads(message)["type"] for message in fake_ws.sent_messages]
    assert sent_types[:4] == [
        "session.update",
        "input_text_buffer.append",
        "input_text_buffer.commit",
        "session.finish",
    ]


def test_qwen_realtime_tts_provider_streams_audio_chunks_before_finish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import voice_output.realtime_tts as realtime_tts_module

    class FakeWebSocket:
        def __init__(self, messages: list[str]) -> None:
            self.messages = messages
            self.sent_messages: list[str] = []
            self.timeout_seconds: float | None = None

        def settimeout(self, timeout_seconds: float) -> None:
            self.timeout_seconds = timeout_seconds

        def send(self, message: str) -> None:
            self.sent_messages.append(message)

        def recv(self) -> str:
            if len(self.sent_messages) < 4:
                raise AssertionError("streaming websocket should have sent all setup messages first")
            if not self.messages:
                raise AssertionError("recv called too many times")
            if self.messages and "second-chunk" in self.messages[0]:
                assert write_order, "first audio chunk must be written before later chunks arrive"
            return self.messages.pop(0)

        def close(self) -> None:
            return None

    class FakeStream:
        def __init__(self, *, sample_rate: int, device: str | int | None = None, gain: float = 1.0) -> None:
            self.sample_rate = sample_rate
            self.device = device
            self.gain = gain

        def __enter__(self) -> "FakeStream":
            write_order.append("stream_open")
            return self

        def write(self, data: bytes) -> None:
            write_order.append(f"write:{len(data)}")

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            write_order.append("stream_close")
            return False

    pcm16_audio = np.array([0, 32767, -32768, 12000], dtype="<i2").tobytes()
    write_order: list[str] = []
    fake_ws = FakeWebSocket(
        [
            json.dumps({"type": "session.updated"}),
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm16_audio[:4]).decode("ascii"),
                    "marker": "first-chunk",
                }
            ),
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(pcm16_audio[4:]).decode("ascii"),
                    "marker": "second-chunk",
                }
            ),
            json.dumps({"type": "session.finished"}),
        ]
    )
    monkeypatch.setattr(realtime_tts_module.websocket, "create_connection", lambda *args, **kwargs: fake_ws)
    monkeypatch.setattr(realtime_tts_module, "Pcm16OutputStream", FakeStream)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_RT_TTS_MODEL=qwen-tts-test",
                "QWEN_RT_TTS_VOICE=Cherry",
            )
        ),
        encoding="utf-8",
    )

    provider = QwenRealtimeTtsProvider(root_dir=tmp_path)
    synthesized = provider.synthesize(
        text="对，就是去救火。",
        output_path=tmp_path / "reply",
        stream_playback=True,
        playback_gain=0.75,
    )

    synthesized_path = Path(synthesized.audio_path)
    assert synthesized.provider_name == "qwen_tts_realtime"
    assert synthesized.model_name == "qwen-tts-test"
    assert synthesized_path.suffix == ".wav"
    assert synthesized_path.is_file()
    assert fake_ws.timeout_seconds == provider.config.timeout_seconds
    assert write_order[0] == "stream_open"
    assert any(entry.startswith("write:") for entry in write_order)
    assert write_order[-1] == "stream_close"

    sent_types = [json.loads(message)["type"] for message in fake_ws.sent_messages]
    assert sent_types[:4] == [
        "session.update",
        "input_text_buffer.append",
        "input_text_buffer.commit",
        "session.finish",
    ]


def test_run_voice_input_script_defaults_to_realtime_runtime_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_input.py")
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"not-real-audio")

    class FakeRealtimeTranscriber:
        def __init__(
            self,
            *,
            model: str | None,
            language: str | None,
            request_timeout_seconds: float | None,
        ) -> None:
            self.model = model
            self.language = language
            self.request_timeout_seconds = request_timeout_seconds

        def transcribe(self, audio_path: str | Path) -> AudioTranscription:
            return AudioTranscription(
                audio_path=str(Path(audio_path).expanduser().resolve()),
                transcript="我要开消防车去救火",
                input_mode="audio_file",
                asr_source="qwen_asr_realtime",
                model_name=self.model or "qwen3-asr-flash-realtime",
                language=self.language or "zh",
                duration_seconds=1.0,
            )

    def fake_synthesize_realtime_reply_audio(
        *,
        text: str,
        provider_mode: str,
        output_path: str | Path | None,
        qwen_model: str | None,
        qwen_voice: str | None,
        qwen_audio_format: str | None,
        tts_timeout_seconds: float | None,
        say_voice: str | None,
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice
        assert provider_mode == "auto"
        if text != "你好，准备好了吗？我们开始吧！":
            assert "救火" in text
        assert output_path is None
        return SynthesizedSpeech(
            audio_path=str((tmp_path / "reply.wav").resolve()),
            text=text,
            input_mode="reply_text",
            provider_name="qwen_tts_realtime",
            model_name="qwen3-tts-flash-realtime",
            audio_format="wav",
        )

    monkeypatch.setattr(script_module, "QwenRealtimeAsrTranscriber", FakeRealtimeTranscriber)
    monkeypatch.setattr(script_module, "synthesize_realtime_reply_audio", fake_synthesize_realtime_reply_audio)
    played_audio_paths: list[str] = []

    def fake_play_audio_file(
        audio_path: str | Path,
        *,
        device: str | int | None = None,
        gain: float = 1.0,
    ) -> None:
        del device
        assert gain == pytest.approx(1.0)
        played_audio_paths.append(str(Path(audio_path).resolve()))

    monkeypatch.setattr(script_module, "play_audio_file", fake_play_audio_file)

    exit_code = script_module.main(
        [
            "--audio-file",
            str(audio_file),
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--completion-point",
            "救火:救火,灭火",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "auto",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runtime_mode"] == "realtime"
    assert payload["audio_transcription"]["asr_source"] == "qwen_asr_realtime"
    assert payload["tts_output"]["provider_name"] == "qwen_tts_realtime"
    assert payload["tts_output"]["playback_ok"] is True
    assert payload["signal_resolution"]["task_signal"] == "task_completed"
    assert played_audio_paths == [str((tmp_path / "reply.wav").resolve())]


def test_run_voice_fast_script_honors_tts_provider_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_fast.py")
    tts_calls: list[dict[str, object]] = []
    playback_calls: list[tuple[str, float]] = []
    recorded_paths: list[Path] = []

    class FakeRecorder:
        def __init__(
            self,
            *,
            sample_rate: int = 16000,
            channels: int = 1,
            device: str | None = None,
            subtype: str = "PCM_16",
            min_speech_seconds: float = 0.15,
            silence_seconds: float = 0.25,
        ) -> None:
            del subtype, min_speech_seconds, silence_seconds
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            del seconds
            resolved_path = Path(output_path).expanduser().resolve()
            resolved_path.write_bytes(b"fake-mic-audio")
            recorded_paths.append(resolved_path)
            return RecordedAudioClip(
                audio_path=str(resolved_path),
                input_mode="mic_listen_until_silence",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=1.0,
                device=self.device,
            )

    class FakeBridgePackage:
        def __init__(self) -> None:
            self.signal_resolution = type(
                "SignalResolution",
                (),
                {"task_signal": "task_completed"},
            )()
            self.interaction_generation = type(
                "InteractionGeneration",
                (),
                {"reply_text": "对，就是去救火。"},
            )()

        def to_dict(self) -> dict[str, object]:
            return {
                "signal_resolution": {"task_signal": "task_completed"},
                "interaction_generation": {"reply_text": "对，就是去救火。"},
                "phase6_turn_payload": {
                    "child_input_text": "我要开消防车去救火",
                    "task_signal": "task_completed",
                },
            }

    def fake_http_asr(audio_path: Path, *, api_key: str) -> str:
        assert api_key == "test-key"
        assert recorded_paths
        assert audio_path == recorded_paths[-1]
        return "我要开消防车去救火"

    def fake_run_phase7_turn_pipeline(
        *,
        child_input_text: str,
        current_task: TaskContext,
        interaction_provider: str = "qwen",
        provider_fast_timeout_seconds: float,
        provider_keep_trying_timeout_seconds: float,
        provider_keep_trying_retry_timeout_seconds: float,
        session_memory_summary: str | None = None,
        session_id: str | None = None,
        next_task_hint: TaskContext | None = None,
    ) -> FakeBridgePackage:
        del current_task, provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds
        del provider_keep_trying_retry_timeout_seconds, session_memory_summary, session_id, next_task_hint
        assert child_input_text == "我要开消防车去救火"
        assert interaction_provider == "template"
        return FakeBridgePackage()

    def fake_synthesize_reply_audio(
        *,
        text: str,
        provider_mode: str = "auto",
        output_path: str | Path | None = None,
        qwen_model: str | None = None,
        qwen_voice: str | None = None,
        qwen_audio_format: str | None = None,
        tts_timeout_seconds: float | None = None,
        say_voice: str | None = None,
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice
        assert provider_mode == "qwen"
        if text != "好了，开始吧。":
            assert "救火" in text
        tts_calls.append(
            {
                "text": text,
                "provider_mode": provider_mode,
                "output_path": output_path,
            }
        )
        resolved_output_path = Path(output_path or tmp_path / f"reply-{len(tts_calls)}.wav").expanduser().resolve()
        resolved_output_path.write_bytes(b"fake-reply-audio")
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name="macos_say",
            model_name="say",
            audio_format="aiff",
        )

    def fake_play_blocking(audio_path: Path, *, gain: float = 0.6) -> None:
        playback_calls.append((str(Path(audio_path).resolve()), gain))

    monkeypatch.setattr(script_module, "build_runtime_env", lambda _: {"QWEN_API_KEY": "test-key"})
    monkeypatch.setattr(script_module, "SilenceAwareWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "_http_asr", fake_http_asr)
    monkeypatch.setattr(script_module, "run_phase7_turn_pipeline", fake_run_phase7_turn_pipeline)
    monkeypatch.setattr(script_module, "synthesize_reply_audio", fake_synthesize_reply_audio)
    monkeypatch.setattr(script_module, "_play_blocking", fake_play_blocking)

    exit_code = script_module.main(
        [
            "--record-seconds",
            "1.0",
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "qwen",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["turn"] == 1
    assert payload["child"] == "我要开消防车去救火"
    assert payload["signal"] == "task_completed"
    assert payload["reply"] == "对，就是去救火。"
    assert len(tts_calls) == 2
    assert all(call["provider_mode"] == "qwen" for call in tts_calls)
    assert len(playback_calls) == 2
    assert all(gain == pytest.approx(0.6) for _, gain in playback_calls)


def test_run_voice_fast_script_defaults_to_fast_local_tts_with_greeting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_fast.py")
    tts_calls: list[dict[str, object]] = []
    playback_calls: list[tuple[str, float]] = []
    recorded_paths: list[Path] = []

    class FakeRecorder:
        def __init__(
            self,
            *,
            sample_rate: int = 16000,
            channels: int = 1,
            device: str | None = None,
            subtype: str = "PCM_16",
            min_speech_seconds: float = 0.15,
            silence_seconds: float = 0.25,
        ) -> None:
            del subtype, min_speech_seconds, silence_seconds
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            del seconds
            resolved_path = Path(output_path).expanduser().resolve()
            resolved_path.write_bytes(b"fake-mic-audio")
            recorded_paths.append(resolved_path)
            return RecordedAudioClip(
                audio_path=str(resolved_path),
                input_mode="mic_listen_until_silence",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=1.0,
                device=self.device,
            )

    class FakeBridgePackage:
        def __init__(self) -> None:
            self.signal_resolution = type(
                "SignalResolution",
                (),
                {"task_signal": "task_completed"},
            )()
            self.interaction_generation = type(
                "InteractionGeneration",
                (),
                {"reply_text": "对，就是去救火。"},
            )()

        def to_dict(self) -> dict[str, object]:
            return {
                "signal_resolution": {"task_signal": "task_completed"},
                "interaction_generation": {"reply_text": "对，就是去救火。"},
                "phase6_turn_payload": {
                    "child_input_text": "我要开消防车去救火",
                    "task_signal": "task_completed",
                },
            }

    def fake_http_asr(audio_path: Path, *, api_key: str) -> str:
        assert api_key == "test-key"
        assert recorded_paths
        assert audio_path == recorded_paths[-1]
        return "我要开消防车去救火"

    def fake_run_phase7_turn_pipeline(
        *,
        child_input_text: str,
        current_task: TaskContext,
        interaction_provider: str = "qwen",
        provider_fast_timeout_seconds: float,
        provider_keep_trying_timeout_seconds: float,
        provider_keep_trying_retry_timeout_seconds: float,
        session_memory_summary: str | None = None,
        session_id: str | None = None,
        next_task_hint: TaskContext | None = None,
    ) -> FakeBridgePackage:
        del current_task, provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds
        del provider_keep_trying_retry_timeout_seconds, session_memory_summary, session_id, next_task_hint
        assert child_input_text == "我要开消防车去救火"
        assert interaction_provider == "template"
        return FakeBridgePackage()

    def fake_synthesize_reply_audio(
        *,
        text: str,
        provider_mode: str = "auto",
        output_path: str | Path | None = None,
        qwen_model: str | None = None,
        qwen_voice: str | None = None,
        qwen_audio_format: str | None = None,
        tts_timeout_seconds: float | None = None,
        say_voice: str | None = None,
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice
        assert provider_mode == "auto"
        if text != "好了，开始吧。":
            assert "救火" in text
        tts_calls.append(
            {
                "text": text,
                "provider_mode": provider_mode,
                "output_path": output_path,
            }
        )
        resolved_output_path = Path(output_path or tmp_path / f"reply-{len(tts_calls)}.wav").expanduser().resolve()
        resolved_output_path.write_bytes(b"fake-reply-audio")
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name="macos_say",
            model_name="say",
            audio_format="aiff",
        )

    def fake_play_blocking(audio_path: Path, *, gain: float = 0.6) -> None:
        playback_calls.append((str(Path(audio_path).resolve()), gain))

    monkeypatch.setattr(script_module, "build_runtime_env", lambda _: {"QWEN_API_KEY": "test-key"})
    monkeypatch.setattr(script_module, "SilenceAwareWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "_http_asr", fake_http_asr)
    monkeypatch.setattr(script_module, "run_phase7_turn_pipeline", fake_run_phase7_turn_pipeline)
    monkeypatch.setattr(script_module, "synthesize_reply_audio", fake_synthesize_reply_audio)
    monkeypatch.setattr(script_module, "_play_blocking", fake_play_blocking)

    exit_code = script_module.main(
        [
            "--record-seconds",
            "1.0",
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "auto",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["turn"] == 1
    assert payload["child"] == "我要开消防车去救火"
    assert payload["signal"] == "task_completed"
    assert payload["reply"] == "对，就是去救火。"
    assert len(tts_calls) == 2
    assert all(call["provider_mode"] == "auto" for call in tts_calls)
    assert len(playback_calls) == 2
    assert all(gain == pytest.approx(0.6) for _, gain in playback_calls)


def test_run_voice_fast_script_streams_qwen_tts_playback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_fast.py")
    stream_calls: list[dict[str, object]] = []
    playback_calls: list[tuple[str, float]] = []
    recorded_paths: list[Path] = []

    class FakeRecorder:
        def __init__(
            self,
            *,
            sample_rate: int = 16000,
            channels: int = 1,
            device: str | None = None,
            subtype: str = "PCM_16",
            min_speech_seconds: float = 0.15,
            silence_seconds: float = 0.25,
        ) -> None:
            del subtype, min_speech_seconds, silence_seconds
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            del seconds
            resolved_path = Path(output_path).expanduser().resolve()
            resolved_path.write_bytes(b"fake-mic-audio")
            recorded_paths.append(resolved_path)
            return RecordedAudioClip(
                audio_path=str(resolved_path),
                input_mode="mic_listen_until_silence",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=1.0,
                device=self.device,
            )

    class FakeBridgePackage:
        def __init__(self) -> None:
            self.signal_resolution = type(
                "SignalResolution",
                (),
                {"task_signal": "task_completed"},
            )()
            self.interaction_generation = type(
                "InteractionGeneration",
                (),
                {"reply_text": "对，就是去救火。"},
            )()

        def to_dict(self) -> dict[str, object]:
            return {
                "signal_resolution": {"task_signal": "task_completed"},
                "interaction_generation": {"reply_text": "对，就是去救火。"},
                "phase6_turn_payload": {
                    "child_input_text": "我要开消防车去救火",
                    "task_signal": "task_completed",
                },
            }

    def fake_http_asr(audio_path: Path, *, api_key: str) -> str:
        assert api_key == "test-key"
        assert recorded_paths
        assert audio_path == recorded_paths[-1]
        return "我要开消防车去救火"

    def fake_run_phase7_turn_pipeline(
        *,
        child_input_text: str,
        current_task: TaskContext,
        interaction_provider: str = "qwen",
        provider_fast_timeout_seconds: float,
        provider_keep_trying_timeout_seconds: float,
        provider_keep_trying_retry_timeout_seconds: float,
        session_memory_summary: str | None = None,
        session_id: str | None = None,
        next_task_hint: TaskContext | None = None,
    ) -> FakeBridgePackage:
        del current_task, provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds
        del provider_keep_trying_retry_timeout_seconds, session_memory_summary, session_id, next_task_hint
        assert child_input_text == "我要开消防车去救火"
        assert interaction_provider == "template"
        return FakeBridgePackage()

    def fake_stream_tts(
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
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice, reply_ready_at
        assert provider_mode == "qwen"
        stream_calls.append(
            {
                "text": text,
                "provider_mode": provider_mode,
                "output_path": output_path,
                "playback_device": playback_device,
                "playback_gain": playback_gain,
            }
        )
        resolved_output_path = Path(output_path or tmp_path / f"stream-{len(stream_calls)}.wav").expanduser().resolve()
        resolved_output_path.write_bytes(b"fake-reply-audio")
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name="qwen_tts_realtime",
            model_name="qwen3-tts-flash-realtime",
            audio_format="wav",
        )

    def fake_play_blocking(audio_path: Path, *, gain: float = 0.6) -> None:
        playback_calls.append((str(Path(audio_path).resolve()), gain))

    monkeypatch.setattr(script_module, "build_runtime_env", lambda _: {"QWEN_API_KEY": "test-key"})
    monkeypatch.setattr(script_module, "SilenceAwareWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "_http_asr", fake_http_asr)
    monkeypatch.setattr(script_module, "run_phase7_turn_pipeline", fake_run_phase7_turn_pipeline)
    monkeypatch.setattr(script_module, "synthesize_and_play_realtime_reply_audio", fake_stream_tts)
    monkeypatch.setattr(script_module, "_play_blocking", fake_play_blocking)

    exit_code = script_module.main(
        [
            "--record-seconds",
            "1.0",
            "--task-id",
            "fs_004",
            "--task-name",
            "消防车出动",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
            "--interaction-provider",
            "template",
            "--tts-provider",
            "qwen",
            "--stream-tts",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["turn"] == 1
    assert payload["child"] == "我要开消防车去救火"
    assert payload["signal"] == "task_completed"
    assert payload["reply"] == "对，就是去救火。"
    assert len(stream_calls) == 2
    assert all(call["provider_mode"] == "qwen" for call in stream_calls)
    assert all(call["playback_gain"] == pytest.approx(0.6) for call in stream_calls)
    assert len(playback_calls) == 0


def test_run_voice_fast_script_can_run_full_phase6_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_fast.py")
    stream_calls: list[dict[str, object]] = []
    playback_calls: list[tuple[str, float]] = []
    recorded_paths: list[Path] = []
    phase6_client_instances: list[object] = []
    asr_calls = {"count": 0}
    bridge_calls = {"count": 0}
    keep_trying_retry_timeouts_seen: list[float] = []

    class FakeRecorder:
        def __init__(
            self,
            *,
            sample_rate: int = 16000,
            channels: int = 1,
            device: str | None = None,
            subtype: str = "PCM_16",
            min_speech_seconds: float = 0.15,
            silence_seconds: float = 0.25,
        ) -> None:
            del subtype, min_speech_seconds, silence_seconds
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            del seconds
            resolved_path = Path(output_path).expanduser().resolve()
            resolved_path.write_bytes(b"fake-mic-audio")
            recorded_paths.append(resolved_path)
            return RecordedAudioClip(
                audio_path=str(resolved_path),
                input_mode="mic_listen_until_silence",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=1.0,
                device=self.device,
            )

    class FakeBridgePackage:
        def __init__(self, *, reply_text: str) -> None:
            self.signal_resolution = type(
                "SignalResolution",
                (),
                {"task_signal": "task_completed"},
            )()
            self.interaction_generation = type(
                "InteractionGeneration",
                (),
                {"reply_text": reply_text},
            )()
            self.phase6_turn_payload = type(
                "Phase6TurnPayload",
                (),
                {
                    "child_input_text": "我要开消防车去救火",
                    "task_signal": "task_completed",
                },
            )()

        def to_dict(self) -> dict[str, object]:
            return {
                "signal_resolution": {"task_signal": "task_completed"},
                "interaction_generation": {"reply_text": self.interaction_generation.reply_text},
                "phase6_turn_payload": {
                    "child_input_text": "我要开消防车去救火",
                    "task_signal": "task_completed",
                },
                "interaction_context": {
                    "recent_turn_summary": "孩子刚刚围绕消防车继续回应。",
                },
            }

    def fake_http_asr(audio_path: Path, *, api_key: str) -> str:
        assert api_key == "test-key"
        assert recorded_paths
        assert audio_path == recorded_paths[-1]
        asr_calls["count"] += 1
        return "我要开消防车去救火"

    def fake_run_phase7_turn_pipeline(
        *,
        child_input_text: str,
        current_task: TaskContext,
        interaction_provider: str = "qwen",
        provider_fast_timeout_seconds: float,
        provider_keep_trying_timeout_seconds: float,
        provider_keep_trying_retry_timeout_seconds: float,
        session_memory_summary: str | None = None,
        session_id: str | None = None,
        next_task_hint: TaskContext | None = None,
    ) -> FakeBridgePackage:
        del provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds
        del session_memory_summary, session_id
        bridge_calls["count"] += 1
        keep_trying_retry_timeouts_seen.append(provider_keep_trying_retry_timeout_seconds)
        assert child_input_text == "我要开消防车去救火"
        assert interaction_provider == "template"
        assert current_task.task_id in {"fs_001", "fs_002"}
        if current_task.task_id == "fs_001":
            assert next_task_hint is not None and next_task_hint.task_id == "fs_002"
        else:
            assert next_task_hint is not None and next_task_hint.task_id == "fs_003"
        reply_text = "第一轮继续救火。" if bridge_calls["count"] == 1 else "第二轮继续救火。"
        return FakeBridgePackage(reply_text=reply_text)

    def fake_stream_tts(
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
    ) -> SynthesizedSpeech:
        del qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice, reply_ready_at
        assert provider_mode == "qwen"
        stream_calls.append(
            {
                "text": text,
                "provider_mode": provider_mode,
                "output_path": output_path,
                "playback_device": playback_device,
                "playback_gain": playback_gain,
            }
        )
        resolved_output_path = Path(output_path or tmp_path / f"reply-{len(stream_calls)}.wav").expanduser().resolve()
        resolved_output_path.write_bytes(b"fake-reply-audio")
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name="qwen_tts_realtime",
            model_name="qwen3-tts-flash-realtime",
            audio_format="wav",
        )

    def fake_play_blocking(audio_path: Path, *, gain: float = 0.6) -> None:
        playback_calls.append((str(Path(audio_path).resolve()), gain))

    class FakePhase6Client:
        def __init__(self, api_base: str) -> None:
            self.api_base = api_base
            self.submissions = 0
            self.created_task_ids: list[str] | None = None
            phase6_client_instances.append(self)

        def create_session(self, task_ids=None):  # type: ignore[no-untyped-def]
            self.created_task_ids = list(task_ids or [])
            assert self.created_task_ids == ["fs_001", "fs_002", "fs_003", "fs_004", "fs_005", "fs_006"]
            return {
                "ok": True,
                "session": {
                    "session_id": "ses_voice_001",
                    "status": "active",
                    "current_task_id": "fs_001",
                },
                "current_task": {
                    "task_id": "fs_001",
                    "name": "场景识别",
                    "goal": "说出哪些是能动的，哪些只是画在墙上的",
                    "expected_child_action": "区分可操作元素与背景元素",
                },
            }

        def submit_turn(self, session_id: str, payload):  # type: ignore[no-untyped-def]
            del session_id
            self.submissions += 1
            assert payload.child_input_text == "我要开消防车去救火"
            assert payload.task_signal == "task_completed"
            if self.submissions == 1:
                return {
                    "ok": True,
                    "session": {
                        "session_id": "ses_voice_001",
                        "status": "active",
                        "current_task_id": "fs_002",
                    },
                    "current_task": {
                        "task_id": "fs_002",
                        "name": "接警判断",
                        "goal": "在指挥台确认这次求助来自哪里",
                        "expected_child_action": "说出内部火警或外部场景火警",
                    },
                }
            return {
                "ok": True,
                "session": {
                    "session_id": "ses_voice_001",
                    "status": "ended",
                    "current_task_id": None,
                },
                "current_task": None,
            }

    monkeypatch.setattr(script_module, "build_runtime_env", lambda _: {"QWEN_API_KEY": "test-key"})
    monkeypatch.setattr(script_module, "SilenceAwareWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "_http_asr", fake_http_asr)
    monkeypatch.setattr(script_module, "run_phase7_turn_pipeline", fake_run_phase7_turn_pipeline)
    monkeypatch.setattr(script_module, "synthesize_and_play_realtime_reply_audio", fake_stream_tts)
    monkeypatch.setattr(script_module, "_play_blocking", fake_play_blocking)
    monkeypatch.setattr(script_module, "_load_fire_station_task_blueprints", lambda: (
        "fire_station",
        [{"task_id": f"fs_00{i}"} for i in range(1, 7)],
    ))
    monkeypatch.setattr(script_module, "Phase6SessionClient", FakePhase6Client)
    monkeypatch.setattr(script_module.random, "choice", lambda seq: seq[0])

    exit_code = script_module.main(
        [
            "--record-seconds",
            "1.0",
            "--max-turns",
            "4",
            "--task-id",
            "fs_001",
            "--task-name",
            "场景识别",
            "--task-goal",
            "说出哪些是能动的，哪些只是画在墙上的",
            "--expected-child-action",
            "区分可操作元素与背景元素",
            "--interaction-provider",
            "template",
            "--phase6-api-base",
            "http://127.0.0.1:4183/api/session-runtime",
            "--submit-phase6",
            "--tts-provider",
            "qwen",
        ]
    )

    assert exit_code == 0
    stdout_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("{")]
    payloads = [json.loads(line) for line in stdout_lines]
    assert len(payloads) == 2
    assert payloads[0]["session_id"] == "ses_voice_001"
    assert payloads[0]["task_id"] == "fs_001"
    assert payloads[0]["reply"].startswith("第一轮继续救火")
    assert (
        "哪一座屋子" in payloads[0]["reply"]
        or "哪一边出事了" in payloads[0]["reply"]
        or "从哪一块传来的" in payloads[0]["reply"]
    )
    assert payloads[0]["phase6_submit"]["response"]["session"]["current_task_id"] == "fs_002"
    assert payloads[1]["session_id"] == "ses_voice_001"
    assert payloads[1]["task_id"] == "fs_002"
    assert payloads[1]["phase6_submit"]["response"]["session"]["status"] == "ended"
    assert payloads[1]["session_memory_summary"] is not None
    assert payloads[1]["session_memory_summary"].startswith("任务：接警判断")
    assert "哪些能动" in stream_calls[0]["text"] or "画在墙上" in stream_calls[0]["text"]
    assert bridge_calls["count"] == 2
    assert asr_calls["count"] == 2
    assert phase6_client_instances and phase6_client_instances[0].submissions == 2
    assert keep_trying_retry_timeouts_seen == [0.0, 0.0]
    assert len(stream_calls) == 3
    assert all(call["provider_mode"] == "qwen" for call in stream_calls)
    assert all(call["playback_gain"] == pytest.approx(0.6) for call in stream_calls)
    assert len(playback_calls) == 0


def test_signal_resolver_tolerates_common_asr_homophone_for_miehuo() -> None:
    resolver = RuleFirstSignalResolver()
    task_context = TaskContext(
        task_id="fs_004",
        task_name="消防车出动",
        task_goal="让孩子说出消防车要去做什么",
        expected_child_action="说出消防车要去救火",
        completion_points=(CompletionPoint.parse("救火:救火,灭火"),),
    )

    signal_resolution = resolver.resolve(
        child_input_text="消防车去灭货",
        current_task=task_context,
    )

    assert signal_resolution.task_signal == "task_completed"
    assert "救火" in signal_resolution.matched_completion_points


@pytest.mark.parametrize(
    ("child_text", "task_context"),
    (
        (
            "火在外头",
            TaskContext(
                task_id="fs_002",
                task_name="接警判断",
                task_goal="在指挥台确认这次求助来自哪里",
                expected_child_action="说出内部火警或外部场景火警",
                completion_points=(CompletionPoint.parse("接警地点:内部,外部"),),
            ),
        ),
        (
            "先派飞机过去",
            TaskContext(
                task_id="fs_003",
                task_name="集合出动",
                task_goal="让消防员集合，并决定消防车还是直升机先出发",
                expected_child_action="完成角色和载具选择",
                completion_points=(CompletionPoint.parse("集合出动:消防车,直升机"),),
            ),
        ),
        (
            "火在床头那边，火不大",
            TaskContext(
                task_id="fs_004",
                task_name="火源判断",
                task_goal="识别这次是小火源还是大火源，以及火源位置",
                expected_child_action="根据火源大小/位置选择处理策略",
                completion_points=(CompletionPoint.parse("火源位置:小火,床边"),),
                completion_match_mode="all",
            ),
        ),
        (
            "这是大活呀",
            TaskContext(
                task_id="fs_004",
                task_name="火源判断",
                task_goal="识别这次是小火源还是大火源，以及火源位置",
                expected_child_action="根据火源大小/位置选择处理策略",
                completion_points=(CompletionPoint.parse("火源大小:大火,小火,中火"),),
                completion_match_mode="any",
            ),
        ),
        (
            "火小",
            TaskContext(
                task_id="fs_004",
                task_name="火源判断",
                task_goal="识别这次是小火源还是大火源，以及火源位置",
                expected_child_action="根据火源大小/位置选择处理策略",
                completion_points=(CompletionPoint.parse("火源大小:大火,小火,中火"),),
                completion_match_mode="any",
            ),
        ),
        (
            "这是中活",
            TaskContext(
                task_id="fs_004",
                task_name="火源判断",
                task_goal="识别这次是小火源还是大火源，以及火源位置",
                expected_child_action="根据火源大小/位置选择处理策略",
                completion_points=(CompletionPoint.parse("火源大小:大火,小火,中火"),),
                completion_match_mode="any",
            ),
        ),
    ),
)
def test_signal_resolver_accepts_common_oral_synonyms_and_homophones(
    child_text: str,
    task_context: TaskContext,
) -> None:
    resolver = RuleFirstSignalResolver()

    signal_resolution = resolver.resolve(
        child_input_text=child_text,
        current_task=task_context,
    )

    assert signal_resolution.task_signal == "task_completed"


def test_fs004_live_config_allows_size_only_judgment_to_complete() -> None:
    module = load_script_module("run_voice_fast.py")
    completion_points = module._build_completion_points("fs_004")
    resolver = RuleFirstSignalResolver()
    task_context = TaskContext(
        task_id="fs_004",
        task_name="火源判断",
        task_goal="识别这次是小火源还是大火源，以及火源位置",
        expected_child_action="根据火源大小/位置选择处理策略",
        completion_points=completion_points,
        completion_match_mode="any",
    )

    signal_resolution = resolver.resolve(
        child_input_text="我觉得火大",
        current_task=task_context,
    )

    assert tuple(point.label for point in completion_points) == ("火源大小", "火源位置")
    assert signal_resolution.task_signal == "task_completed"
    assert signal_resolution.matched_completion_points == ("火源大小",)
