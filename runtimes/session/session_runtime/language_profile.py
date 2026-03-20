from __future__ import annotations

import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
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
    DEFAULT_QWEN_MODEL,
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    OpenAICompatibleConfigError,
    OpenAICompatibleRequestError,
)
from runtime.env_loader import build_runtime_env  # type: ignore[import-not-found]


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_NORMALIZE_COMPARE_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")
CONTROL_MARKERS = (
    "开始玩消防站",
    "开始玩消防车",
    "开始玩",
    "重新开始",
    "再来一次",
    "从头开始",
    "音量",
    "静音",
    "恢复音量",
    "亮度",
)
META_PROMPT_MARKERS = (
    "请你以",
    "请以",
    "用富有感情",
    "依依不舍",
    "结束这场对话",
    "未来头",
    "提示词",
    "输出",
    "模型",
    "千问",
    "豆包",
    "ai",
    "AI",
)
ADULT_DEBUG_MARKERS = (
    "为什么还要",
    "你自己",
    "不就完了吗",
    "重新刷了一遍",
    "这玩具",
    "不是，我",
)


def _safe_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_token(token: str) -> str:
    return token.strip()


def _normalize_for_compare(text: str) -> str:
    return _NORMALIZE_COMPARE_RE.sub("", text or "").strip().lower()


def _assistant_reply_text(turn_payload: Mapping[str, Any] | None) -> str:
    if not isinstance(turn_payload, Mapping):
        return ""
    assistant_reply = turn_payload.get("assistant_reply")
    if not isinstance(assistant_reply, Mapping):
        return ""
    return _safe_text(assistant_reply.get("reply_text"))


def _looks_like_meta_prompt(text: str) -> bool:
    normalized = _safe_text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(marker in normalized for marker in CONTROL_MARKERS):
        return True
    if any(marker.lower() in lowered for marker in META_PROMPT_MARKERS):
        return True
    return len(normalized) >= 20 and ("请你" in normalized or "对话" in normalized)


def _looks_like_adult_debug_utterance(text: str) -> bool:
    normalized = _safe_text(text)
    if not normalized:
        return False
    if len(normalized) < 12:
        return False
    if any(marker in normalized for marker in ADULT_DEBUG_MARKERS):
        return True
    if ("？" in normalized or "?" in normalized) and any(
        marker in normalized for marker in ("你", "为什么", "自己", "吗", "呢")
    ):
        return True
    return False


def _looks_like_assistant_echo(text: str, previous_assistant_text: str) -> bool:
    normalized_child = _normalize_for_compare(text)
    normalized_assistant = _normalize_for_compare(previous_assistant_text)
    if len(normalized_child) < 6 or len(normalized_assistant) < 6:
        return False
    if normalized_child in normalized_assistant or normalized_assistant in normalized_child:
        return True
    overlap_ratio = SequenceMatcher(None, normalized_child, normalized_assistant).ratio()
    return overlap_ratio >= 0.78


def _collect_child_utterances(session_snapshot: Mapping[str, Any]) -> list[str]:
    turns = session_snapshot.get("turns")
    if not isinstance(turns, Sequence):
        return []
    utterances: list[str] = []
    previous_assistant_text = ""
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        child_text = _safe_text(turn.get("child_input_text"))
        current_assistant_text = _assistant_reply_text(turn)
        if child_text:
            if (
                not _looks_like_meta_prompt(child_text)
                and not _looks_like_adult_debug_utterance(child_text)
                and not _looks_like_assistant_echo(
                child_text,
                previous_assistant_text,
                )
            ):
                utterances.append(child_text)
        previous_assistant_text = current_assistant_text
    return utterances


def _extract_tokens(texts: Sequence[str]) -> list[str]:
    tokens: list[str] = []
    stopwords = {
        "开始玩消防站",
        "我觉得",
        "这个东西",
        "这个",
        "那个",
        "然后",
        "就是",
        "一下",
        "一个",
    }
    for text in texts:
        for token in TOKEN_RE.findall(text):
            normalized = _normalize_token(token)
            if len(normalized) < 2 or normalized in stopwords:
                continue
            tokens.append(normalized)
    return tokens


