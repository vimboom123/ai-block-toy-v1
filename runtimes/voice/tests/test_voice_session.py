from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from input_understanding import CompletionPoint, TaskContext  # noqa: E402
from phase6_bridge.client import Phase6SessionClient  # noqa: E402
from voice_input import AudioTranscription, RecordedAudioClip  # noqa: E402
from voice_output import SynthesizedSpeech  # noqa: E402


def load_script_module(script_name: str):
    script_path = ROOT_DIR / "scripts" / script_name
    module_name = f"phase7_voice_{script_name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase6_session_client_can_create_and_read_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import phase6_bridge.client as client_module

    requests_seen: list[tuple[str, str, dict | None]] = []

    class FakeHeaders:
        def get_content_charset(self) -> str:
            return "utf-8"

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload
            self.headers = FakeHeaders()

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
            del exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        requests_seen.append((req.full_url, req.method, body))
        if req.method == "POST" and req.full_url.endswith("/sessions"):
            return FakeResponse(
                {
                    "ok": True,
                    "session": {
                        "session_id": "ses_voice_001",
                    },
                }
            )
        if req.method == "GET" and req.full_url.endswith("/sessions/ses_voice_001"):
            return FakeResponse(
                {
                    "ok": True,
                    "session": {
                        "session_id": "ses_voice_001",
                        "status": "active",
                    },
                }
            )
        raise AssertionError(f"unexpected request: {req.method} {req.full_url}")

    monkeypatch.setattr(client_module.request, "urlopen", fake_urlopen)

    client = Phase6SessionClient("http://127.0.0.1:4183/api/session-runtime")
    created = client.create_session(task_ids=["fs_004"])
    snapshot = client.get_session_snapshot("ses_voice_001")

    assert created["session"]["session_id"] == "ses_voice_001"
    assert snapshot["session"]["session_id"] == "ses_voice_001"
    assert requests_seen[0] == (
        "http://127.0.0.1:4183/api/session-runtime/sessions",
        "POST",
        {"task_ids": ["fs_004"]},
    )
    assert requests_seen[1] == (
        "http://127.0.0.1:4183/api/session-runtime/sessions/ses_voice_001",
        "GET",
        None,
    )


def test_run_voice_session_uses_terminal_declarative_reply_when_phase6_session_ends() -> None:
    script_module = load_script_module("run_voice_session.py")
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


def test_run_voice_session_uses_assistant_led_terminal_summary_when_phase6_auto_closes() -> None:
    script_module = load_script_module("run_voice_session.py")
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


