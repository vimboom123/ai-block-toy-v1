#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from input_understanding import (  # noqa: E402
    CompletionPoint,
    MinimalInteractionGenerator,
    TaskContext,
)
from phase6_bridge import (  # noqa: E402
    Phase6BridgeError,
    Phase6SessionClient,
)
from runtime_pipeline import run_phase7_turn_pipeline  # noqa: E402


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Phase 7 text-only prototype for signal resolution and interaction generation.",
    )
    parser.add_argument("--child-text", required=True, help="Latest child utterance in text form.")
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
        "--interaction-provider",
        choices=("qwen", "minimax", "ark_doubao", "template", "auto"),
        default="qwen",
        help="Natural reply provider. Default is qwen; you can also switch to minimax, ark_doubao, template, or auto.",
    )
    parser.add_argument(
        "--provider-fast-timeout-seconds",
        type=positive_float,
        default=MinimalInteractionGenerator.DEFAULT_FAST_PATH_TIMEOUT_SECONDS,
        help="Fast-path timeout for task_completed / end_session provider attempts.",
    )
    parser.add_argument(
        "--provider-keep-trying-timeout-seconds",
        type=positive_float,
        default=MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_TIMEOUT_SECONDS,
        help="First provider timeout for keep_trying.",
    )
    parser.add_argument(
        "--provider-keep-trying-retry-timeout-seconds",
        type=non_negative_float,
        default=MinimalInteractionGenerator.DEFAULT_KEEP_TRYING_RETRY_TIMEOUT_SECONDS,
        help="Optional second provider timeout for keep_trying retry. Set to 0 to disable the retry.",
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
    return parser


def parse_completion_points(raw_specs: list[str]) -> tuple[CompletionPoint, ...]:
    return tuple(CompletionPoint.parse(raw_spec) for raw_spec in raw_specs)


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

    bridge_package = run_phase7_turn_pipeline(
        child_input_text=args.child_text,
        current_task=current_task,
        interaction_provider=args.interaction_provider,
        provider_fast_timeout_seconds=args.provider_fast_timeout_seconds,
        provider_keep_trying_timeout_seconds=args.provider_keep_trying_timeout_seconds,
        provider_keep_trying_retry_timeout_seconds=args.provider_keep_trying_retry_timeout_seconds,
        session_id=args.session_id,
    )

    output_payload = bridge_package.to_dict()
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
            print(json.dumps(output_payload, ensure_ascii=False, indent=2))
            return 1

    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