def build_language_profile_payload(session_snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    recent_sessions = list(session_snapshots)[:10]
    utterances: list[str] = []
    completed_count = 0
    active_count = 0
    stuck_points: list[str] = []
    for session_snapshot in recent_sessions:
        session = session_snapshot.get("session") if isinstance(session_snapshot, Mapping) else None
        session_payload = session if isinstance(session, Mapping) else session_snapshot
        if _safe_text(session_payload.get("status")) == "ended":
            completed_count += 1
        else:
            active_count += 1
        current_task = session_snapshot.get("current_task") if isinstance(session_snapshot, Mapping) else None
        stuck_point = _safe_text(
            (current_task.get("parent_label") or current_task.get("name"))
            if isinstance(current_task, Mapping)
            else session_payload.get("stuck_point")
        )
        if stuck_point:
            stuck_points.append(stuck_point)
        utterances.extend(_collect_child_utterances(session_snapshot))

    tokens = _extract_tokens(utterances)
    token_counts: dict[str, int] = {}
    for token in tokens:
        token_counts[token] = token_counts.get(token, 0) + 1
    top_tokens = sorted(token_counts.items(), key=lambda item: (-item[1], item[0]))[:8]

    highlights: list[str] = []
    if any("消防车" in text for text in utterances):
        highlights.append("经常主动提到消防车，说明对角色和行动有持续关注。")
    if any(("救火" in text) or ("灭火" in text) for text in utterances):
        highlights.append("会把表达往行动结果上收，已经有“去做什么”的任务意识。")
    if any(("大火" in text) or ("小火" in text) or ("左边" in text) or ("右边" in text) for text in utterances):
        highlights.append("开始出现更具体的位置和程度判断，语言不只停留在泛泛描述。")
    if not highlights:
        highlights.append("最近 10 轮里，孩子的表达越来越贴近任务动作，说明口语组织在变稳。")

    profile_summary = (
        f"最近 {len(recent_sessions)} 个 session 里，过滤后保留了 {len(utterances)} 句孩子自然表达。"
        f"其中已结束 {completed_count} 轮、仍在进行 {active_count} 轮。"
        f"高频词主要集中在：{('、'.join(token for token, _ in top_tokens[:5])) or '消防站任务'}。"
        f"整体看，孩子已经会把注意力放到角色、动作和火情判断上，语言正在从零散反应转向更完整的任务表达。"
    )

    return {
        "session_window": len(recent_sessions),
        "utterance_count": len(utterances),
        "completed_session_count": completed_count,
        "active_session_count": active_count,
        "top_tokens": [{"token": token, "count": count} for token, count in top_tokens],
        "stuck_points": stuck_points[:5],
        "recent_utterances": utterances[-10:],
        "highlights": highlights,
        "profile_summary": profile_summary,
    }


@dataclass
class LanguageProfileSnapshot:
    profile_summary: str
    poem_text: str
    highlights: list[str]
    recent_utterances: list[str]
    top_tokens: list[dict[str, Any]]
    session_window: int
    utterance_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_summary": self.profile_summary,
            "poem_text": self.poem_text,
            "highlights": self.highlights,
            "recent_utterances": self.recent_utterances,
            "top_tokens": self.top_tokens,
            "session_window": self.session_window,
            "utterance_count": self.utterance_count,
        }