def test_run_voice_session_script_overlaps_playback_and_syncs_phase6(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_voice_session.py")
    events: list[str] = []
    echo_cancel_calls: list[tuple[str, str]] = []
    transcription_paths: list[str] = []
    playback_started = threading.Event()
    release_playback = threading.Event()

    class FakeRecorder:
        DEFAULT_SAMPLE_RATE = 16000
        DEFAULT_CHANNELS = 1

        def __init__(self, *, sample_rate: int, channels: int, device: str | None = None) -> None:
            self.sample_rate = sample_rate
            self.channels = channels
            self.device = device
            self.calls = 0

        def record(self, *, seconds: float, output_path: str | Path) -> RecordedAudioClip:
            self.calls += 1
            output_path = Path(output_path).expanduser().resolve()
            output_path.write_bytes(b"fake-recording")
            events.append(f"record_{self.calls}:{output_path.name}")
            if self.calls == 1:
                assert playback_started.is_set(), "opening playback must start before the first recording window"
            if self.calls == 2:
                assert playback_started.is_set(), "playback must start before the next recording window"
                release_playback.set()
            return RecordedAudioClip(
                audio_path=str(output_path),
                input_mode="mic_record_once",
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_seconds=seconds,
                device=self.device,
            )

    class FakeTranscriber:
        def __init__(
            self,
            *,
            model: str | None,
            language: str | None,
            request_timeout_seconds: float | None,
        ) -> None:
            del model, language, request_timeout_seconds
            self.calls = 0

        def transcribe(self, audio_path: str | Path) -> AudioTranscription:
            transcription_paths.append(str(Path(audio_path).expanduser().resolve()))
            self.calls += 1
            transcript = "墙上的都是背景" if self.calls == 1 else "外面着火了"
            return AudioTranscription(
                audio_path=transcription_paths[-1],
                transcript=transcript,
                input_mode="audio_file",
                asr_source="qwen_asr_realtime",
                model_name="qwen3-asr-flash-realtime",
                language="zh",
                duration_seconds=1.0,
            )

    class FakeEchoCancellationResult:
        def __init__(self, *, processed_audio_path: Path, raw_audio_path: Path, reference_audio_path: Path) -> None:
            self.processed_audio_path = str(processed_audio_path)
            self.raw_audio_path = str(raw_audio_path)
            self.reference_audio_path = str(reference_audio_path)

        def to_dict(self) -> dict[str, object]:
            return {
                "raw_audio_path": self.raw_audio_path,
                "processed_audio_path": self.processed_audio_path,
                "reference_audio_path": self.reference_audio_path,
                "sample_rate": 16000,
                "applied": True,
                "delay_samples": 2400,
                "delay_seconds": 0.15,
                "gain": 0.6,
                "correlation_score": 0.92,
                "analysis_seconds": 1.25,
                "max_lag_seconds": 1.0,
                "method": "cross_correlation_subtraction",
            }

    def fake_cancel_playback_echo(
        raw_audio_path: str | Path,
        reference_audio_path: str | Path,
        *,
        output_path: str | Path | None = None,
        target_sample_rate: int = 16000,
        analysis_seconds: float = 1.25,
        max_lag_seconds: float = 1.0,
    ) -> FakeEchoCancellationResult:
        del target_sample_rate, analysis_seconds, max_lag_seconds
        raw_path = Path(raw_audio_path).expanduser().resolve()
        reference_path = Path(reference_audio_path).expanduser().resolve()
        echo_cancel_calls.append((raw_path.name, reference_path.name))
        processed_path = Path(output_path or raw_path.with_name(f"{raw_path.stem}-echo-cancel.wav")).expanduser().resolve()
        processed_path.write_bytes(b"fake-cleaned-audio")
        return FakeEchoCancellationResult(
            processed_audio_path=processed_path,
            raw_audio_path=raw_path,
            reference_audio_path=reference_path,
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
        del provider_mode, qwen_model, qwen_voice, qwen_audio_format, tts_timeout_seconds, say_voice
        resolved_output_path = Path(
            output_path or tmp_path / f"reply-{len(events) + 1}.wav"
        ).expanduser().resolve()
        resolved_output_path.write_bytes(b"fake-reply-audio")
        return SynthesizedSpeech(
            audio_path=str(resolved_output_path),
            text=text,
            input_mode="reply_text",
            provider_name="qwen_tts_realtime",
            model_name="qwen3-tts-flash-realtime",
            audio_format="wav",
        )

    def fake_play_audio_file(
        audio_path: str | Path,
        *,
        device: str | int | None = None,
        gain: float = 1.0,
    ) -> None:
        del device
        assert gain == pytest.approx(0.4)
        name = Path(audio_path).name
        playback_index = sum(1 for event in events if event.startswith("play_start_")) + 1
        events.append(f"play_start_{playback_index}:{name}")
        playback_started.set()
        if "opening" not in name and "turn-1" in name:
            release_playback.wait(timeout=2)
        events.append(f"play_end_{playback_index}:{name}")

    class FakePhase6Client:
        def __init__(self, api_base: str) -> None:
            self.api_base = api_base
            self.submissions = 0

        def create_session(self, task_ids=None):  # type: ignore[no-untyped-def]
            assert task_ids == ["fs_001", "fs_002", "fs_003", "fs_004", "fs_005", "fs_006"]
            events.append("phase6_create")
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

        def get_session_snapshot(self, session_id: str):  # type: ignore[no-untyped-def]
            del session_id
            events.append("phase6_get")
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
            del session_id, payload
            self.submissions += 1
            events.append(f"phase6_submit_{self.submissions}")
            if self.submissions == 1:
                session_status = "active"
                current_task_id = "fs_002"
                current_task = {
                    "task_id": "fs_002",
                    "name": "接警判断",
                    "goal": "在指挥台确认这次求助来自哪里",
                    "expected_child_action": "说出内部火警或外部场景火警",
                }
            else:
                session_status = "ended"
                current_task_id = None
                current_task = None
            return {
                "ok": True,
                "session": {
                    "session_id": "ses_voice_001",
                    "status": session_status,
                    "current_task_id": current_task_id,
                },
                "current_task": current_task,
            }

    monkeypatch.setattr(script_module, "SilenceAwareWavRecorder", FakeRecorder)
    monkeypatch.setattr(script_module, "QwenRealtimeAsrTranscriber", FakeTranscriber)
    monkeypatch.setattr(script_module, "cancel_playback_echo", fake_cancel_playback_echo)
    monkeypatch.setattr(script_module, "synthesize_realtime_reply_audio", fake_synthesize_realtime_reply_audio)
    monkeypatch.setattr(script_module, "play_audio_file", fake_play_audio_file)
    monkeypatch.setattr(script_module, "Phase6SessionClient", FakePhase6Client)
    monkeypatch.setattr(script_module.random, "choice", lambda seq: seq[0])
    monkeypatch.delenv("QWEN_MAX_TOKENS", raising=False)
    monkeypatch.delenv("DASHSCOPE_MAX_TOKENS", raising=False)

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
            "--completion-point",
            "背景可动:背景,可动,能动,会动,墙上,画在墙上,固定,不能动",
            "--runtime-mode",
            "realtime",
            "--interaction-provider",
            "template",
            "--phase6-api-base",
            "http://127.0.0.1:4183/api/session-runtime",
            "--submit-phase6",
            "--tts-provider",
            "auto",
            "--record-output-file",
            str(tmp_path / "record.wav"),
            "--tts-output-file",
            str(tmp_path / "reply.wav"),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "ses_voice_001"
    assert payload["turn_count"] == 2
    assert (
        "哪些能动" in payload["session_opening"]["text"]
        or "画在墙上" in payload["session_opening"]["text"]
    )
    assert payload["turns"][0]["signal_resolution"]["task_signal"] == "task_completed"
    assert payload["turns"][0]["next_current_task"]["task_id"] == "fs_002"
    assert (
        "哪一座屋子" in payload["turns"][0]["interaction_generation"]["reply_text"]
        or "哪一边出事了" in payload["turns"][0]["interaction_generation"]["reply_text"]
        or "从哪一块传来的" in payload["turns"][0]["interaction_generation"]["reply_text"]
    )
    assert payload["turns"][1]["signal_resolution"]["task_signal"] == "task_completed"
    assert payload["turns"][0]["session_memory_summary"] is None
    assert payload["turns"][1]["session_memory_summary"] is not None
    assert payload["turns"][1]["session_memory_summary"].startswith("任务：场景识别")
    assert payload["turns"][1]["session_memory_summary"].find("下一步：接警判断") != -1
    assert payload["turns"][1]["session_memory_summary"]
    assert payload["turns"][1]["interaction_context"]["session_memory"] == payload["turns"][1]["session_memory_summary"]
    assert payload["turns"][1]["session_memory_summary"].find(payload["turns"][0]["child_input_text"]) != -1
    assert payload["turns"][1]["session_memory_summary"].find(payload["turns"][0]["interaction_generation"]["reply_text"]) != -1
    assert payload["turns"][1]["audio_preparation"]["ok"] is True
    assert payload["turns"][1]["audio_preparation"]["applied"] is True
    assert payload["turns"][1]["audio_transcription"]["audio_path"].endswith("record-turn-2-echo-cancel.wav")
    assert payload["turns"][0]["tts_output"]["playback_ok"] is True
    assert payload["turns"][1]["tts_output"]["playback_ok"] is True
    assert "QWEN_MAX_TOKENS" not in script_module.os.environ
    assert "DASHSCOPE_MAX_TOKENS" not in script_module.os.environ
    assert events.index("play_start_1:reply-opening.wav") < events.index("record_1:record-turn-1.wav")
    assert events.index("play_start_2:reply-turn-1.wav") < events.index("record_2:record-turn-2.wav")
    assert events.index("play_end_2:reply-turn-1.wav") < events.index("record_2:record-turn-2.wav")
    assert echo_cancel_calls == [("record-turn-2.wav", "reply-turn-1.wav")]
    assert transcription_paths[0].endswith("record-turn-1.wav")
    assert transcription_paths[1].endswith("record-turn-2-echo-cancel.wav")
    assert "phase6_create" in events
    assert "phase6_submit_1" in events
    assert "phase6_submit_2" in events
