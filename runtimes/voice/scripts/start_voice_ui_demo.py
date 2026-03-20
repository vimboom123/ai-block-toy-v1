#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT_DIR = Path(__file__).resolve().parents[1]
SESSION_RUNTIME_ROOT_DIR = ROOT_DIR.parent / "session"
VOICE_FAST_SCRIPT_PATH = ROOT_DIR / "scripts" / "run_voice_fast.py"

DEFAULT_PHASE6_PORT = 4183
DEFAULT_PHASE6_START_TIMEOUT_SECONDS = 15.0
DEFAULT_UI_URL_TEMPLATE = "http://127.0.0.1:{port}/"
DEFAULT_PHASE6_API_BASE_TEMPLATE = "http://127.0.0.1:{port}/api/session-runtime"
REQUIRED_STATE_MACHINE_VERSION = "ai_block_toy_state_machine_v1"


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def _phase6_health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/health"


def _phase6_ui_url(port: int) -> str:
    return DEFAULT_UI_URL_TEMPLATE.format(port=port)


def _phase6_api_base(port: int) -> str:
    return DEFAULT_PHASE6_API_BASE_TEMPLATE.format(port=port)


def _phase6_log_path(port: int) -> Path:
    return SESSION_RUNTIME_ROOT_DIR / "state" / f"voice-ui-demo-phase6-{port}.log"


def _has_required_state_machine_health(health_payload: dict[str, Any] | None) -> bool:
    if not isinstance(health_payload, dict):
        return False
    return health_payload.get("state_machine_version") == REQUIRED_STATE_MACHINE_VERSION


def _require_state_machine_health(health_payload: dict[str, Any], *, port: int) -> None:
    version = health_payload.get("state_machine_version")
    if version == REQUIRED_STATE_MACHINE_VERSION:
        return
    raise RuntimeError(
        "Phase 6 server on port "
        f"{port} is not exposing the required state machine version "
        f"({REQUIRED_STATE_MACHINE_VERSION}). "
        "Please restart 4183 or launch the demo on a fresh port."
    )


