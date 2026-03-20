from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from input_understanding import (  # noqa: E402
    ArkDoubaoInteractionProvider,
    InteractionDraft,
    CompletionPoint,
    MinimalInteractionGenerator,
    RuleFirstSignalResolver,
    TaskContext,
)
from phase6_bridge import build_phase7_bridge_package  # noqa: E402


def build_task_context() -> TaskContext:
    return TaskContext(
        task_id="fs_004",
        task_name="消防车出动",
        task_goal="让孩子说出消防车要去做什么",
        expected_child_action="说出消防车要去救火",
        scene_context="消防车从消防站出发，路上会遇到不同的火情，但这轮流程始终是先发现火源，再去救火。",
        completion_points=(
            CompletionPoint.parse("救火:救火,灭火"),
        ),
        completion_match_mode="any",
    )


def load_script_module(script_name: str):
    script_path = ROOT_DIR / "scripts" / script_name
    module_name = f"phase7_{script_name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_first_resolver_can_complete_task_from_keywords() -> None:
    resolver = RuleFirstSignalResolver()

    signal_resolution = resolver.resolve(
        child_input_text="我要开消防车去灭火",
        current_task=build_task_context(),
    )

    assert signal_resolution.task_signal == "task_completed"
    assert "救火" in signal_resolution.matched_completion_points
    assert signal_resolution.fallback_needed is False


def test_rule_first_resolver_accepts_scene_near_synonyms_and_asr_variants() -> None:
    resolver = RuleFirstSignalResolver()
    task_context = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(
            CompletionPoint.parse("固定元素:墙上,画在墙上,固定,不能动"),
        ),
        completion_match_mode="any",
    )

    signal_resolution = resolver.resolve(
        child_input_text="挂在墙上的东西不会动",
        current_task=task_context,
    )

    assert signal_resolution.task_signal == "task_completed"
    assert signal_resolution.matched_completion_points == ("固定元素",)


def test_rule_first_resolver_accepts_rescue_semantic_aliases() -> None:
    resolver = RuleFirstSignalResolver()

    signal_resolution = resolver.resolve(
        child_input_text="消防车赶过去处理一下",
        current_task=build_task_context(),
    )

    assert signal_resolution.task_signal == "task_completed"
    assert signal_resolution.matched_completion_points == ("救火",)


def test_interaction_generator_redirects_when_child_is_interested_but_off_target() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")

    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )
    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.task_signal == "keep_trying"
    assert interaction_generation.interaction_mode in {"warm_redirect", "playful_probe"}
    assert "消防车" in interaction_generation.reply_text
    assert interaction_generation.acknowledged_child_point == "消防车"
    assert interaction_generation.followup_question is not None
    assert "回到这一步" not in interaction_generation.followup_question
    assert "...？" not in interaction_generation.followup_question
    assert (
        "消防车" in interaction_generation.followup_question
        or "救火" in interaction_generation.followup_question
        or "帮什么忙" in interaction_generation.followup_question
        or "做什么" in interaction_generation.followup_question
    )
    assert "你来告诉我" not in interaction_generation.reply_text
    assert interaction_generation.generation_source == "template_fallback"


def test_interaction_generator_uses_completion_points_for_scene_tasks() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(
            CompletionPoint.parse("背景可动:背景,可动,能动,会动,墙上,画在墙上,固定,不能动"),
        ),
        completion_match_mode="any",
    )

    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=task_context,
    )
    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.task_signal == "keep_trying"
    assert interaction_generation.followup_question is not None
    assert "背景上有什么" not in interaction_generation.followup_question
    assert (
        "能动" in interaction_generation.followup_question
        or "不能动" in interaction_generation.followup_question
        or "画在墙上" in interaction_generation.followup_question
    )


