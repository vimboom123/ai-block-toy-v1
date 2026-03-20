from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

PHASE7_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "voice"
PHASE5_ROOT_DIR = Path(__file__).resolve().parents[3] / "runtimes" / "dialog"
if str(PHASE7_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE7_ROOT_DIR))
if str(PHASE5_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE5_ROOT_DIR))

from input_understanding.interaction_provider import (  # type: ignore[import-not-found]
    DEFAULT_QWEN_BASE_URL,
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    OpenAICompatibleConfigError,
    OpenAICompatibleRequestError,
)
from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]

JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
DEFAULT_PLANNER_QWEN_MODEL = "qwen-turbo"

DEFAULT_STAGE_ORDER = (
    ("fs_001", "场景识别"),
    ("fs_002", "接警判断"),
    ("fs_003", "集合出动"),
    ("fs_004", "火源判断"),
    ("fs_005", "救援执行"),
    ("fs_006", "回站总结"),
)

PHYSICAL_ASSETS = (
    {"name": "消防小人", "count": 2, "traits": ("可移动", "消防员", "可以执行救援动作")},
    {"name": "小火", "count": 2, "traits": ("可移动", "火情", "小范围火点")},
    {"name": "大火", "count": 1, "traits": ("可移动", "火情", "大范围火点")},
    {"name": "消防车", "count": 2, "traits": ("可移动", "出动车辆", "可以执行地面救援")},
    {"name": "上下双人床", "count": 1, "traits": ("可移动", "休息区家具", "可以作为火点附近场景线索")},
    {"name": "消防直升机", "count": 1, "traits": ("可移动", "空中救援", "里面已经有飞行员")},
    {"name": "消防指挥中心", "count": 1, "traits": ("可移动", "接警", "可以打电话和下达指令")},
    {"name": "消防门", "count": 1, "traits": ("只能上下移动", "门", "可以开合")},
    {"name": "铃铛", "count": 1, "traits": ("只能拨弄", "警报线索", "触发提醒")},
    {"name": "消防屋子", "count": 2, "traits": ("承载场景", "屋顶有停机坪", "内部包含消防门和主要积木")},
)

BACKGROUND_ASSETS = (
    {"name": "locker", "traits": ("背景", "屋内储物柜")},
    {"name": "gym", "traits": ("背景", "屋内健身区")},
    {"name": "消防栓", "traits": ("背景", "消防器材")},
    {"name": "灭火器", "traits": ("背景", "消防器材")},
    {"name": "路障", "count": 2, "traits": ("背景", "道路提醒")},
    {"name": "铲子", "traits": ("背景", "工具")},
    {"name": "斧头", "traits": ("背景", "工具")},
    {"name": "闹钟", "traits": ("背景", "时间线索")},
    {"name": "医药箱", "count": 2, "traits": ("背景", "急救物品")},
    {"name": "更衣室", "traits": ("背景", "另一个屋子内部空间")},
    {"name": "墙上的挂钟", "traits": ("背景", "二楼挂在墙上", "在铃铛旁边")},
)

