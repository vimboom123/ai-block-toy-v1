from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib import error

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from input_understanding import (  # noqa: E402
    MinimalInteractionGenerator,
    QwenSemanticSignalResolver,
    QwenTaskSignalResolver,
    RuleFirstSignalResolver,
    SignalResolution,
    SignalResolverLLMStub,
    TaskContext,
)
from input_understanding.models import CompletionPoint  # noqa: E402
from input_understanding.interaction_provider import (  # noqa: E402
    BaseInteractionProvider,
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    InteractionProviderError,
    QwenInteractionProvider,
)
from input_understanding.task_oral_hints import build_task_oral_hints  # noqa: E402
from phase6_bridge import Phase6BridgeError, Phase6SessionClient, Phase6TurnPayload  # noqa: E402
from runtime_pipeline import run_phase7_turn_pipeline  # noqa: E402


def build_task_context() -> TaskContext:
    return TaskContext(
        task_id="fs_004",
        task_name="消防车出动",
        task_goal="让孩子说出消防车要去做什么",
        expected_child_action="说出消防车要去救火",
        completion_points=(
            CompletionPoint.parse("救火:救火,灭火"),
        ),
        completion_match_mode="any",
    )


def load_script_module(script_name: str):
    script_path = ROOT_DIR / "scripts" / script_name
    module_name = f"phase7_closure_{script_name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("child_text", "expected_signal", "expected_mode", "expected_partial_credit"),
    (
        ("消防车真帅", "keep_trying", "playful_probe", False),
        ("去帮忙", "keep_trying", "acknowledge_and_redirect", True),
        ("我要开消防车去灭火", "task_completed", "celebrate_completion", False),
        ("我不想玩了", "end_session", "graceful_end", False),
        ("不知道", "keep_trying", "emotional_soothing", False),
    ),
)
def test_pipeline_covers_typical_text_cases(
    child_text: str,
    expected_signal: str,
    expected_mode: str,
    expected_partial_credit: bool,
) -> None:
    package = run_phase7_turn_pipeline(
        child_input_text=child_text,
        current_task=build_task_context(),
        interaction_provider="template",
    )

    assert package.signal_resolution.task_signal == expected_signal
    assert package.signal_resolution.partial_credit is expected_partial_credit
    assert package.interaction_generation.interaction_mode == expected_mode
    assert package.interaction_context is not None
    assert package.interaction_context.partial_credit is expected_partial_credit
    assert package.phase6_turn_payload.task_signal == expected_signal

    if child_text == "去帮忙":
        assert "关键完成点" in package.signal_resolution.reason
        assert "你刚刚提到去帮忙" not in package.interaction_generation.reply_text
        assert "去帮忙这个方向" in (package.interaction_context.interaction_goal or "")
    elif child_text == "我要开消防车去灭火":
        assert "下一步再看看" not in package.interaction_generation.reply_text
    elif child_text == "我不想玩了":
        assert package.interaction_generation.followup_question is None
        assert "继续追问" not in (package.interaction_context.interaction_goal or "")


def test_qwen_missing_key_falls_back_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env_key in (
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_MODEL",
        "DASHSCOPE_MODEL",
        "QWEN_BASE_URL",
        "DASHSCOPE_BASE_URL",
        "QWEN_REQUEST_URL",
        "DASHSCOPE_REQUEST_URL",
    ):
        monkeypatch.delenv(env_key, raising=False)

    (tmp_path / ".env.local").write_text("", encoding="utf-8")
    provider = QwenInteractionProvider(root_dir=tmp_path)
    generator = MinimalInteractionGenerator(provider=provider, provider_mode="qwen")
    signal_resolution = RuleFirstSignalResolver().resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "qwen"
    assert "Missing Qwen API key" in (interaction_generation.fallback_reason or "")
    assert "attempt 1" in (interaction_generation.fallback_reason or "")
    assert "attempt 2" not in (interaction_generation.fallback_reason or "")


