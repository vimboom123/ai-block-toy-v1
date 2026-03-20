from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.scene_loader import get_candidate_task

PROMPT_VERSION = "classic_world_fire_station_text_dialog_v2"


@dataclass(frozen=True)
class PromptBundle:
    scene_id: str
    task_id: str
    prompt_version: str
    system_prompt: str
    context_prompt: str
    user_prompt: str
    expected_child_action: str


def _lines(title: str, values: list[str]) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return f"{title}: none"
    return f"{title}: {'、'.join(cleaned)}"


def build_prompt_bundle(scene_pack: dict[str, Any], task_id: str) -> PromptBundle:
    task = get_candidate_task(scene_pack, task_id)

    age_band = scene_pack.get("age_band") or {}
    conversation_style = scene_pack.get("conversation_style") or {}
    system_voice = conversation_style.get("system_voice") or {}
    world_constraints = scene_pack.get("world_constraints") or {}
    story_world = scene_pack.get("story_world") or {}
    core_objects = scene_pack.get("core_objects") or {}
    child_role = scene_pack.get("character_roles", {}).get("child_role", "")
    guide_role = scene_pack.get("character_roles", {}).get("guide_role", "")

    task_id_value = str(task.get("task_id") or "")
    task_name = str(task.get("name") or "")
    task_goal = str(task.get("goal") or "")
    expected_child_action = str(task.get("expected_child_action") or "")

    system_prompt = "\n".join(
        [
            "你是 AI 积木玩具的中文文本引导员。",
            f"当前面对 {age_band.get('min', 4)}-{age_band.get('max', 6)} 岁孩子。",
            "这是纯文本对话运行时，不要提 UI、按钮、语音识别或语音播报。",
            "句子要短，一次只给一个任务，先观察再行动。",
            "reply_text 最多两句，每句尽量不超过 18 个字。",
            "next_expected_action 要短，尽量控制在 12 个字内。",
            "语气要有引导感，但不能说教，不能制造恐惧。",
            "你必须只输出一个 JSON 对象，不要加代码块，不要加额外说明。",
            'JSON 字段固定为 reply_text, guidance_type, next_expected_action。',
            "guidance_type 只能从 observation, decision, action, confirmation, repair 里选一个。",
        ]
    )

    compact_context_blocks = [
        f"scene_id: {scene_pack.get('scene_id')}",
        f"setting: {story_world.get('setting', '')}",
        _lines("required_objects", core_objects.get("required") or []),
        _lines("movable_objects", world_constraints.get("movable_objects") or []),
        f"child_role: {child_role}",
        f"guide_role: {guide_role}",
        f"short_sentences: {str(system_voice.get('short_sentences', True)).lower()}",
        f"task_id: {task_id_value}",
        f"task_name: {task_name}",
        f"task_goal: {task_goal}",
        f"expected_child_action: {expected_child_action}",
    ]

    if task_id == "fs_004":
        compact_context_blocks = [
            f"scene_id: {scene_pack.get('scene_id')}",
            "setting: 小镇消防站接到火警，需要判断火源情况。",
            "key_objects: 两个小火源、一个大火源、消防车、直升机、指挥台、床",
            f"child_role: {child_role}",
            f"guide_role: {guide_role}",
            f"task_id: {task_id_value}",
            f"task_name: {task_name}",
            f"task_goal: {task_goal}",
            f"expected_child_action: {expected_child_action}",
            "extra_rule: 先引导孩子判断火源大小和位置，不替孩子直接决定。",
        ]

    context_prompt = "\n".join(compact_context_blocks)

    user_prompt = "\n".join(
        [
            "基于上面的场景信息，为这个任务生成当前这一轮的儿童引导语。",
            "要求：",
            "1. reply_text 用中文，像消防指挥员在和孩子说话，控制在两句内。",
            "2. 只能推动当前任务，不要跳到后面多个步骤。",
            "3. next_expected_action 必须是一个立刻可执行的单一步动作，尽量控制在 12 个字内。",
            "4. 如果任务本质是判断/选择，就把引导重点放在判断，不要直接替孩子决定。",
            "5. 输出严格 JSON。",
        ]
    )

    return PromptBundle(
        scene_id=str(scene_pack.get("scene_id", "")),
        task_id=task_id_value,
        prompt_version=PROMPT_VERSION,
        system_prompt=system_prompt,
        context_prompt=context_prompt,
        user_prompt=user_prompt,
        expected_child_action=expected_child_action,
    )