STORY_SLOTS = (
    {
        "label": "停机坪接应任务",
        "location": "屋顶停机坪",
        "trigger_asset": "消防直升机",
        "entry_assets": ("消防直升机", "停机坪", "消防指挥中心"),
        "command_asset": "消防指挥中心",
        "lead_asset": "消防直升机",
        "helper_asset": "消防车",
        "support_asset": "消防指挥中心",
        "crew_asset": "两位消防小人",
        "environment": ("消防屋子", "墙上的挂钟", "停机坪"),
        "situation": "屋顶停机坪忽然来了新的接应任务，直升机准备落下，大家得先确认谁去屋顶接应、谁在下面待命。",
        "source_hint": "屋顶停机坪那边传来了新的接应动静",
        "judgment_focus": "先看直升机要落在哪、谁先去屋顶接应、谁在下面待命配合",
        "action_goal": "把消防小人带到停机坪边准备接应，再把消防车摆到下面待命位置，配合指挥中心把直升机接稳",
        "closing_result": "停机坪接应顺利完成",
    },
    {
        "label": "夜间集合任务",
        "location": "更衣室和上下双人床附近",
        "trigger_asset": "闹钟",
        "entry_assets": ("闹钟", "上下双人床", "locker"),
        "command_asset": "消防指挥中心",
        "lead_asset": "上下双人床",
        "helper_asset": "locker",
        "support_asset": "消防车",
        "crew_asset": "两位消防小人",
        "environment": ("更衣室", "locker", "gym"),
        "situation": "夜班提醒突然响起，双人床和 locker 附近还没整理好，两位消防小人要先集合准备值班。",
        "source_hint": "更衣室这边先响起来了",
        "judgment_focus": "先从哪一边集合、谁先去指挥中心报到、哪样装备要先拿",
        "action_goal": "把消防小人从床边带去集合，再把消防车摆到待命位置",
        "closing_result": "夜间集合顺利完成",
    },
    {
        "label": "门口通道清理任务",
        "location": "消防门内外",
        "trigger_asset": "消防门",
        "entry_assets": ("消防门", "路障", "消防车"),
        "command_asset": "消防指挥中心",
        "lead_asset": "消防门",
        "helper_asset": "路障",
        "support_asset": "消防车",
        "crew_asset": "两位消防小人",
        "environment": ("消防门", "路障", "消防栓"),
        "situation": "消防门口的通道被路障挡住了，车想出动却先过不去，大家得先把门口整理顺。",
        "source_hint": "消防门口先传来卡住的情况",
        "judgment_focus": "先清哪边、先动车还是先动门、通道到底卡在什么地方",
        "action_goal": "拨开路障、抬起消防门，再把消防车摆到门口准备通过",
        "closing_result": "消防门口的通道整理好了",
    },
    {
        "label": "医药箱接应任务",
        "location": "更衣室和二楼挂钟旁",
        "trigger_asset": "医药箱",
        "entry_assets": ("医药箱", "墙上的挂钟", "消防指挥中心"),
        "command_asset": "消防指挥中心",
        "lead_asset": "医药箱",
        "helper_asset": "消防直升机",
        "support_asset": "消防车",
        "crew_asset": "两位消防小人",
        "environment": ("医药箱", "更衣室", "墙上的挂钟"),
        "situation": "指挥中心忽然接到电话，要把医药箱送去楼上休息区，大家要先找到它，再决定怎么接应。",
        "source_hint": "更衣室和楼上那边有新的接应需求",
        "judgment_focus": "先派谁、从哪条路线送过去、医药箱现在靠近哪一边",
        "action_goal": "找到医药箱，再让消防小人或直升机去把它接应过去",
        "closing_result": "医药箱接应顺利完成",
    },
    {
        "label": "床边小火提醒",
        "location": "上下双人床旁边",
        "trigger_asset": "小火",
        "entry_assets": ("小火", "上下双人床", "闹钟"),
        "command_asset": "消防指挥中心",
        "lead_asset": "小火",
        "helper_asset": "消防车",
        "support_asset": "消防直升机",
        "crew_asset": "两位消防小人",
        "environment": ("消防屋子", "更衣室", "locker"),
        "situation": "床边冒出了一小团火苗，值班提醒一响，大家要先判断位置，再安排车和人去处理。",
        "source_hint": "床边这边最先出问题了",
        "judgment_focus": "这团小火靠近哪里、谁先去更合适、车该停在什么位置",
        "action_goal": "把消防车和消防小人摆到床边附近，把小火处理好",
        "closing_result": "床边的小火被顺利处理好了",
    },
)