def test_qwen_timeout_retries_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env_key in (
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_MODEL",
        "DASHSCOPE_MODEL",
    ):
        monkeypatch.delenv(env_key, raising=False)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            (
                "QWEN_API_KEY=test-key",
                "QWEN_MODEL=qwen-test",
            )
        ),
        encoding="utf-8",
    )

    timeout_seconds_seen: list[float] = []

    def fake_create_chat_completion(self: OpenAICompatibleClient, messages: list[dict[str, str]]):
        del messages
        timeout_seconds_seen.append(self.config.timeout_seconds)
        raise socket.timeout("qwen timed out")

    monkeypatch.setattr(
        OpenAICompatibleClient,
        "create_chat_completion",
        fake_create_chat_completion,
    )

    provider = QwenInteractionProvider(root_dir=tmp_path)
    generator = MinimalInteractionGenerator(
        provider=provider,
        provider_mode="qwen",
        keep_trying_timeout_seconds=1.1,
        keep_trying_retry_timeout_seconds=3.4,
    )
    signal_resolution = RuleFirstSignalResolver().resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert timeout_seconds_seen == [1.1, 3.4]
    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "qwen"
    assert "attempt 1" in (interaction_generation.fallback_reason or "")
    assert "attempt 2" in (interaction_generation.fallback_reason or "")
    assert "timeout" in (interaction_generation.fallback_reason or "").lower()


def test_openai_compatible_client_omits_max_tokens_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payloads: list[dict[str, object]] = []

    class FakeResponse:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"reply_text":"好","acknowledged_child_point":"消防车","followup_question":"接下来呢？"}'
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del timeout
        captured_payloads.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr("input_understanding.interaction_provider.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        OpenAICompatibleConfig(
            api_key="test-key",
            model="qwen-plus",
            request_url="https://example.com/chat/completions",
            max_tokens=None,
        )
    )

    result = client.create_chat_completion(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "消防车"},
        ]
    )

    assert result.content_text
    assert captured_payloads
    assert "max_tokens" not in captured_payloads[0]