class SessionLanguageProfileService:
    def __init__(self) -> None:
        self._cached_signature = ""
        self._cached_snapshot: LanguageProfileSnapshot | None = None
        self._client: OpenAICompatibleClient | None = None
        self._client_error = False

    def build_snapshot(self, session_snapshots: Sequence[Mapping[str, Any]]) -> LanguageProfileSnapshot:
        payload = build_language_profile_payload(session_snapshots)
        signature_source = [
            _safe_text(
                (session.get("session") or {}).get("session_id")
                if isinstance(session.get("session"), Mapping)
                else session.get("session_id")
            )
            for session in list(session_snapshots)[:10]
            if _safe_text(
                (session.get("session") or {}).get("session_id")
                if isinstance(session.get("session"), Mapping)
                else session.get("session_id")
            )
        ]
        signature = hashlib.sha1(
            json.dumps(signature_source, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if self._cached_snapshot is not None and self._cached_signature == signature:
            return self._cached_snapshot

        poem_text = self._build_poem(payload)
        snapshot = LanguageProfileSnapshot(
            profile_summary=payload["profile_summary"],
            poem_text=poem_text,
            highlights=list(payload["highlights"]),
            recent_utterances=list(payload["recent_utterances"]),
            top_tokens=list(payload["top_tokens"]),
            session_window=int(payload["session_window"]),
            utterance_count=int(payload["utterance_count"]),
        )
        self._cached_signature = signature
        self._cached_snapshot = snapshot
        return snapshot

    def _build_poem(self, payload: Mapping[str, Any]) -> str:
        client = self._get_client()
        if client is None:
            return self._fallback_poem(payload)

        prompt = {
            "session_window": payload["session_window"],
            "utterance_count": payload["utterance_count"],
            "top_tokens": payload["top_tokens"],
            "highlights": payload["highlights"],
            "recent_utterances": payload["recent_utterances"][-5:],
            "instruction": (
                "请根据这些儿童最近10个session的语言表现，写成一阙宋词小令风格的短词。"
                "要求文雅、温柔、正向、含蓄，篇幅比绝句略长，约6到10句。"
                "不要现代口语，不要引号，不要白话点评，不要老师评语口吻，不要提缺点，不要神化拔高。"
                "尽量有词牌气息，但不必强行标注词牌名。输出只要词句本身。"
            ),
        }
        try:
            result = client.create_chat_completion(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是儿童语言观察助手。"
                            "请把真实的语言成长表现写成宋词小令气质的中文短词。"
                            "不要散文化，不要解释，不要夹杂现代对白。"
                            "整体基调要温和赞许，措辞要古雅婉转。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ]
            ).content_text.strip()
            return result or self._fallback_poem(payload)
        except (OpenAICompatibleConfigError, OpenAICompatibleRequestError, ValueError):
            return self._fallback_poem(payload)
        except Exception:
            return self._fallback_poem(payload)

    def _get_client(self) -> OpenAICompatibleClient | None:
        if self._client_error:
            return None
        if self._client is not None:
            return self._client
        try:
            env = build_runtime_env(PHASE5_ROOT_DIR / ".env.local")
            config = OpenAICompatibleConfig.from_env(
                env,
                provider_label="Session language profile",
                api_key_env_keys=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
                model_env_keys=("QWEN_MODEL", "DASHSCOPE_MODEL"),
                base_url_env_keys=("QWEN_BASE_URL", "DASHSCOPE_BASE_URL"),
                request_url_env_keys=("QWEN_REQUEST_URL", "DASHSCOPE_REQUEST_URL", "DASHSCOPE_CHAT_COMPLETIONS_URL"),
                timeout_env_keys=(),
                max_tokens_env_keys=(),
                temperature_env_keys=(),
                default_base_url=DEFAULT_QWEN_BASE_URL,
                default_model=DEFAULT_QWEN_MODEL,
                default_timeout_seconds=6.0,
                default_max_tokens=180,
                default_temperature=0.6,
            )
            self._client = OpenAICompatibleClient(config)
            return self._client
        except Exception:
            self._client_error = True
            return None

    @staticmethod
    def _fallback_poem(payload: Mapping[str, Any]) -> str:
        return (
            "小语穿春昼，\n"
            "轻轻点火痕。\n"
            "才分深与浅，又辨远和近。\n"
            "一句一句，渐有章法；\n"
            "一声一念，自见天真。\n"
            "且看新枝抽翠，也照童心。"
        )
