from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any


class SceneLoadError(RuntimeError):
    """Raised when the scene pack cannot be parsed."""


def _load_with_python(path: Path) -> dict[str, Any] | list[Any]:
    import yaml  # type: ignore[import-not-found]

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _load_with_ruby(path: Path) -> dict[str, Any] | list[Any]:
    ruby_script = """
require "json"
require "yaml"
raw = File.read(ARGV[0], encoding: "UTF-8")
data = YAML.safe_load(raw, permitted_classes: [], aliases: false)
print JSON.generate(data)
""".strip()

    try:
        result = subprocess.run(
            ["ruby", "-e", ruby_script, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SceneLoadError(
            "Scene loader needs either PyYAML in Python or the system Ruby YAML runtime."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or "unknown Ruby YAML error"
        raise SceneLoadError(f"Failed to parse YAML scene pack: {stderr}") from exc

    return json.loads(result.stdout)


def load_scene_pack(path: Path) -> dict[str, Any]:
    loader = _load_with_python if importlib.util.find_spec("yaml") else _load_with_ruby
    loaded = loader(path)
    if not isinstance(loaded, dict):
        raise SceneLoadError(f"Scene pack must decode to a mapping: {path}")
    return loaded


def get_candidate_task(scene_pack: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = scene_pack.get("candidate_tasks") or []
    for task in tasks:
        if task.get("task_id") == task_id:
            return task
    available = ", ".join(task.get("task_id", "<missing>") for task in tasks) or "<none>"
    raise SceneLoadError(f"Unknown task_id '{task_id}'. Available tasks: {available}")