STAGE_REQUIREMENTS = {
    "fs_001": {
        "intent": "把孩子带进这次消防站故事，先找到最先出现的提醒、异常、关键角色或关键物件，不要默认问哪些能动或不能动。",
        "must_cover": ("第一条故事线索", "故事入口"),
    },
    "fs_002": {
        "intent": "让孩子根据提醒和场景线索判断这次新情况是从哪一块区域、哪一座屋子或哪一边传来的，不要固定成内部/外部二选一。",
        "must_cover": ("来源判断", "提醒线索"),
    },
    "fs_003": {
        "intent": "让孩子决定这一步先让哪一个角色、载具、装置或物件动起来，并把具体积木摆动起来，不要固定成消防车还是直升机二选一。",
        "must_cover": ("先动起来的角色或载具", "协同动作"),
    },
    "fs_004": {
        "intent": "让孩子判断当前最关键的信息或阻碍，可以是位置、路线、卡住的地方、靠近哪件积木、哪边更急，不要固定成大火小火。",
        "must_cover": ("关键判断", "执行前关键线索"),
    },
    "fs_005": {
        "intent": "引导孩子实际摆弄积木完成执行动作，而不是只口头回答。执行动作可以是开门、出车、接应、集合、送装备、清理通道、处理火点、转移角色。",
        "must_cover": ("执行动作", "动作目标"),
    },
    "fs_006": {
        "intent": "让系统自己做一段收束总结，回顾刚才的警情、出动和救援结果，不再要求孩子自己复盘。",
        "must_cover": ("系统总结", "结果收束"),
    },
}

_BELL_ENTRY_TOKENS = ("铃铛", "警报", "铃声", "电话")


@dataclass(frozen=True)
class GeneratedSessionPlan:
    story_title: str
    story_context: str
    task_blueprints: tuple[dict[str, Any], ...]
    generation_source: str


def _safe_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _extract_json_mapping(raw_text: str) -> Mapping[str, Any] | None:
    candidate = raw_text.strip()
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(candidate)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, Mapping) else None