def _find_listening_pids(port: int) -> list[int]:
    completed = subprocess.run(
        [
            "lsof",
            "-nP",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        return []
    pids: list[int] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _stop_listening_processes_on_port(port: int, *, timeout_seconds: float = 5.0) -> list[int]:
    pids = _find_listening_pids(port)
    if not pids:
        return []

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = _find_listening_pids(port)
        if not remaining:
            return pids
        time.sleep(0.2)

    remaining = _find_listening_pids(port)
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue

    kill_deadline = time.monotonic() + 2.0
    while time.monotonic() < kill_deadline:
        if not _find_listening_pids(port):
            return pids
        time.sleep(0.1)
    return pids


def _probe_phase6_server(port: int, *, timeout_seconds: float = 1.0) -> dict[str, Any] | None:
    try:
        with request.urlopen(_phase6_health_url(port), timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _start_phase6_server(port: int) -> tuple[subprocess.Popen[Any], Path, Any]:
    log_path = _phase6_log_path(port)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "session_runtime.server", "--port", str(port)],
        cwd=SESSION_RUNTIME_ROOT_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_path, log_file


def _wait_for_phase6_server(
    port: int,
    *,
    timeout_seconds: float,
    process: subprocess.Popen[Any] | None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_payload = _probe_phase6_server(port, timeout_seconds=1.0)
        if last_payload is not None and last_payload.get("ok") is True:
            return last_payload
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Phase 6 server exited early with code {process.returncode}")
        time.sleep(0.25)
    raise RuntimeError(
        f"Phase 6 server on port {port} did not become healthy within {timeout_seconds:g}s"
    )


def _ensure_state_machine_phase6_server(
    *,
    port: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], subprocess.Popen[Any] | None, Any | None, bool]:
    health_payload = _probe_phase6_server(port, timeout_seconds=1.0)
    phase6_process: subprocess.Popen[Any] | None = None
    phase6_log_file = None
    phase6_started_here = False

    if health_payload is None:
        phase6_process, phase6_log_path, phase6_log_file = _start_phase6_server(port)
        phase6_started_here = True
        print(
            f"[launcher] Phase 6 不在线，已尝试拉起 {port}（日志：{phase6_log_path}）",
            file=sys.stderr,
        )
        health_payload = _wait_for_phase6_server(
            port,
            timeout_seconds=timeout_seconds,
            process=phase6_process,
        )
        _require_state_machine_health(health_payload, port=port)
        return health_payload, phase6_process, phase6_log_file, phase6_started_here

    if _has_required_state_machine_health(health_payload):
        print(
            f"[launcher] 复用已运行的 Phase 6：{_phase6_ui_url(port)}",
            file=sys.stderr,
        )
        return health_payload, phase6_process, phase6_log_file, phase6_started_here

    print(
        f"[launcher] 发现旧版 Phase 6（port={port}），正在替换成状态机版服务...",
        file=sys.stderr,
    )
    stopped_pids = _stop_listening_processes_on_port(port)
    if not stopped_pids:
        raise RuntimeError(
            f"Phase 6 server on port {port} is stale and could not be replaced automatically."
        )
    phase6_process, phase6_log_path, phase6_log_file = _start_phase6_server(port)
    phase6_started_here = True
    print(
        f"[launcher] 已替换旧版服务，重启 {port}（日志：{phase6_log_path}）",
        file=sys.stderr,
    )
    health_payload = _wait_for_phase6_server(
        port,
        timeout_seconds=timeout_seconds,
        process=phase6_process,
    )
    _require_state_machine_health(health_payload, port=port)
    return health_payload, phase6_process, phase6_log_file, phase6_started_here


def _build_voice_fast_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(VOICE_FAST_SCRIPT_PATH),
        "--record-seconds",
        str(args.record_seconds),
        "--max-turns",
        str(args.max_turns),
        "--task-id",
        "fs_001",
        "--task-name",
        "场景识别",
        "--task-goal",
        "说出哪些是能动的，哪些只是画在墙上的",
        "--expected-child-action",
        "区分可操作元素与背景元素",
        "--phase6-api-base",
        _phase6_api_base(args.phase6_port),
        "--submit-phase6",
        "--tts-provider",
        args.tts_provider,
        "--interaction-provider",
        args.interaction_provider,
        "--provider-fast-timeout-seconds",
        str(args.provider_fast_timeout_seconds),
        "--provider-keep-trying-timeout-seconds",
        str(args.provider_keep_trying_timeout_seconds),
        "--provider-keep-trying-retry-timeout-seconds",
        str(args.provider_keep_trying_retry_timeout_seconds),
    ]
    if args.record_device is not None:
        command.extend(["--record-device", str(args.record_device)])
    if args.playback_gain != 0.6:
        command.extend(["--playback-gain", str(args.playback_gain)])
    if args.no_playback:
        command.append("--no-playback")
    if args.stream_tts:
        command.append("--stream-tts")
    else:
        command.append("--no-stream-tts")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一键启动 Fire Station 家长端 UI + 真实麦克风 fast 语音链路"
    )
    parser.add_argument("--phase6-port", type=positive_int, default=DEFAULT_PHASE6_PORT)
    parser.add_argument("--record-seconds", type=positive_float, default=20.0)
    parser.add_argument("--max-turns", type=positive_int, default=12)
    parser.add_argument("--record-device", default=None)
    parser.add_argument("--playback-gain", type=float, default=0.6)
    parser.add_argument(
        "--interaction-provider",
        default="qwen",
        choices=("qwen", "minimax", "ark_doubao", "template", "auto"),
    )
    parser.add_argument(
        "--tts-provider",
        default="qwen",
        choices=("auto", "qwen", "say", "none"),
    )
    parser.add_argument(
        "--provider-fast-timeout-seconds",
        type=float,
        default=0.0,
        help="默认 0，表示 fast path 不限时。",
    )
    parser.add_argument(
        "--provider-keep-trying-timeout-seconds",
        type=float,
        default=0.0,
        help="默认 0，表示 keep_trying 不限时。",
    )
    parser.add_argument(
        "--provider-keep-trying-retry-timeout-seconds",
        type=float,
        default=0.0,
        help="默认 0，表示不做 keep_trying retry。",
    )
    parser.add_argument(
        "--phase6-start-timeout-seconds",
        type=positive_float,
        default=DEFAULT_PHASE6_START_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        help="启动后自动打开家长端 UI。",
    )
    parser.add_argument(
        "--no-open-browser",
        dest="open_browser",
        action="store_false",
        help="只打印 UI 链接，不自动打开浏览器。",
    )
    parser.set_defaults(open_browser=True)
    parser.add_argument(
        "--stream-tts",
        dest="stream_tts",
        action="store_true",
        help="默认开启流式千问 TTS。",
    )
    parser.add_argument(
        "--no-stream-tts",
        dest="stream_tts",
        action="store_false",
        help="关闭流式 TTS，退回整段生成后再播。",
    )
    parser.set_defaults(stream_tts=True)
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument(
        "--shutdown-started-phase6-on-exit",
        action="store_true",
        help="如果是脚本帮你拉起的 Phase 6，voice loop 结束时顺手关掉它。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    (
        health_payload,
        phase6_process,
        phase6_log_file,
        phase6_started_here,
    ) = _ensure_state_machine_phase6_server(
        port=args.phase6_port,
        timeout_seconds=args.phase6_start_timeout_seconds,
    )

    ui_url = _phase6_ui_url(args.phase6_port)
    print(f"[launcher] 家长端 UI：{ui_url}", file=sys.stderr)
    if health_payload is not None:
        state_machine_version = health_payload.get("state_machine_version")
        if state_machine_version:
            print(
                f"[launcher] state_machine_version={state_machine_version}",
                file=sys.stderr,
            )
        latest_session_id = health_payload.get("latest_session_id")
        if latest_session_id:
            print(f"[launcher] latest_session_id={latest_session_id}", file=sys.stderr)
    if args.open_browser:
        webbrowser.open(ui_url, new=1)

    command = _build_voice_fast_command(args)
    print(f"[launcher] voice 命令：{' '.join(command)}", file=sys.stderr)

    try:
        completed = subprocess.run(
            command,
            cwd=ROOT_DIR,
        )
        return int(completed.returncode)
    finally:
        if args.shutdown_started_phase6_on_exit and phase6_started_here and phase6_process is not None:
            phase6_process.terminate()
            try:
                phase6_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                phase6_process.kill()
                phase6_process.wait(timeout=5)
        if phase6_log_file is not None:
            phase6_log_file.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[launcher] 已中断。", file=sys.stderr)
        raise SystemExit(130)
