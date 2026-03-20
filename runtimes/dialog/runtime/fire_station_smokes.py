from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.dialog_runtime import (
    DEFAULT_FIRE_STATION_SCENE_FILE,
    DEFAULT_FIRE_STATION_SMOKE_TASK_IDS,
    FireStationDialogRuntime,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Ark dialog smoke checks for the Fire Station scene."
    )
    parser.add_argument(
        "--scene-file",
        default=DEFAULT_FIRE_STATION_SCENE_FILE,
        help="Scene pack path relative to the project folder.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="Repeat to run specific tasks. Defaults to fs_002, fs_003, fs_004.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty JSON.",
    )
    return parser.parse_args()


def _resolved_task_ids(task_ids: list[str] | None) -> list[str]:
    if task_ids:
        return task_ids
    return list(DEFAULT_FIRE_STATION_SMOKE_TASK_IDS)


def main() -> int:
    args = parse_args()
    task_ids = _resolved_task_ids(args.task_ids)
    runtime = FireStationDialogRuntime(Path(__file__).resolve().parents[1], args.scene_file)
    results = [result.to_dict() for result in runtime.run_tasks(task_ids)]
    payload: list[dict[str, object]] | dict[str, object] = (
        results[0] if len(results) == 1 else results
    )
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )
    return 0 if all(result["error"] is None for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
