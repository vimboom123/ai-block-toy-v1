#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT_DIR.parents[1]
STATE_ROOT = PROJECT_ROOT.parents[3]
XIAOZHI_SERVER_ROOT = STATE_ROOT / "workspace" / "tmp" / "xiaozhi-esp32-server" / "main" / "xiaozhi-server"
CONFIG_PATH = XIAOZHI_SERVER_ROOT / "data" / ".config.yaml"
SESSION_STORE_PATH = ROOT_DIR / "state" / "xiaozhi-phase6-session-store.json"


def _ip_from_interface(interface: str) -> str:
    try:
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _default_interface() -> str:
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    for line in result.stdout.splitlines():
        if "interface:" not in line:
            continue
        return line.split("interface:", 1)[1].strip()
    return ""


def _local_ip() -> str:
    configured_ip = (os.environ.get("AI_BLOCK_TOY_LOCAL_IP") or "").strip()
    if configured_ip:
        return configured_ip
    if sys.platform == "darwin":
        default_interface = _default_interface()
        if default_interface:
            value = _ip_from_interface(default_interface)
            if value:
                return value
        for interface in ("en0", "en1"):
            value = _ip_from_interface(interface)
            if value:
                return value
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def _first_env(*keys: str) -> str:
    for key in keys:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _interaction_provider_from_env() -> str:
    provider = (os.environ.get("AI_BLOCK_TOY_INTERACTION_PROVIDER") or "").strip().lower()
    if provider in {"qwen", "ark_doubao", "minimax", "template", "auto"}:
        return provider
    return "qwen"


def _qwen_tts_voice_from_env() -> str:
    return (
        (os.environ.get("AI_BLOCK_TOY_QWEN_TTS_VOICE") or "").strip()
        or (os.environ.get("QWEN_RT_TTS_VOICE") or "").strip()
        or "Cherry"
    )


def _device_screen_mode_from_env() -> str:
    mode = (os.environ.get("AI_BLOCK_TOY_DEVICE_SCREEN_MODE") or "").strip().lower()
    if mode in {"text", "emoji_only"}:
        return mode
    return "emoji_only"


def _vad_silence_ms_from_env() -> int:
    raw_value = (os.environ.get("AI_BLOCK_TOY_VAD_SILENCE_MS") or "").strip()
    if raw_value:
        return max(int(raw_value), 200)
    return 1200


def _provider_timeout_triplet(interaction_provider: str) -> tuple[float, float, float]:
    if interaction_provider == "qwen":
        return (12.0, 12.0, 18.0)
    return (1.8, 1.2, 0.0)


def _yaml_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _build_config_yaml(
    *,
    local_ip: str,
    websocket_port: int,
    http_port: int,
    dashscope_api_key: str,
    interaction_provider: str,
    qwen_tts_voice: str,
) -> str:
    provider_fast_timeout_seconds, provider_keep_trying_timeout_seconds, provider_keep_trying_retry_timeout_seconds = (
        _provider_timeout_triplet(interaction_provider)
    )
    vad_silence_ms = _vad_silence_ms_from_env()
    websocket_url = f"ws://{local_ip}:{websocket_port}/xiaozhi/v1/"
    vision_url = f"http://{local_ip}:{http_port}/mcp/vision/explain"
    project_root = str(PROJECT_ROOT.resolve())
    session_store_path = str(SESSION_STORE_PATH.resolve())
    scene_context = (
        "消防站任务主线：先判断场景和火情，再决定谁出动，再去执行救援，"
        "最后做简短回顾。每次只推进当前最小一步。"
    )
    device_screen_mode = _device_screen_mode_from_env()
    return f"""server:
  ip: 0.0.0.0
  port: {websocket_port}
  http_port: {http_port}
  websocket: {_yaml_quote(websocket_url)}
  vision_explain: {_yaml_quote(vision_url)}
  auth:
    enabled: false

selected_module:
  VAD: SileroVAD
  ASR: QwenRealtimeBridgeASR
  LLM: AIBlockToyBridgeLLM
  VLLM: ChatGLMVLLM
  TTS: QwenRealtimeBridgeTTS
  Memory: nomem
  Intent: nointent

log:
  log_level: INFO
  log_dir: tmp
  log_file: server.log
  data_dir: data

device_screen_mode: {_yaml_quote(device_screen_mode)}

LLM:
  AIBlockToyBridgeLLM:
    type: ai_block_toy_bridge
    model_name: ai_block_toy_bridge
    project_root: {_yaml_quote(project_root)}
    session_store_path: {_yaml_quote(session_store_path)}
    interaction_provider: {_yaml_quote(interaction_provider)}
    scene_style: playful_companion
    scene_context: {_yaml_quote(scene_context)}
    provider_fast_timeout_seconds: {provider_fast_timeout_seconds}
    provider_keep_trying_timeout_seconds: {provider_keep_trying_timeout_seconds}
    provider_keep_trying_retry_timeout_seconds: {provider_keep_trying_retry_timeout_seconds}
    persist_state: true

ASR:
  QwenRealtimeBridgeASR:
    type: qwen_realtime_bridge_asr
    project_root: {_yaml_quote(project_root)}
    api_key: {_yaml_quote(dashscope_api_key)}
    model: 'qwen3-asr-flash-realtime'
    language: 'zh'
    timeout_seconds: 30
    output_dir: tmp/

VAD:
  SileroVAD:
    type: silero
    threshold: 0.5
    threshold_low: 0.25
    model_dir: models/snakers4_silero-vad
    min_silence_duration_ms: {vad_silence_ms}

TTS:
  QwenRealtimeBridgeTTS:
    type: qwen_realtime_bridge
    project_root: {_yaml_quote(project_root)}
    model: 'qwen3-tts-flash-realtime'
    voice: {_yaml_quote(qwen_tts_voice)}
    response_format: 'pcm'
    timeout_seconds: 12
    output_dir: tmp/
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write XiaoZhi bridge config and optionally start the bridge.")
    parser.add_argument("--websocket-port", type=int, default=8000)
    parser.add_argument("--http-port", type=int, default=8003)
    parser.add_argument(
        "--interaction-provider",
        choices=("qwen", "ark_doubao", "minimax", "template", "auto"),
        default=_interaction_provider_from_env(),
    )
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)

    dashscope_api_key = _first_env("DASHSCOPE_API_KEY", "QWEN_API_KEY", "DASHSCOPE_RT_API_KEY", "QWEN_RT_API_KEY")
    if not dashscope_api_key:
        raise SystemExit("Missing DashScope/Qwen API key in env.")

    local_ip = _local_ip()
    config_text = _build_config_yaml(
        local_ip=local_ip,
        websocket_port=args.websocket_port,
        http_port=args.http_port,
        dashscope_api_key=dashscope_api_key,
        interaction_provider=args.interaction_provider,
        qwen_tts_voice=_qwen_tts_voice_from_env(),
    )

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(config_text, encoding="utf-8")

    ota_url = f"http://{local_ip}:{args.http_port}/xiaozhi/ota/"
    websocket_url = f"ws://{local_ip}:{args.websocket_port}/xiaozhi/v1/"
    print(f"[bridge] config: {CONFIG_PATH}")
    print(f"[bridge] ota:    {ota_url}")
    print(f"[bridge] ws:     {websocket_url}")
    print(f"[bridge] phase7: {args.interaction_provider}")

    if args.print_only:
        return 0

    command = [sys.executable, "app.py"]
    return subprocess.call(command, cwd=str(XIAOZHI_SERVER_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
