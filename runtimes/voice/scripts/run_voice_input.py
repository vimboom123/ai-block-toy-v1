#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from input_understanding import CompletionPoint, MinimalInteractionGenerator, TaskContext  # noqa: E402
from phase6_bridge import Phase6BridgeError, Phase6SessionClient  # noqa: E402
from runtime_pipeline import run_phase7_turn_pipeline  # noqa: E402
from voice_input import (  # noqa: E402
    AudioRecordingError,
    QwenRealtimeAsrError,
    QwenRealtimeAsrTranscriber,
    OneShotWavRecorder,
    SilenceAwareWavRecorder,
    WhisperCliError,
    WhisperCliTranscriber,
)
from voice_output import SpeechSynthesisError, synthesize_realtime_reply_audio, synthesize_reply_audio  # noqa: E402
from voice_realtime import AudioPlaybackError, play_audio_file  # noqa: E402


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_completion_points(raw_specs: list[str]) -> tuple[CompletionPoint, ...]:
    return tuple(CompletionPoint.parse(raw_spec) for raw_spec in raw_specs)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 0")
    return parsed


def default_record_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("/tmp") / f"ai-block-toy-phase7-recording-{timestamp}.wav"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Phase 7 single-turn voice prototype through realtime ASR/TTS or the legacy voice pipeline.",
    )
    audio_source_group = parser.add_mutually_exclusive_group(required=True)
    audio_source_group.add_argument("--audio-file", help="Path to a local audio file.")
    audio_source_group.add_argument(
        "--record-seconds",
        type=positive_float,
        help="Record up to N seconds; realtime mode may stop earlier once speech ends.",
    )
    parser.add_argument("--task-id", default="task_demo", help="Current task id.")
    parser.add_argument("--task-name", default="当前任务", help="Current task name.")
    parser.add_argument("--task-goal", required=True, help="Current task goal.")
    parser.add_argument(
        "--expected-child-action",
        required=True,
        help="What the child is expected to say or do for this task.",
    )
    parser.add_argument(
        "--completion-point",
        action="append",
        default=[],
        help="Completion point spec. Format: 'label:kw1,kw2' or plain 'keyword'. Repeatable.",
    )
    parser.add_argument(
        "--completion-match-mode",
        choices=("any", "all"),
        default="any",
        help="Whether any completion point is enough, or all points are required.",
    )
    parser.add_argument("--scene-context", default=None, help="Optional short scene context.")
    parser.add_argument(
        "--scene-style",
        default="playful_companion",
        help="Optional scene style label used by the interaction generator.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("realtime", "legacy"),
        default="realtime",
        help="Voice runtime mode. realtime uses Qwen realtime WebSocket ASR/TTS; legacy keeps whisper + HTTP TTS.",
    )
    parser.add_argument(
        "--interaction-provider",
        choices=("qwen", "minimax", "ark_doubao", "template", "auto"),
        default="qwen",
        help="Natural reply provider. Default is qwen; you can also switch to minimax, ark_doubao, template, or auto.",
    )
    parser.add_argument(
        "--provider-fast-timeout-seconds",
        type=non_negative_float,
        default=MinimalInteractionGenerator.DEFAULT_FAST_PATH_TIMEOUT_SECONDS,
        help="Fast-path timeout for task_completed / end_session provider attempts. Set to 0 to disable the timeout.",
    )
    parser.add_argument(
        "--provider-keep-trying-timeout-seconds",
        type=non_negative_float,
        default=MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS,
        help="First provider timeout for keep_trying. Set to 0 to disable the timeout.",
    )
    parser.add_argument(
        "--provider-keep-trying-retry-timeout-seconds",
        type=non_negative_float,
        default=MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS,
        help="Optional second provider timeout for keep_trying retry. Set to 0 to disable the retry.",
    )
    parser.add_argument(
        "--whisper-command",
        default="whisper",
        help="Whisper CLI command or absolute path.",
    )
    parser.add_argument(
        "--record-output-file",
        default=None,
        help="Output wav path for --record-seconds. Defaults to /tmp/ai-block-toy-phase7-recording-<timestamp>.wav.",
    )
    parser.add_argument(
        "--record-sample-rate",
        type=positive_int,
        default=OneShotWavRecorder.DEFAULT_SAMPLE_RATE,
        help="Sample rate for one-shot mic recording.",
    )
    parser.add_argument(
        "--record-channels",
        type=positive_int,
        default=OneShotWavRecorder.DEFAULT_CHANNELS,
        help="Channel count for one-shot mic recording.",
    )
    parser.add_argument(
        "--record-device",
        default=None,
        help="Optional sounddevice input device name or index.",
    )
    parser.add_argument(
        "--whisper-model",
        default=WhisperCliTranscriber.DEFAULT_MODEL,
        help="Whisper model name. Default uses the locally cached large-v3-turbo path on this machine.",
    )
    parser.add_argument(
        "--whisper-language",
        default="zh",
        help="Language hint for legacy whisper, and also the fallback ASR language when --realtime-asr-language is not set.",
    )
    parser.add_argument(
        "--realtime-asr-model",
        default=None,
        help="Optional Qwen realtime ASR model override when --runtime-mode realtime.",
    )
    parser.add_argument(
        "--realtime-asr-language",
        default=None,
        help="Optional Qwen realtime ASR language override when --runtime-mode realtime.",
    )
    parser.add_argument(
        "--realtime-asr-timeout-seconds",
        type=positive_float,
        default=None,
        help="Optional timeout for Qwen realtime ASR websocket calls.",
    )
    parser.add_argument(
        "--whisper-device",
        default="cpu",
        help="Inference device passed to whisper CLI.",
    )
    parser.add_argument(
        "--whisper-task",
        choices=("transcribe", "translate"),
        default="transcribe",
        help="Whisper task mode.",
    )
    parser.add_argument(
        "--whisper-threads",
        type=int,
        default=None,
        help="Optional CPU thread count for whisper CLI.",
    )
    parser.add_argument("--session-id", default=None, help="Optional Phase 6 session id.")
    parser.add_argument(
        "--phase6-api-base",
        default=None,
        help="Optional Phase 6 API base, for example http://127.0.0.1:4183/api/session-runtime",
    )
    parser.add_argument(
        "--submit-phase6",
        action="store_true",
        help="If set, submit the bridge payload to a running Phase 6 server.",
    )
    parser.add_argument(
        "--tts-provider",
        choices=("auto", "qwen", "say", "none"),
        default="auto",
        help="Reply audio provider. Default auto tries Qwen TTS first, then macOS say fallback.",
    )
    parser.add_argument(
        "--tts-output-file",
        default=None,
        help="Optional output audio path for the reply TTS file.",
    )
    parser.add_argument(
        "--no-playback",
        action="store_true",
        help="Do not play synthesized reply audio out loud.",
    )
    parser.add_argument(
        "--playback-device",
        default=None,
        help="Optional sounddevice playback device for synthesized reply audio.",
    )
    parser.add_argument(
        "--tts-timeout-seconds",
        type=positive_float,
        default=None,
        help="Optional timeout for Qwen TTS requests.",
    )
    parser.add_argument(
        "--qwen-tts-model",
        default=None,
        help="Optional Qwen TTS model override. Otherwise read env or use the provider default.",
    )
    parser.add_argument(
        "--qwen-tts-voice",
        default=None,
        help="Optional Qwen TTS voice override. Otherwise read env or use the provider default.",
    )
    parser.add_argument(
        "--qwen-tts-format",
        default=None,
        help="Optional Qwen TTS audio format override, for example wav or mp3.",
    )
    parser.add_argument(
        "--say-voice",
        default=None,
        help="Optional macOS say voice name used by --tts-provider say or auto fallback.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.submit_phase6 and (not args.phase6_api_base or not args.session_id):
        parser.error("--submit-phase6 requires both --phase6-api-base and --session-id")

    current_task = TaskContext(
        task_id=args.task_id,
        task_name=args.task_name,
        task_goal=args.task_goal,
        expected_child_action=args.expected_child_action,
        completion_points=parse_completion_points(args.completion_point),
        completion_match_mode=args.completion_match_mode,
        scene_context=args.scene_context,
        scene_style=args.scene_style,
    )

    audio_path = args.audio_file
    recorded_clip = None
    if args.record_seconds is not None:
        record_output_path = Path(args.record_output_file) if args.record_output_file else default_record_output_path()
        recorder_cls = SilenceAwareWavRecorder if args.runtime_mode == "realtime" else OneShotWavRecorder
        recorder = recorder_cls(
            sample_rate=args.record_sample_rate,
            channels=args.record_channels,
            device=args.record_device,
        )
        print(
            f"[phase7] recording {args.record_seconds:.2f}s -> {record_output_path}",
            file=sys.stderr,
        )
        try:
            recorded_clip = recorder.record(
                seconds=args.record_seconds,
                output_path=record_output_path,
            )
        except AudioRecordingError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "audio_recording",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        audio_path = recorded_clip.audio_path

    if args.runtime_mode == "realtime":
        transcriber = QwenRealtimeAsrTranscriber(
            model=args.realtime_asr_model,
            language=(
                args.realtime_asr_language
                if args.realtime_asr_language is not None
                else (args.whisper_language or None)
            ),
            request_timeout_seconds=args.realtime_asr_timeout_seconds,
        )
    else:
        transcriber = WhisperCliTranscriber(
            command=args.whisper_command,
            model=args.whisper_model,
            language=args.whisper_language or None,
            device=args.whisper_device,
            task=args.whisper_task,
            threads=args.whisper_threads,
        )

    try:
        audio_transcription = transcriber.transcribe(audio_path)
    except (WhisperCliError, QwenRealtimeAsrError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "audio_transcription",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    bridge_package = run_phase7_turn_pipeline(
        child_input_text=audio_transcription.transcript,
        current_task=current_task,
        interaction_provider=args.interaction_provider,
        provider_fast_timeout_seconds=args.provider_fast_timeout_seconds,
        provider_keep_trying_timeout_seconds=args.provider_keep_trying_timeout_seconds,
        provider_keep_trying_retry_timeout_seconds=args.provider_keep_trying_retry_timeout_seconds,
        session_id=args.session_id,
    )

    output_payload = {
        "runtime_mode": args.runtime_mode,
        "audio_transcription": audio_transcription.to_dict(),
        **bridge_package.to_dict(),
    }
    if recorded_clip is not None:
        output_payload["audio_recording"] = recorded_clip.to_dict()
    exit_code = 0
    if args.submit_phase6:
        try:
            client = Phase6SessionClient(args.phase6_api_base)
            phase6_response = client.submit_turn(
                session_id=args.session_id,
                payload=bridge_package.phase6_turn_payload,
            )
            output_payload["phase6_submit"] = {
                "ok": True,
                "api_base": args.phase6_api_base,
                "response": phase6_response,
            }
        except Phase6BridgeError as exc:
            output_payload["phase6_submit"] = {
                "ok": False,
                "api_base": args.phase6_api_base,
                "error": str(exc),
            }
            exit_code = 1

    if args.tts_provider != "none":
        try:
            reply_ready_at = time.monotonic()
            if args.runtime_mode == "realtime":
                synthesized_speech = synthesize_realtime_reply_audio(
                    text=bridge_package.interaction_generation.reply_text,
                    provider_mode=args.tts_provider,
                    output_path=args.tts_output_file,
                    qwen_model=args.qwen_tts_model,
                    qwen_voice=args.qwen_tts_voice,
                    qwen_audio_format=args.qwen_tts_format,
                    tts_timeout_seconds=args.tts_timeout_seconds,
                    say_voice=args.say_voice,
                )
            else:
                synthesized_speech = synthesize_reply_audio(
                    text=bridge_package.interaction_generation.reply_text,
                    provider_mode=args.tts_provider,
                    output_path=args.tts_output_file,
                    qwen_model=args.qwen_tts_model,
                    qwen_voice=args.qwen_tts_voice,
                    qwen_audio_format=args.qwen_tts_format,
                    tts_timeout_seconds=args.tts_timeout_seconds,
                    say_voice=args.say_voice,
                )
            if synthesized_speech is not None:
                output_payload["tts_output"] = {
                    "ok": True,
                    **synthesized_speech.to_dict(),
                }
                if not args.no_playback:
                    try:
                        print(
                            f"  [播报间隔] {(time.monotonic() - reply_ready_at) * 1000:.0f}ms -> 开始播放",
                            file=sys.stderr,
                        )
                        play_audio_file(synthesized_speech.audio_path, device=args.playback_device)
                        output_payload["tts_output"]["playback_ok"] = True
                    except AudioPlaybackError as exc:
                        output_payload["tts_output"]["playback_ok"] = False
                        output_payload["tts_output"]["playback_error"] = str(exc)
                        exit_code = 1
        except SpeechSynthesisError as exc:
            output_payload["tts_output"] = {
                "ok": False,
                "requested_provider": args.tts_provider,
                "text": bridge_package.interaction_generation.reply_text,
                "error": str(exc),
            }

    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