def test_partial_credit_scene_task_followup_stays_on_current_scene_goal() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(
            CompletionPoint.parse("能动元素:床,消防车,能动,会动,可动"),
            CompletionPoint.parse("固定元素:墙上,画在墙上,固定,不能动"),
        ),
        completion_match_mode="all",
    )

    signal_resolution = resolver.resolve(
        child_input_text="床会动",
        current_task=task_context,
    )
    interaction_generation = generator.generate(
        child_input_text="床会动",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.partial_credit is True
    assert "做什么" not in interaction_generation.reply_text
    assert "干嘛" not in interaction_generation.reply_text
    assert (
        "能动" in interaction_generation.reply_text
        or "不能动" in interaction_generation.reply_text
        or "画在墙上" in interaction_generation.reply_text
    )


def test_unified_reply_that_jumps_to_next_task_is_realigned_to_current_task() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(
            CompletionPoint.parse("能动元素:床,消防车,能动,会动,可动"),
            CompletionPoint.parse("固定元素:墙上,画在墙上,固定,不能动"),
        ),
        completion_match_mode="all",
    )
    signal_resolution = resolver.resolve(
        child_input_text="床会动",
        current_task=task_context,
    )
    interaction_context = generator.build_context(
        child_input_text="床会动",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    generation = generator.build_generation_from_draft(
        interaction_context=interaction_context,
        draft=SimpleNamespace(
            reply_text="那我们先让消防车还是直升机出发呀？",
            acknowledged_child_point="床会动",
            followup_question="那我们先让消防车还是直升机出发呀？",
            provider_name="qwen_unified",
        ),
        provider_name="qwen_unified",
    )

    assert signal_resolution.task_signal == "keep_trying"
    assert generation.generation_source == "llm_provider"
    assert generation.fallback_reason is not None
    assert "realigned" in generation.fallback_reason
    assert "消防车还是直升机" not in generation.reply_text
    assert (
        "能动" in generation.reply_text
        or "不能动" in generation.reply_text
        or "画在墙上" in generation.reply_text
    )


def test_unified_reply_with_scene_details_is_preserved_when_it_stays_on_current_task() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(
            CompletionPoint.parse("能动元素:床,消防车,能动,会动,可动"),
            CompletionPoint.parse("固定元素:墙上,画在墙上,固定,不能动"),
        ),
        completion_match_mode="all",
    )
    signal_resolution = resolver.resolve(
        child_input_text="我不知道",
        current_task=task_context,
    )
    interaction_context = generator.build_context(
        child_input_text="我不知道",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    generation = generator.build_generation_from_draft(
        interaction_context=interaction_context,
        draft=SimpleNamespace(
            reply_text=(
                "没关系，我们一起看看厨房墙上的画，还有旁边的小消防车模型。"
                "你觉得哪个会动，哪个是固定在那里的呀？"
            ),
            acknowledged_child_point=None,
            followup_question="你觉得画在墙上的东西会动吗？",
            provider_name="qwen_unified",
        ),
        provider_name="qwen_unified",
    )

    assert generation.generation_source == "llm_provider"
    assert generation.fallback_reason is None
    assert "厨房墙上的画" in generation.reply_text
    assert "小消防车模型" in generation.reply_text


def test_fast_story_fallback_starts_from_scene_recognition_not_alarm_dispatch() -> None:
    module = load_script_module("run_voice_fast.py")
    opening_text, scene_context = module._build_fallback_fire_station_story_context()

    assert "先接警" not in opening_text
    assert "再出动" not in opening_text
    assert ("哪些能动" in opening_text) or ("画在墙上" in opening_text)
    assert opening_text.endswith("？")
    assert "先区分场景里能动和固定的元素" in scene_context


def test_fast_story_parser_accepts_structured_scene_context_objects() -> None:
    module = load_script_module("run_voice_fast.py")
    scene_context = module._coerce_story_scene_context(
        {
            "fire_location": "玩具城堡屋顶",
            "fire_situation": "火势正在蔓延",
            "task_sequence": ["场景识别", "接警判断", "集合出动"],
        }
    )

    assert scene_context is not None
    assert "玩具城堡屋顶" in scene_context
    assert "火势正在蔓延" in scene_context
    assert "场景识别" in scene_context
    assert "接警判断" in scene_context


def test_fast_story_alignment_appends_first_task_question_when_missing() -> None:
    module = load_script_module("run_voice_fast.py")
    opening_text, scene_context = module._align_story_to_first_task(
        "消防站里忽然响起了警报声，大家都看向墙上的火苗。",
        "火情：墙上的火苗变成了真的火。",
    )

    assert opening_text.endswith("？")
    assert "哪些能动" in opening_text or "画在墙上" in opening_text
    assert "任务顺序" in scene_context


@pytest.mark.parametrize(
    ("task_context", "expected_keywords"),
    (
        (
            TaskContext(
                task_id="fs_001",
                task_name="场景识别",
                task_goal="说出哪些是能动的，哪些只是画在墙上的",
                expected_child_action="区分可操作元素与背景元素",
                completion_points=(
                    CompletionPoint.parse("背景可动:背景,可动,能动,会动,墙上,画在墙上,固定,不能动"),
                ),
                completion_match_mode="any",
            ),
            ("能动", "不能动", "画在墙上"),
        ),
        (
            TaskContext(
                task_id="fs_002",
                task_name="接警判断",
                task_goal="在指挥台确认这次求助来自哪里",
                expected_child_action="说出内部火警或外部场景火警",
                completion_points=(
                    CompletionPoint.parse("接警地点:内部,外部,消防站,别的场景,外面"),
                ),
                completion_match_mode="any",
            ),
            ("内部", "外部", "里面", "外面"),
        ),
        (
            TaskContext(
                task_id="fs_003",
                task_name="集合出动",
                task_goal="让消防员集合，并决定消防车还是直升机先出发",
                expected_child_action="完成角色和载具选择",
                completion_points=(
                    CompletionPoint.parse("集合出动:消防员,消防车,直升机,集合,出动,先出发"),
                ),
                completion_match_mode="any",
            ),
            ("消防车", "直升机", "集合", "出动"),
        ),
        (
            TaskContext(
                task_id="fs_004",
                task_name="火源判断",
                task_goal="识别这次是小火源还是大火源，以及火源位置",
                expected_child_action="根据火源大小/位置选择处理策略",
                completion_points=(
                    CompletionPoint.parse("火源大小:大火,小火,中火"),
                    CompletionPoint.parse("火源位置:左边,右边,床,位置"),
                ),
                completion_match_mode="any",
            ),
            ("小火", "中火", "大火", "左边", "右边", "床"),
        ),
        (
            TaskContext(
                task_id="fs_005",
                task_name="救援执行",
                task_goal="把对应载具和消防员送到任务位置",
                expected_child_action="完成出动和处理动作",
                completion_points=(
                    CompletionPoint.parse("救援执行:去救火,灭火,救援,出发,处理,到了,赶去"),
                ),
                completion_match_mode="any",
            ),
            ("救火", "灭火", "送去", "出发", "处理"),
        ),
        (
            TaskContext(
                task_id="fs_006",
                task_name="回站总结",
                task_goal="复述刚才发生了什么",
                expected_child_action="用自己的话按顺序总结",
                completion_points=(
                    CompletionPoint.parse("回站总结:总结,刚才,先,然后,最后,回站,归队,我知道了"),
                ),
                completion_match_mode="any",
            ),
            ("刚才", "下次", "总结", "回站"),
        ),
    ),
)
def test_fire_station_tasks_keep_questions_on_topic(
    task_context: TaskContext,
    expected_keywords: tuple[str, ...],
) -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")

    signal_resolution = resolver.resolve(
        child_input_text="我们继续吧",
        current_task=task_context,
    )
    interaction_generation = generator.generate(
        child_input_text="我们继续吧",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.task_signal == "keep_trying"
    assert interaction_generation.followup_question is not None
    assert any(keyword in interaction_generation.followup_question for keyword in expected_keywords)


def test_interaction_generator_prefers_provider_reply_when_available() -> None:
    resolver = RuleFirstSignalResolver()

    class FakeProvider:
        provider_name = "fake_doubao"

        def generate_reply(self, **_: object) -> InteractionDraft:
            return InteractionDraft(
                reply_text="是啊，消防车真挺帅。那它现在要开去帮谁呀？",
                acknowledged_child_point="消防车",
                followup_question="那它现在要开去帮谁呀？",
            )

    generator = MinimalInteractionGenerator(provider=FakeProvider(), provider_mode="auto")
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )
    interaction_context = generator.build_context(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "llm_provider"
    assert interaction_generation.provider_name == "fake_doubao"
    assert interaction_generation.followup_question == interaction_context.preferred_followup_question
    assert interaction_generation.reply_text == f"是啊，消防车真挺帅。{interaction_context.preferred_followup_question}"
    assert "帮谁呀" not in interaction_generation.reply_text


def test_interaction_generator_pushes_followup_to_the_end_when_provider_reply_is_loose() -> None:
    resolver = RuleFirstSignalResolver()

    class FakeProvider:
        provider_name = "fake_doubao"

        def generate_reply(self, **_: object) -> InteractionDraft:
            return InteractionDraft(
                reply_text="好嘞，接警成功了。我们先看下一步。",
                acknowledged_child_point="接警成功",
                followup_question="这是内部火警还是外部火警呀？",
            )

    generator = MinimalInteractionGenerator(provider=FakeProvider(), provider_mode="auto")
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )
    interaction_context = generator.build_context(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "llm_provider"
    assert interaction_generation.followup_question == interaction_context.preferred_followup_question
    assert interaction_generation.reply_text == f"好嘞，接警成功了。{interaction_context.preferred_followup_question}"
    assert "内部火警" not in interaction_generation.reply_text


def test_interaction_generator_keeps_provider_followup_when_it_is_already_on_task() -> None:
    resolver = RuleFirstSignalResolver()

    class FakeProvider:
        provider_name = "fake_doubao"

        def generate_reply(self, **_: object) -> InteractionDraft:
            return InteractionDraft(
                reply_text="是啊，消防车真挺帅。那消防车现在要去做什么呀？",
                acknowledged_child_point="消防车",
                followup_question="那消防车现在要去做什么呀？",
            )

    generator = MinimalInteractionGenerator(provider=FakeProvider(), provider_mode="auto")
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "llm_provider"
    assert interaction_generation.followup_question == "那消防车现在要去做什么呀？"
    assert interaction_generation.reply_text == "是啊，消防车真挺帅。那消防车现在要去做什么呀？"


def test_interaction_generator_defaults_provider_mode_to_qwen() -> None:
    class FakeProvider:
        pass

    generator = MinimalInteractionGenerator(provider=FakeProvider())

    assert generator.provider_mode == "qwen"


def test_interaction_generator_builds_structured_context_for_keep_trying() -> None:
    resolver = RuleFirstSignalResolver()

    class CapturingProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.interaction_context_seen = None

        def generate_reply(self, **kwargs: object) -> InteractionDraft:
            self.interaction_context_seen = kwargs["interaction_context"]
            return InteractionDraft(
                reply_text="是啊，消防车很帅。那它现在要去帮谁呀？",
                acknowledged_child_point="消防车",
                followup_question="那它现在要去帮谁呀？",
            )

    provider = CapturingProvider()
    generator = MinimalInteractionGenerator(provider=provider, provider_mode="auto")
    task_context = build_task_context()
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=task_context,
    )

    interaction_context, interaction_generation = generator.generate_with_context(
        child_input_text="消防车真帅",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    assert provider.interaction_context_seen == interaction_context
    assert interaction_context.child_input_text == "消防车真帅"
    assert interaction_context.normalized_child_text == "消防车真帅"
    assert interaction_context.task_signal == "keep_trying"
    assert interaction_context.engagement_state == "playful"
    assert interaction_context.matched_completion_points == ()
    assert interaction_context.missing_completion_points == ("救火",)
    assert interaction_context.scene_style == "playful_companion"
    assert interaction_context.redirect_strength == "soft"
    assert interaction_context.expected_child_action == "说出消防车要去救火"
    assert interaction_context.recent_turn_summary is not None
    assert "消防车" in interaction_context.recent_turn_summary
    assert "救火" in interaction_context.interaction_goal
    assert interaction_context.preferred_followup_question is not None
    assert interaction_generation.followup_question == interaction_context.preferred_followup_question
    assert interaction_generation.reply_text == f"是啊，消防车很帅。{interaction_context.preferred_followup_question}"
    assert "帮谁呀" not in interaction_generation.reply_text
    assert interaction_generation.generation_source == "llm_provider"


def test_interaction_generator_retries_keep_trying_with_wider_timeout_and_keeps_provider_reply() -> None:
    resolver = RuleFirstSignalResolver()

    class RetryOnceProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.request_options_seen: list[object] = []

        def generate_reply(self, **kwargs: object) -> InteractionDraft:
            self.request_options_seen.append(kwargs["request_options"])
            if len(self.request_options_seen) == 1:
                raise socket.timeout("first attempt timed out")
            return InteractionDraft(
                reply_text="是啊，消防车很帅。那它现在要去帮谁呀？",
                acknowledged_child_point="消防车",
                followup_question="那它现在要去帮谁呀？",
            )

    provider = RetryOnceProvider()
    generator = MinimalInteractionGenerator(
        provider=provider,
        provider_mode="auto",
        keep_trying_timeout_seconds=1.5,
        keep_trying_retry_timeout_seconds=5.5,
        fast_path_timeout_seconds=0.8,
    )
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.task_signal == "keep_trying"
    assert interaction_generation.generation_source == "llm_provider"
    assert interaction_generation.reply_text.endswith(interaction_generation.followup_question or "")
    assert "帮谁呀" not in interaction_generation.reply_text
    assert len(provider.request_options_seen) == 2
    assert provider.request_options_seen[0].timeout_seconds == 1.5
    assert provider.request_options_seen[0].prompt_variant == "default"
    assert provider.request_options_seen[1].timeout_seconds == 5.5
    assert provider.request_options_seen[1].prompt_variant == "relaxed_keep_trying"
    assert "超时" in (provider.request_options_seen[1].retry_hint or "")


def test_interaction_generator_falls_back_when_provider_reply_is_mechanical() -> None:
    resolver = RuleFirstSignalResolver()

    class FakeProvider:
        provider_name = "fake_doubao"

        def generate_reply(self, **_: object) -> InteractionDraft:
            return InteractionDraft(reply_text="你来告诉我：消防车要去救火。")

    generator = MinimalInteractionGenerator(provider=FakeProvider(), provider_mode="auto")
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "fake_doubao"
    assert "mechanical classroom wording" in (interaction_generation.fallback_reason or "")
    assert "你来告诉我" not in interaction_generation.reply_text


def test_interaction_generator_falls_back_when_provider_times_out() -> None:
    resolver = RuleFirstSignalResolver()

    class TimeoutProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.request_options_seen: list[object] = []

        def generate_reply(self, **_: object) -> InteractionDraft:
            self.request_options_seen.append(_["request_options"])
            raise socket.timeout("timed out")

    provider = TimeoutProvider()
    generator = MinimalInteractionGenerator(
        provider=provider,
        provider_mode="auto",
        keep_trying_timeout_seconds=1.2,
        keep_trying_retry_timeout_seconds=4.8,
    )
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "fake_doubao"
    assert len(provider.request_options_seen) == 2
    assert provider.request_options_seen[0].timeout_seconds == 1.2
    assert provider.request_options_seen[1].timeout_seconds == 4.8
    assert provider.request_options_seen[1].prompt_variant == "relaxed_keep_trying"
    assert "attempt 1" in (interaction_generation.fallback_reason or "")
    assert "attempt 2" in (interaction_generation.fallback_reason or "")
    assert "timeout" in (interaction_generation.fallback_reason or "").lower()
    assert "你来告诉我" not in interaction_generation.reply_text


def test_interaction_generator_can_disable_keep_trying_retry_and_fall_back_after_one_timeout() -> None:
    resolver = RuleFirstSignalResolver()

    class TimeoutProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.request_options_seen: list[object] = []

        def generate_reply(self, **kwargs: object) -> InteractionDraft:
            self.request_options_seen.append(kwargs["request_options"])
            raise socket.timeout("timed out")

    provider = TimeoutProvider()
    generator = MinimalInteractionGenerator(
        provider=provider,
        provider_mode="auto",
        keep_trying_timeout_seconds=1.2,
        keep_trying_retry_timeout_seconds=0.0,
    )
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "fake_doubao"
    assert len(provider.request_options_seen) == 1
    assert provider.request_options_seen[0].timeout_seconds == 1.2
    assert provider.request_options_seen[0].prompt_variant == "default"
    assert "attempt 1" in (interaction_generation.fallback_reason or "")
    assert "attempt 2" not in (interaction_generation.fallback_reason or "")
    assert "timeout" in (interaction_generation.fallback_reason or "").lower()


def test_ark_provider_prompt_uses_grouped_interaction_context() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = build_task_context()
    session_memory_summary = "上一轮我们已经接住了孩子提到消防车，还把话题往救火上拉了一次。"
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=task_context,
    )
    interaction_context, _ = generator.generate_with_context(
        child_input_text="消防车真帅",
        current_task=task_context,
        signal_resolution=signal_resolution,
        session_memory_summary=session_memory_summary,
    )

    user_prompt = ArkDoubaoInteractionProvider._build_user_prompt(
        interaction_context=interaction_context,
        request_options=None,
    )
    system_prompt = ArkDoubaoInteractionProvider._build_system_prompt(
        interaction_context=interaction_context,
        request_options=None,
    )
    payload = json.loads(user_prompt)

    assert set(payload) == {"task", "child", "reply"}
    assert payload["task"]["signal"] == "keep_trying"
    assert payload["task"]["goal"] == interaction_context.interaction_goal
    assert payload["task"]["task_goal"] == interaction_context.task_goal
    assert payload["task"]["completion_points"][0]["label"] == "救火"
    assert payload["task"]["completion_points"][0]["keywords"] == ["救火", "灭火"]
    assert payload["task"]["expected_action"] == interaction_context.expected_child_action
    assert payload["task"]["scene_style"] == interaction_context.scene_style
    assert payload["task"]["scene_context"] == interaction_context.scene_context
    assert payload["task"]["session_memory"] == session_memory_summary
    assert payload["task"]["need"] == ["救火"]
    assert payload["child"]["said"] == "消防车真帅"
    assert payload["child"]["normalized"] == interaction_context.normalized_child_text
    assert payload["child"]["state"] == "playful"
    assert payload["child"]["summary"] == interaction_context.recent_turn_summary
    assert payload["reply"]["ack"] == "消防车"
    assert payload["reply"]["ask"] == interaction_context.preferred_followup_question
    assert "故事背景" in system_prompt
    assert "原始任务目标" in system_prompt
    assert "任务完成点" in system_prompt
    assert "连贯的小故事" in system_prompt
    assert "会话记忆" in system_prompt
    assert "reply_text 优先由模型自由生成，不要套固定句式；每次尽量换说法。" in system_prompt
    assert "除开场外，reply_text 默认控制在 1 到 2 句、35 到 70 个汉字；不要越说越长。" in system_prompt
    assert session_memory_summary in system_prompt
    assert "不要把“背景上有什么/场景里有什么”当成默认提问方向" in system_prompt
    assert "优先围绕任务完成点提问" in system_prompt
    assert "task.signal" not in system_prompt
    assert len(system_prompt) + len(user_prompt) < 1600


def test_interaction_generator_carries_session_memory_into_context() -> None:
    resolver = RuleFirstSignalResolver()

    class CapturingProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.seen_context = None

        def generate_reply(self, **kwargs: object) -> InteractionDraft:
            self.seen_context = kwargs["interaction_context"]
            return InteractionDraft(
                reply_text="是啊，消防车很帅。那它现在要去帮谁呀？",
                acknowledged_child_point="消防车",
                followup_question="那它现在要去帮谁呀？",
            )

    provider = CapturingProvider()
    generator = MinimalInteractionGenerator(provider=provider, provider_mode="auto")
    task_context = build_task_context()
    session_memory_summary = "上一轮孩子已经说过消防车要去救火，我们现在继续往下带。"
    signal_resolution = resolver.resolve(
        child_input_text="消防车真帅",
        current_task=task_context,
    )

    interaction_context, interaction_generation = generator.generate_with_context(
        child_input_text="消防车真帅",
        current_task=task_context,
        signal_resolution=signal_resolution,
        session_memory_summary=session_memory_summary,
    )

    assert provider.seen_context == interaction_context
    assert interaction_context.session_memory == session_memory_summary
    assert interaction_context.task_signal == "keep_trying"
    assert interaction_generation.generation_source == "llm_provider"


@pytest.mark.parametrize(
    ("child_text", "expected_signal"),
    (
        ("我要开消防车去灭火", "task_completed"),
        ("不玩了，我们先停一下", "end_session"),
    ),
)
def test_interaction_generator_keeps_fast_single_attempt_for_fast_paths(
    child_text: str,
    expected_signal: str,
) -> None:
    resolver = RuleFirstSignalResolver()

    class TimeoutProvider:
        provider_name = "fake_doubao"

        def __init__(self) -> None:
            self.request_options_seen: list[object] = []

        def generate_reply(self, **kwargs: object) -> InteractionDraft:
            self.request_options_seen.append(kwargs["request_options"])
            raise socket.timeout("timed out")

    provider = TimeoutProvider()
    generator = MinimalInteractionGenerator(
        provider=provider,
        provider_mode="auto",
        keep_trying_timeout_seconds=4.0,
        keep_trying_retry_timeout_seconds=7.0,
        fast_path_timeout_seconds=0.9,
    )
    signal_resolution = resolver.resolve(
        child_input_text=child_text,
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text=child_text,
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert signal_resolution.task_signal == expected_signal
    assert interaction_generation.generation_source == "template_fallback"
    assert len(provider.request_options_seen) == 1
    assert provider.request_options_seen[0].timeout_seconds == 0.9
    assert provider.request_options_seen[0].prompt_variant == "fast_path"


def test_bridge_package_contains_phase6_payload() -> None:
    resolver = RuleFirstSignalResolver()
    generator = MinimalInteractionGenerator(provider_mode="template")
    task_context = build_task_context()
    signal_resolution = resolver.resolve(
        child_input_text="我还没想好",
        current_task=task_context,
    )
    interaction_context, interaction_generation = generator.generate_with_context(
        child_input_text="我还没想好",
        current_task=task_context,
        signal_resolution=signal_resolution,
    )

    package = build_phase7_bridge_package(
        child_input_text="我还没想好",
        current_task=task_context,
        signal_resolution=signal_resolution,
        interaction_generation=interaction_generation,
        interaction_context=interaction_context,
        session_id="ses_demo_001",
    )

    payload = package.to_dict()

    assert payload["phase6_turn_payload"]["child_input_text"] == "我还没想好"
    assert payload["phase6_turn_payload"]["task_signal"] == signal_resolution.task_signal
    assert payload["phase6_turn_payload"]["assistant_reply_text"] == interaction_generation.reply_text
    assert payload["phase6_turn_payload"]["signal_reason"] == signal_resolution.reason
    assert payload["phase6_turn_payload"]["engagement_state"] == signal_resolution.engagement_state
    assert payload["phase6_turn_payload"]["interaction_mode"] == interaction_generation.interaction_mode
    assert payload["interaction_context"]["task_signal"] == signal_resolution.task_signal
    assert payload["interaction_context"]["expected_child_action"] == "说出消防车要去救火"
    assert payload["session_id"] == "ses_demo_001"


def test_cli_outputs_signal_and_interaction_json() -> None:
    script_path = ROOT_DIR / "scripts" / "run_text_prototype.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--child-text",
            "我要开消防车去救火",
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
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["signal_resolution"]["task_signal"] == "task_completed"
    assert payload["interaction_generation"]["interaction_mode"] == "celebrate_completion"
    assert payload["interaction_context"]["task_signal"] == "task_completed"
    assert payload["interaction_context"]["expected_child_action"] == "说出消防车要去救火"
    assert payload["interaction_generation"]["generation_source"] == "template_fallback"
    assert payload["phase6_turn_payload"]["task_signal"] == "task_completed"


def test_cli_parsers_default_interaction_provider_to_qwen() -> None:
    text_module = load_script_module("run_text_prototype.py")
    voice_module = load_script_module("run_voice_input.py")

    text_args = text_module.build_parser().parse_args(
        [
            "--child-text",
            "消防车真帅",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
        ]
    )
    voice_args = voice_module.build_parser().parse_args(
        [
            "--audio-file",
            "/tmp/demo.wav",
            "--task-goal",
            "让孩子说出消防车要去做什么",
            "--expected-child-action",
            "说出消防车要去救火",
        ]
    )

    assert text_args.interaction_provider == "qwen"
    assert voice_args.interaction_provider == "qwen"
    assert voice_args.runtime_mode == "realtime"