def test_qwen_semantic_signal_resolver_can_promote_equivalent_phrase_to_completed() -> None:
    class FakeClient:
        def create_chat_completion(self, messages: list[dict[str, str]]):  # type: ignore[no-untyped-def]
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
            return type(
                "FakeChatResult",
                (),
                {
                    "content_text": json.dumps(
                        {
                            "task_signal": "task_completed",
                            "matched_completion_points": ["救火"],
                            "confidence": 0.83,
                            "reason": "孩子已经表达出消防车是去把火扑灭，语义上等于去救火。",
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    resolver = RuleFirstSignalResolver(
        llm_stub=QwenSemanticSignalResolver(client=FakeClient()),
    )
    task_context = build_task_context()

    signal_resolution = resolver.resolve(
        child_input_text="消防车要去把火扑灭",
        current_task=task_context,
    )

    assert signal_resolution.task_signal == "task_completed"
    assert signal_resolution.matched_completion_points == ("救火",)
    assert ("语义" in signal_resolution.reason) or ("已命中当前任务完成点" in signal_resolution.reason)


def test_qwen_task_signal_resolver_can_classify_without_keyword_rules() -> None:
    class FakeClient:
        def create_chat_completion(self, messages: list[dict[str, str]]):  # type: ignore[no-untyped-def]
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
            return type(
                "FakeChatResult",
                (),
                {
                    "content_text": json.dumps(
                        {
                            "task_signal": "task_completed",
                            "matched_completion_points": ["救火"],
                            "partial_credit": False,
                            "engagement_state": "engaged",
                            "confidence": 0.88,
                            "reason": "孩子虽然没说救火两个字，但已经明确表达要把火扑灭。",
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    resolver = QwenTaskSignalResolver(client=FakeClient())
    signal_resolution = resolver.resolve(
        child_input_text="消防车要把火扑灭",
        current_task=build_task_context(),
    )

    assert signal_resolution is not None
    assert signal_resolution.task_signal == "task_completed"
    assert signal_resolution.matched_completion_points == ("救火",)
    assert signal_resolution.engagement_state == "engaged"


def test_pipeline_uses_semantic_signal_resolver_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime_pipeline as runtime_pipeline_module

    class CompleteStub(SignalResolverLLMStub):
        def resolve(
            self,
            *,
            child_input_text: str,
            normalized_child_text: str,
            current_task: TaskContext,
            rule_candidate: SignalResolution | None,
        ) -> SignalResolution | None:
            del child_input_text, normalized_child_text
            assert current_task.task_id == "fs_004"
            assert rule_candidate is not None
            return SignalResolution(
                task_signal="task_completed",
                confidence=0.81,
                reason="AI 语义判定：孩子已经说明消防车要去把火扑灭。",
                fallback_needed=False,
                normalized_child_text=rule_candidate.normalized_child_text,
                partial_credit=False,
                matched_completion_points=("救火",),
                missing_completion_points=(),
                engagement_state=rule_candidate.engagement_state,
            )

    monkeypatch.setattr(runtime_pipeline_module, "build_signal_resolver_llm", lambda provider_mode: CompleteStub())

    package = run_phase7_turn_pipeline(
        child_input_text="消防车要去把火扑灭",
        current_task=build_task_context(),
        interaction_provider="template",
    )

    assert package.signal_resolution.task_signal == "task_completed"
    assert package.interaction_generation.interaction_mode == "celebrate_completion"
    assert package.phase6_turn_payload.task_signal == "task_completed"


def test_pipeline_can_merge_ai_signal_and_reply_into_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime_pipeline as runtime_pipeline_module

    current_task = TaskContext(
        task_id="fs_001",
        task_name="场景识别",
        task_goal="说出哪些是能动的，哪些只是画在墙上的",
        expected_child_action="区分可操作元素与背景元素",
        completion_points=(CompletionPoint.parse("背景可动:背景,墙上,固定,不能动"),),
    )
    next_task_hint = TaskContext(
        task_id="fs_002",
        task_name="接警判断",
        task_goal="在指挥台确认这次求助来自哪里",
        expected_child_action="说出内部火警或外部场景火警",
        completion_points=(CompletionPoint.parse("接警地点:内部,外部,里面,外面"),),
    )

    class FakeUnifiedProvider:
        def generate_turn(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["child_input_text"] == "墙上的画不能动"
            assert kwargs["current_task"].task_id == "fs_001"
            assert kwargs["next_task_hint"].task_id == "fs_002"
            return SimpleNamespace(
                task_signal="task_completed",
                matched_completion_points=("背景可动",),
                partial_credit=False,
                engagement_state="engaged",
                confidence=0.9,
                reason="孩子已经说明墙上的画不能动，当前任务完成。",
                reply_text="对啦，墙上的画不能动。那这次火警是在里面还是外面呀？",
                acknowledged_child_point="背景可动",
                followup_question="那这次火警是在里面还是外面呀？",
                provider_name="qwen_unified",
            )

    class FailSignalResolver:
        def resolve(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("split signal resolver should not run when unified turn succeeds")

    class FailRuleResolver:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def resolve(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("rule resolver should not run when unified turn succeeds")

    monkeypatch.setattr(runtime_pipeline_module, "build_unified_turn_provider", lambda provider_mode: FakeUnifiedProvider())
    monkeypatch.setattr(runtime_pipeline_module, "QwenTaskSignalResolver", lambda: FailSignalResolver())
    monkeypatch.setattr(runtime_pipeline_module, "RuleFirstSignalResolver", lambda llm_stub: FailRuleResolver())

    package = run_phase7_turn_pipeline(
        child_input_text="墙上的画不能动",
        current_task=current_task,
        interaction_provider="qwen",
        next_task_hint=next_task_hint,
    )

    assert package.signal_resolution.task_signal == "task_completed"
    assert package.signal_resolution.matched_completion_points == ("背景可动",)
    assert package.interaction_generation.provider_name == "qwen_unified"
    assert "里面还是外面" in package.interaction_generation.reply_text
    assert package.phase6_turn_payload.task_signal == "task_completed"


def test_pipeline_does_not_fire_second_ai_request_when_unified_provider_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime_pipeline as runtime_pipeline_module

    class FailingUnifiedProvider:
        def generate_turn(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            raise InteractionProviderError("Qwen total timeout after 1.2s", retryable=True)

    class FailSignalResolver:
        def resolve(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("split AI signal resolver should not run after unified provider failure")

    monkeypatch.setattr(runtime_pipeline_module, "build_unified_turn_provider", lambda provider_mode: FailingUnifiedProvider())
    monkeypatch.setattr(runtime_pipeline_module, "QwenTaskSignalResolver", lambda: FailSignalResolver())

    package = run_phase7_turn_pipeline(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        interaction_provider="qwen",
    )

    assert package.signal_resolution.task_signal == "keep_trying"
    assert package.interaction_generation.generation_source == "template_fallback"


@pytest.mark.parametrize(
    ("task_id", "expected_phrase"),
    (
        ("fs_001", "墙上的画"),
        ("fs_002", "里面"),
        ("fs_003", "消防车"),
        ("fs_004", "火很大"),
        ("fs_005", "去救火"),
        ("fs_006", "刚才先怎样再怎样"),
    ),
)
def test_fire_station_oral_hints_cover_each_task(task_id: str, expected_phrase: str) -> None:
    task_context = TaskContext(
        task_id=task_id,
        task_name="测试任务",
        task_goal="测试",
        expected_child_action="测试",
        completion_points=(CompletionPoint.parse("测试点:测试"),),
    )

    hints = build_task_oral_hints(task_context)

    assert hints
    assert any(expected_phrase in hint for hint in hints)


@pytest.mark.parametrize(
    ("response_text", "expected_error"),
    (
        ("not json at all", "JSON decode error"),
        ('{"acknowledged_child_point":"消防车"}', "missing reply_text"),
    ),
)
def test_provider_payload_errors_fall_back_to_template(
    response_text: str,
    expected_error: str,
) -> None:
    class BrokenPayloadProvider(BaseInteractionProvider):
        provider_name = "broken_payload"
        provider_label = "BrokenPayload"

        def __init__(self, payload_text: str):
            self.payload_text = payload_text

        def _request_model_text(self, **_: object) -> str:
            return self.payload_text

    generator = MinimalInteractionGenerator(
        provider=BrokenPayloadProvider(response_text),
        provider_mode="auto",
    )
    signal_resolution = RuleFirstSignalResolver().resolve(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
    )

    interaction_generation = generator.generate(
        child_input_text="消防车真帅",
        current_task=build_task_context(),
        signal_resolution=signal_resolution,
    )

    assert interaction_generation.generation_source == "template_fallback"
    assert interaction_generation.provider_name == "broken_payload"
    assert expected_error in (interaction_generation.fallback_reason or "")


@pytest.mark.parametrize(
    ("status_code", "body_text"),
    (
        (404, '{"error":"session not found"}'),
        (500, '{"error":"internal boom"}'),
    ),
)
def test_phase6_session_client_wraps_http_failures(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body_text: str,
) -> None:
    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del timeout
        raise error.HTTPError(
            req.full_url,
            status_code,
            "bridge failed",
            hdrs=None,
            fp=io.BytesIO(body_text.encode("utf-8")),
        )

    monkeypatch.setattr("phase6_bridge.client.request.urlopen", fake_urlopen)
    client = Phase6SessionClient("http://127.0.0.1:4183/api/session-runtime")

    with pytest.raises(Phase6BridgeError, match=f"HTTP {status_code}"):
        client.submit_turn(
            session_id="ses_demo_001",
            payload=Phase6TurnPayload(child_input_text="消防车真帅", task_signal="keep_trying"),
        )


@pytest.mark.parametrize(
    ("raised_exc", "expected_message"),
    (
        (socket.timeout("timed out"), "timed out"),
        (error.URLError("connection refused"), "connection refused"),
    ),
)
def test_phase6_session_client_wraps_timeout_and_connection_failures(
    monkeypatch: pytest.MonkeyPatch,
    raised_exc: BaseException,
    expected_message: str,
) -> None:
    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del req, timeout
        raise raised_exc

    monkeypatch.setattr("phase6_bridge.client.request.urlopen", fake_urlopen)
    client = Phase6SessionClient("http://127.0.0.1:4183/api/session-runtime")

    with pytest.raises(Phase6BridgeError, match=expected_message):
        client.submit_turn(
            session_id="ses_demo_001",
            payload=Phase6TurnPayload(child_input_text="消防车真帅", task_signal="keep_trying"),
        )


def test_text_cli_bridge_failure_still_returns_signal_and_interaction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script_module = load_script_module("run_text_prototype.py")

    class FailingClient:
        def __init__(self, api_base: str):
            self.api_base = api_base

        def submit_turn(self, session_id: str, payload: Phase6TurnPayload) -> dict:
            del session_id, payload
            raise Phase6BridgeError("Phase 6 bridge HTTP 404: session not found")

    monkeypatch.setattr(script_module, "Phase6SessionClient", FailingClient)

    exit_code = script_module.main(
        [
            "--child-text",
            "消防车真帅",
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
            "--submit-phase6",
            "--phase6-api-base",
            "http://127.0.0.1:4183/api/session-runtime",
            "--session-id",
            "ses_missing_001",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["signal_resolution"]["task_signal"] == "keep_trying"
    assert payload["interaction_generation"]["interaction_mode"] == "playful_probe"
    assert payload["phase6_turn_payload"]["task_signal"] == "keep_trying"
    assert payload["phase6_submit"]["ok"] is False
    assert "session not found" in payload["phase6_submit"]["error"]