def _entity_names(items: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(_safe_text(item.get("name")) for item in items if _safe_text(item.get("name")))


def _fs001_entry_rotation_pool() -> tuple[str, ...]:
    ordered_names = (
        *_entity_names(PHYSICAL_ASSETS),
        "停机坪",
        *_entity_names(BACKGROUND_ASSETS),
    )
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in ordered_names:
        normalized = _safe_text(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_names.append(normalized)
    return tuple(unique_names)


def _preferred_fs001_entries(session_id: str, *, size: int = 4) -> tuple[str, ...]:
    rotation_pool = _fs001_entry_rotation_pool()
    if not rotation_pool:
        return ()
    if not session_id:
        return rotation_pool[:size]
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    start = digest[1] % len(rotation_pool)
    candidates = [
        rotation_pool[(start + offset) % len(rotation_pool)]
        for offset in range(size)
    ]
    if digest[2] % 7 == 0 and "铃铛" not in candidates:
        insert_at = digest[3] % (len(candidates) + 1)
        candidates.insert(insert_at, "铃铛")
    return tuple(candidates)


def _text_mentions_any(text: str, terms: Sequence[str]) -> bool:
    normalized = _safe_text(text)
    return any(term and term in normalized for term in terms)


def _serialize_completion_points(raw_points: Sequence[Mapping[str, Any]] | Sequence[str]) -> tuple[dict[str, Any], ...]:
    if isinstance(raw_points, str):
        raw_points = (raw_points,)
    serialized: list[dict[str, Any]] = []
    for raw_point in raw_points:
        if isinstance(raw_point, Mapping):
            label = _safe_text(raw_point.get("label"))
            keywords = tuple(
                _safe_text(keyword)
                for keyword in (raw_point.get("keywords") or ())
                if _safe_text(keyword)
            )
            if label and keywords:
                serialized.append({"label": label, "keywords": list(keywords)})
            elif label:
                serialized.append({"label": label, "keywords": [label]})
            continue

        label = _safe_text(raw_point)
        if label:
            serialized.append({"label": label, "keywords": [label]})
    return tuple(serialized)


class FireStationSessionPlanner:
    def __init__(self, provider_mode: str = "qwen"):
        self.provider_mode = provider_mode
        self._client = self._build_client()

    def build_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        requested_task_ids: Sequence[str] | None,
        default_task_blueprints: Sequence[Mapping[str, Any]],
    ) -> GeneratedSessionPlan:
        selected_task_ids = tuple(
            task_id
            for task_id, _ in DEFAULT_STAGE_ORDER
            if not requested_task_ids or task_id in {str(item).strip() for item in requested_task_ids}
        )
        if not selected_task_ids:
            selected_task_ids = tuple(task_id for task_id, _ in DEFAULT_STAGE_ORDER)

        try:
            if self._client is not None and self.provider_mode in {"qwen", "auto"}:
                model_plan = self._try_build_model_plan(
                    session_id=session_id,
                    scene_id=scene_id,
                    selected_task_ids=selected_task_ids,
                    default_task_blueprints=default_task_blueprints,
                )
                if model_plan is not None:
                    return model_plan
        except Exception:
            pass

        return self.build_fallback_plan(
            session_id=session_id,
            scene_id=scene_id,
            selected_task_ids=selected_task_ids,
            default_task_blueprints=default_task_blueprints,
        )

    def build_fallback_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        selected_task_ids: Sequence[str],
        default_task_blueprints: Sequence[Mapping[str, Any]],
    ) -> GeneratedSessionPlan:
        return self._build_fallback_plan(
            session_id=session_id,
            scene_id=scene_id,
            selected_task_ids=selected_task_ids,
            default_task_blueprints=default_task_blueprints,
        )

    def _build_client(self) -> OpenAICompatibleClient | None:
        try:
            runtime_env = build_runtime_env(PHASE5_ROOT_DIR / ".env.local")
            config = OpenAICompatibleConfig.from_env(
                runtime_env,
                provider_label="Qwen fire station session planner",
                api_key_env_keys=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
                model_env_keys=("QWEN_STORY_MODEL", "DASHSCOPE_STORY_MODEL", "QWEN_MODEL", "DASHSCOPE_MODEL"),
                base_url_env_keys=("QWEN_BASE_URL", "DASHSCOPE_BASE_URL"),
                request_url_env_keys=("QWEN_REQUEST_URL", "DASHSCOPE_REQUEST_URL", "DASHSCOPE_CHAT_COMPLETIONS_URL"),
                timeout_env_keys=("QWEN_STORY_TIMEOUT_SECONDS", "DASHSCOPE_STORY_TIMEOUT_SECONDS"),
                max_tokens_env_keys=(),
                temperature_env_keys=(),
                default_base_url=DEFAULT_QWEN_BASE_URL,
                default_model=DEFAULT_PLANNER_QWEN_MODEL,
                default_timeout_seconds=18.0,
                default_max_tokens=1800,
                default_temperature=0.8,
            )
        except (OpenAICompatibleConfigError, ValueError):
            return None
        return OpenAICompatibleClient(config)

    def _try_build_model_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        selected_task_ids: Sequence[str],
        default_task_blueprints: Sequence[Mapping[str, Any]],
    ) -> GeneratedSessionPlan | None:
        preferred_fs001_entries = _preferred_fs001_entries(session_id)
        try:
            result = self._client.create_chat_completion(  # type: ignore[union-attr]
                [
                    {
                        "role": "system",
                        "content": (
                            "你是儿童消防站积木玩具的故事规划器。"
                            "请输出一个 JSON 对象，字段只有 story_title, story_context, tasks。"
                            "tasks 固定 6 个，task_id 必须按顺序是 fs_001 到 fs_006。"
                            "每个 task 必须包含 name, goal, expected_child_action, parent_label, story_beat, "
                            "selected_entities, selected_background_elements, completion_points, assistant_led_summary。"
                            "故事要前后连贯，且每一步都鼓励孩子真实摆弄积木，不要只做抽象问答。"
                            "不要套用固定故事池；每次都要基于这次 session 自己想一条新的消防站故事线。"
                            "故事不必每轮都是真着火或灭火；可以从消防站场景里自然衍生集合、接应、整理通道、运送物品、夜间值班、接电话等任务。"
                            "fs_001 的入口可以来自这套消防站里任何实体积木、可互动装置、空间位置或背景线索，不要只盯住少数默认物件。"
                            "fs_001 不要每次都从铃铛、警报或电话开始。铃铛可以偶尔出现，但不能每轮都当默认入口。"
                            "优先从这轮给定的首步入口候选里选不同的实体、位置或背景线索起步。"
                            "completion_points 必须是数组，每项都要有 label 和 keywords。"
                            "每个阶段优先给 2 个 completion_points；keywords 要覆盖常见口语近义说法。"
                            "fs_006 必须是系统自动总结阶段：assistant_led_summary=true，不能要求孩子自己复述。"
                            "只输出 JSON，不要解释，不要代码块。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "scene_id": scene_id,
                                "session_id": session_id,
                                "stage_slots": [
                                    {
                                        "task_id": task_id,
                                        "default_name": next(
                                            (
                                                _safe_text(task.get("name"))
                                                for task in default_task_blueprints
                                                if _safe_text(task.get("task_id")) == task_id
                                            ),
                                            default_name,
                                        ),
                                        "stage_requirement": STAGE_REQUIREMENTS.get(task_id, {}).get("intent"),
                                    }
                                    for task_id, default_name in DEFAULT_STAGE_ORDER
                                    if task_id in selected_task_ids
                                ],
                                "physical_assets": [item["name"] for item in PHYSICAL_ASSETS],
                                "background_assets": [item["name"] for item in BACKGROUND_ASSETS],
                                "planning_requirements": [
                                    "每轮只挑部分积木和背景线索，但 6 阶段必须形成同一条故事主线。",
                                    "fs_001 的入口可以来自整套消防站里的任意实体积木、位置或背景线索，不要只用少数固定入口。",
                                    "优先围绕不同的实体或背景线索展开动作，不要把铃铛、警报或电话固定成每一轮的唯一入口。",
                                    f"这轮 fs_001 的优先入口候选：{'、'.join(preferred_fs001_entries)}。",
                                    "同一套积木在不同轮里可以扮演不同作用，不要把某个积木永久绑定成一种剧情用途。",
                                    "不要把所有阶段都写成着火和灭火；允许生成站内演练、集合、接应、通道清理、装备整理、医药箱运送、电话指令等故事。",
                                    "阶段语义保持：fs_001 故事入口观察，fs_002 来源判断，fs_003 先动起来，fs_004 关键判断，fs_005 动手执行，fs_006 系统总结。",
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
            payload = _extract_json_mapping(result.content_text)
        except (OpenAICompatibleRequestError, OpenAICompatibleConfigError, ValueError, json.JSONDecodeError):
            return None

        if payload is None:
            return None

        story_title = _safe_text(payload.get("story_title"))
        story_context = _safe_text(payload.get("story_context"))
        raw_tasks = payload.get("tasks")
        if not story_title or not story_context or not isinstance(raw_tasks, Sequence):
            return None

        base_defaults = {
            _safe_text(task.get("task_id")): dict(task)
            for task in default_task_blueprints
            if _safe_text(task.get("task_id"))
        }
        tasks_by_id: dict[str, dict[str, Any]] = {}
        for raw_task in raw_tasks:
            if not isinstance(raw_task, Mapping):
                continue
            task_id = _safe_text(raw_task.get("task_id"))
            if task_id not in selected_task_ids or task_id in tasks_by_id:
                continue
            default_task = base_defaults.get(task_id, {})
            completion_points = _serialize_completion_points(raw_task.get("completion_points") or ())
            if not completion_points:
                continue
            tasks_by_id[task_id] = {
                "task_id": task_id,
                "name": _safe_text(raw_task.get("name")) or _safe_text(default_task.get("name")) or task_id,
                "goal": _safe_text(raw_task.get("goal")) or _safe_text(default_task.get("goal")) or "继续完成这一阶段。",
                "expected_child_action": _safe_text(raw_task.get("expected_child_action"))
                or _safe_text(default_task.get("expected_child_action"))
                or "继续摆弄当前积木并说出你的判断。",
                "parent_label": _safe_text(raw_task.get("parent_label"))
                or _safe_text(raw_task.get("name"))
                or _safe_text(default_task.get("parent_label"))
                or _safe_text(default_task.get("name"))
                or task_id,
                "story_beat": _safe_text(raw_task.get("story_beat")) or _safe_text(raw_task.get("goal")),
                "selected_entities": [
                    _safe_text(value) for value in (raw_task.get("selected_entities") or ()) if _safe_text(value)
                ],
                "selected_background_elements": [
                    _safe_text(value)
                    for value in (raw_task.get("selected_background_elements") or ())
                    if _safe_text(value)
                ],
                "completion_points": list(completion_points),
                "assistant_led_summary": bool(
                    raw_task.get("assistant_led_summary", default_task.get("assistant_led_summary", task_id == "fs_006"))
                ),
                "requires_self_report": bool(raw_task.get("requires_self_report", default_task.get("requires_self_report"))),
                "max_attempts": int(raw_task.get("max_attempts") or default_task.get("max_attempts") or 5),
            }

        ordered_tasks = [
            tasks_by_id[task_id]
            for task_id in selected_task_ids
            if task_id in tasks_by_id
        ]
        if len(ordered_tasks) != len(selected_task_ids):
            return None
        fs001_task = tasks_by_id.get("fs_001")
        if self._is_overusing_bell_in_fs001(
            session_id=session_id,
            story_title=story_title,
            fs001_task=fs001_task,
        ):
            return None

        return GeneratedSessionPlan(
            story_title=story_title,
            story_context=story_context,
            task_blueprints=tuple(ordered_tasks),
            generation_source="llm_provider",
        )

    @staticmethod
    def _is_overusing_bell_in_fs001(
        *,
        session_id: str,
        story_title: str,
        fs001_task: Mapping[str, Any] | None,
    ) -> bool:
        if not isinstance(fs001_task, Mapping):
            return False
        selected_entities = [
            _safe_text(item)
            for item in (fs001_task.get("selected_entities") or ())
            if _safe_text(item)
        ]
        fs001_text = " ".join(
            part
            for part in (
                story_title,
                _safe_text(fs001_task.get("goal")),
                _safe_text(fs001_task.get("story_beat")),
                _safe_text(fs001_task.get("expected_child_action")),
            )
            if part
        )
        preferred_entries = set(_preferred_fs001_entries(session_id))
        bell_is_preferred = "铃铛" in preferred_entries
        bell_is_lead_entity = bool(selected_entities) and selected_entities[0] == "铃铛"
        bell_is_selected = "铃铛" in selected_entities
        bell_is_explicitly_mentioned = _text_mentions_any(fs001_text, _BELL_ENTRY_TOKENS)
        preferred_anchors = [entity for entity in selected_entities if entity in preferred_entries and entity != "铃铛"]

        if not bell_is_selected and not bell_is_explicitly_mentioned:
            return False
        if bell_is_preferred:
            return False
        if bell_is_lead_entity:
            return True
        if not preferred_anchors:
            return True
        if bell_is_explicitly_mentioned and len(preferred_anchors) < 2:
            return True
        return False

    def _build_fallback_plan(
        self,
        *,
        session_id: str,
        scene_id: str,
        selected_task_ids: Sequence[str],
        default_task_blueprints: Sequence[Mapping[str, Any]],
    ) -> GeneratedSessionPlan:
        seed_bytes = hashlib.sha256(session_id.encode("utf-8")).digest()
        incident = STORY_SLOTS[seed_bytes[0] % len(STORY_SLOTS)]
        story_title = f"{incident['label']}的站内故事"
        story_context = (
            f"消防站里忽然出现了一条新的站内故事：{incident['situation']}"
            f"孩子需要先找到第一条故事线索，再判断这条情况是从哪边传来的，"
            f"接着决定先让谁动起来、看清当前最关键的信息，最后亲手摆弄积木完成处理，"
            "再由系统把这次故事收束总结。"
        )
        base_defaults = {
            _safe_text(task.get("task_id")): dict(task)
            for task in default_task_blueprints
            if _safe_text(task.get("task_id"))
        }

        fallback_tasks: list[dict[str, Any]] = []
        for task_id in selected_task_ids:
            default_task = base_defaults.get(task_id, {})
            fallback_tasks.append(
                self._build_fallback_task_blueprint(
                    task_id=task_id,
                    default_task=default_task,
                    incident=incident,
                )
            )

        return GeneratedSessionPlan(
            story_title=story_title,
            story_context=story_context,
            task_blueprints=tuple(fallback_tasks),
            generation_source="deterministic_fallback",
        )

    def _build_fallback_task_blueprint(
        self,
        *,
        task_id: str,
        default_task: Mapping[str, Any],
        incident: Mapping[str, Any],
    ) -> dict[str, Any]:
        lead_asset = _safe_text(incident.get("lead_asset"))
        helper_asset = _safe_text(incident.get("helper_asset"))
        support_asset = _safe_text(incident.get("support_asset"))
        crew_asset = _safe_text(incident.get("crew_asset")) or "两位消防小人"
        command_asset = _safe_text(incident.get("command_asset"))
        alarm_asset = _safe_text(incident.get("trigger_asset"))
        entry_assets = tuple(_safe_text(item) for item in (incident.get("entry_assets") or ()) if _safe_text(item))
        if not entry_assets:
            entry_assets = tuple(item for item in (alarm_asset, command_asset, lead_asset) if item)
        environment = tuple(_safe_text(item) for item in (incident.get("environment") or ()) if _safe_text(item))
        location = _safe_text(incident.get("location"))
        label = _safe_text(incident.get("label"))
        situation = _safe_text(incident.get("situation"))
        source_hint = _safe_text(incident.get("source_hint"))
        judgment_focus = _safe_text(incident.get("judgment_focus"))
        action_goal = _safe_text(incident.get("action_goal"))
        closing_result = _safe_text(incident.get("closing_result"))

        if task_id == "fs_001":
            return {
                "task_id": task_id,
                "name": "先找到故事入口",
                "goal": f"先围绕{location}找到这次新情况最先冒出来的故事线索，看看最先被注意到的是谁、什么，或者哪一块位置最值得先看。",
                "expected_child_action": f"请孩子指出先被注意到的{'、'.join(entry_assets[:3])}，或者最先该去看的{location}。",
                "parent_label": "先找到故事线索",
                "story_beat": f"{situation} 先别急着往下做，先找到{location}这边最先冒出来的提醒和线索。",
                "selected_entities": list(entry_assets[:3]),
                "selected_background_elements": list(environment[:1] or ("墙上的挂钟",)),
                "completion_points": [
                    {"label": "第一条故事线索", "keywords": [*entry_assets[:3], "提醒", "响了", "亮了", "电话", "动静", "先看到", "先注意到"]},
                    {"label": "故事入口位置", "keywords": [location, "哪里", "那边", "门口", "床边", "停机坪", "更衣室"]},
                ],
                "requires_self_report": bool(default_task.get("requires_self_report")),
                "max_attempts": int(default_task.get("max_attempts") or 5),
            }
        if task_id == "fs_002":
            return {
                "task_id": task_id,
                "name": "先判断情况从哪边来",
                "goal": f"根据{alarm_asset}和{command_asset}的线索，判断这次新情况主要来自{location}这一块区域。",
                "expected_child_action": f"让孩子说出这次情况更像发生在{location}附近，并指出是谁先把消息传到{command_asset}。",
                "parent_label": "判断情况从哪来",
                "story_beat": f"{alarm_asset}和{command_asset}先后提醒之后，大家要先判断这条任务到底是从哪一块传来的：{source_hint}。",
                "selected_entities": [alarm_asset, command_asset, lead_asset],
                "selected_background_elements": list(environment[1:3] or ("更衣室", "墙上的挂钟")),
                "completion_points": [
                    {"label": "来源判断", "keywords": [location, source_hint, "哪边", "哪一块", "哪座屋子", "门口", "停机坪", "更衣室"]},
                    {"label": "提醒线索", "keywords": [alarm_asset, command_asset, "接警", "铃声", "打电话", "先提醒"]},
                ],
                "requires_self_report": bool(default_task.get("requires_self_report")),
                "max_attempts": int(default_task.get("max_attempts") or 5),
            }
        if task_id == "fs_003":
            return {
                "task_id": task_id,
                "name": "先让关键角色动起来",
                "goal": f"让孩子决定先让{lead_asset}、{helper_asset}、{support_asset}或{crew_asset}里的谁先动起来，并把它摆到合适的位置。",
                "expected_child_action": f"请孩子亲手移动{lead_asset}、{helper_asset}、{support_asset}或{crew_asset}里的关键积木，做出第一步准备动作。",
                "parent_label": "先动起来",
                "story_beat": f"确认来源之后，大家要先让关键角色动起来：也许是{lead_asset}，也许是{helper_asset}，也可能要先动{support_asset}和{crew_asset}。",
                "selected_entities": [lead_asset, helper_asset, crew_asset],
                "selected_background_elements": list(environment[:1] or ("路障",)),
                "completion_points": [
                    {"label": "先动起来的主角", "keywords": [lead_asset, helper_asset, support_asset, "先动", "先摆", "先去", "先处理"]},
                    {"label": "协同动作", "keywords": [crew_asset, "消防小人", "集合", "跟上", "一起", "准备好"]},
                ],
                "requires_self_report": bool(default_task.get("requires_self_report")),
                "max_attempts": int(default_task.get("max_attempts") or 5),
            }
        if task_id == "fs_004":
            return {
                "task_id": task_id,
                "name": "看清现在最要紧的事",
                "goal": f"让孩子判断当前最关键的信息或阻碍是什么，比如路线、位置、卡住的地方、先要处理的物件。重点是：{judgment_focus}。",
                "expected_child_action": f"让孩子说出现在最要紧的一点，比如{judgment_focus}。",
                "parent_label": "判断关键点",
                "story_beat": f"大家要动手之前，还得先看清楚：{judgment_focus}。",
                "selected_entities": [lead_asset, support_asset, helper_asset],
                "selected_background_elements": list(environment[:2] or ("墙上的挂钟",)),
                "completion_points": [
                    {"label": "关键判断", "keywords": [location, lead_asset, support_asset, helper_asset, "哪边", "卡住", "先处理", "靠近", "路线"]},
                    {"label": "执行前线索", "keywords": [judgment_focus, location, "门口", "床边", "停机坪", "楼上", "更衣室"]},
                ],
                "requires_self_report": bool(default_task.get("requires_self_report")),
                "max_attempts": int(default_task.get("max_attempts") or 5),
            }
        if task_id == "fs_005":
            return {
                "task_id": task_id,
                "name": "动手把这一步做完",
                "goal": f"引导孩子亲手摆动积木完成执行动作。这一轮要做的是：{action_goal}。",
                "expected_child_action": f"请孩子真的动手去做：{action_goal}。",
                "parent_label": "动手执行",
                "story_beat": f"这一步不只是说，要真的把积木摆起来：{action_goal}。",
                "selected_entities": [lead_asset, helper_asset, crew_asset],
                "selected_background_elements": list(environment[:1] or ("消防栓",)),
                "completion_points": [
                    {"label": "执行动作", "keywords": [lead_asset, helper_asset, support_asset, crew_asset, "开过去", "摆过去", "送过去", "接应", "集合", "打开", "整理"]},
                    {"label": "动作目标", "keywords": [location, action_goal, lead_asset, support_asset, "到位", "处理好", "送到", "清出来"]},
                ],
                "requires_self_report": bool(default_task.get("requires_self_report")),
                "max_attempts": int(default_task.get("max_attempts") or 5),
            }
        return {
            "task_id": task_id,
            "name": "系统回站总结",
            "goal": "由系统自己把刚才的故事入口、判断、动作和结果连成一段完整收尾，不再要求孩子自己总结。",
            "expected_child_action": "听系统把这次消防站故事收尾讲完，然后自然结束这一轮。",
            "parent_label": "回站总结",
            "story_beat": f"任务结束后，大家回到消防站，由系统来收束：先是谁提醒，后来谁动起来，最后又是怎么把“{label}”处理好的。",
            "selected_entities": [alarm_asset, command_asset, lead_asset, helper_asset],
            "selected_background_elements": list(environment[:2] or ("墙上的挂钟",)),
            "completion_points": [
                {"label": "系统总结故事线索", "keywords": [alarm_asset, command_asset, label, "提醒", "接警", "电话"]},
                {"label": "系统总结处理结果", "keywords": [lead_asset, helper_asset, support_asset, closing_result, "处理好了", "回站", "总结"]},
            ],
            "assistant_led_summary": True,
            "requires_self_report": False,
            "max_attempts": int(default_task.get("max_attempts") or 5),
        }
