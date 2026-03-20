from __future__ import annotations

import re

from .llm_stub import SignalResolverLLMStub
from .models import (
    SignalResolution,
    TaskContext,
    completion_ratio,
    partial_completion_threshold,
)

FILLER_MARKERS = (
    "嗯",
    "呃",
    "那个",
    "这个",
    "然后",
    "就是",
    "我觉得",
    "我想想",
    "让我想想",
)
END_SESSION_MARKERS = (
    "不玩了",
    "不想玩了",
    "不要玩了",
    "结束",
    "停一下",
    "先停",
    "休息",
    "拜拜",
    "再见",
)
FRUSTRATION_MARKERS = (
    "不会",
    "不知道",
    "想不出来",
    "好难",
    "不懂",
    "算了",
    "不想说",
)
CURIOUS_MARKERS = ("为什么", "怎么", "是不是", "吗", "呢", "什么", "啥")
PLAYFUL_MARKERS = ("哇", "哈哈", "真帅", "好玩", "厉害", "好酷", "冲呀")
PARTIAL_CREDIT_MARKERS = (
    "帮忙",
    "帮人",
    "去帮",
    "救人",
    "救援",
    "去救",
    "去灭",
    "处理",
    "赶去",
    "开去",
)
PARTIAL_ACTION_MARKERS = ("帮", "救", "灭", "处理", "忙")
GO_ACTION_MARKERS = ("去", "要去", "赶去", "开去", "过去")
PUNCTUATION_RE = re.compile(r"[，。！？、,.!?;；:：\-\(\)\[\]\{\}\"'“”‘’\s]+")
COMMON_ORAL_NORMALIZATIONS = {
    "著火": "着火",
    "灭火": "救火",
    "灭货": "救火",
    "扑火": "救火",
    "扑灭": "救火",
    "灭掉": "救火",
    "消防車": "消防车",
    "飞机": "直升机",
    "飞过去": "直升机",
    "外头": "外部",
    "外边": "外部",
    "外面": "外部",
    "里头": "内部",
    "里面": "内部",
    "屋里": "内部",
    "家里": "内部",
    "接景": "接警",
    "街警": "接警",
    "接井": "接警",
    "火不大": "小火",
    "小火苗": "小火",
    "火小": "小火",
    "小伙": "小火",
    "小活": "小火",
    "火很大": "大火",
    "大火苗": "大火",
    "火大": "大火",
    "大伙": "大火",
    "大活": "大火",
    "火中等": "中火",
    "中等火": "中火",
    "中伙": "中火",
    "中活": "中火",
    "床头": "床边",
    "挂在墙上": "画在墙上",
    "贴在墙上": "画在墙上",
    "贴墙上": "画在墙上",
    "墙上的": "墙上",
    "不会动": "不能动",
    "动不了": "不能动",
    "活的": "能动",
    "活动的": "能动",
    "会跑": "能动",
    "会走": "能动",
    "屋内": "内部",
    "站里": "内部",
    "屋外": "外部",
    "站外": "外部",
    "飞机先去": "直升机",
    "飞机过去": "直升机",
    "货很大": "火很大",
    "货不大": "火不大",
    "火苗很小": "小火",
    "火苗大": "大火",
    "赶过去处理": "救火",
    "过去处理": "救火",
    "过去帮忙": "救火",
    "刚刚": "刚才",
    "归队": "回站",
    "复盘": "总结",
    "bye": "拜拜",
}
TASK_KEYWORD_ALIASES = {
    "fs_001": {
        "能动": ("活动的", "活的", "会跑", "会走", "自己会动"),
        "会动": ("活动的", "活的", "自己会动"),
        "墙上": ("墙上的", "挂在墙上", "贴在墙上", "贴墙上"),
        "画在墙上": ("挂在墙上", "贴在墙上", "贴墙上"),
        "固定": ("定住的", "钉着的", "挂着的", "贴着的"),
        "不能动": ("不会动", "动不了"),
    },
    "fs_002": {
        "内部": ("屋内", "站里", "里面头", "屋子里"),
        "外部": ("屋外", "站外", "门外头", "外场"),
    },
    "fs_003": {
        "消防车": ("消防车先去", "车先去"),
        "直升机": ("飞机先去", "飞机过去", "直升飞机"),
        "集合": ("集合出发", "快集合"),
        "出动": ("出发", "赶紧去"),
    },
    "fs_004": {
        "大火": ("火苗大", "火势大", "火挺大"),
        "小火": ("火苗小", "火势小", "一点火", "小点火"),
        "中火": ("不大不小",),
        "床边": ("床旁边", "床那边"),
    },
    "fs_005": {
        "救火": ("赶过去处理", "过去处理", "过去帮忙", "先去处理", "去现场处理"),
        "灭火": ("赶过去处理", "过去处理", "先去处理"),
    },
    "fs_006": {
        "总结": ("讲一遍", "说一遍", "回顾一下"),
        "回站": ("回去", "回消防站"),
    },
}


class RuleFirstSignalResolver:
    def __init__(self, llm_stub: SignalResolverLLMStub | None = None):
        self.llm_stub = llm_stub or SignalResolverLLMStub()

    def resolve(self, child_input_text: str, current_task: TaskContext) -> SignalResolution:
        if not isinstance(child_input_text, str):
            raise TypeError("child_input_text must be a string")

        normalized_child_text = self._normalize_display_text(child_input_text)
        normalized_for_matching = self._normalize_for_matching(normalized_child_text)
        engagement_state = self._infer_engagement_state(normalized_for_matching)

        if not normalized_for_matching:
            return SignalResolution(
                task_signal="keep_trying",
                confidence=0.99,
                reason="孩子还没给出可判断内容，先保持当前任务。",
                fallback_needed=bool(current_task.completion_points),
                normalized_child_text="(empty)",
                partial_credit=False,
                matched_completion_points=(),
                missing_completion_points=current_task.completion_point_labels(),
                engagement_state="unknown",
            )

        if self._contains_any(normalized_for_matching, END_SESSION_MARKERS):
            return SignalResolution(
                task_signal="end_session",
                confidence=0.97,
                reason="检测到明确退出或暂停意图。",
                fallback_needed=False,
                normalized_child_text=normalized_child_text,
                partial_credit=False,
                matched_completion_points=(),
                missing_completion_points=current_task.completion_point_labels(),
                engagement_state="withdrawing",
            )

        matched_completion_points = self._match_completion_points(
            normalized_for_matching=normalized_for_matching,
            current_task=current_task,
        )
        missing_completion_points = tuple(
            label
            for label in current_task.completion_point_labels()
            if label not in matched_completion_points
        )
        partial_credit = self._looks_like_partial_credit(
            normalized_child_text=normalized_child_text,
            normalized_for_matching=normalized_for_matching,
            current_task=current_task,
            matched_completion_points=matched_completion_points,
        )

        if self._is_completion_satisfied(current_task, matched_completion_points):
            completion_confidence = min(
                0.82 + 0.14 * completion_ratio(current_task, len(matched_completion_points)),
                0.98,
            )
            return SignalResolution(
                task_signal="task_completed",
                confidence=completion_confidence,
                reason=f"已命中当前任务完成点：{', '.join(matched_completion_points)}。",
                fallback_needed=False,
                normalized_child_text=normalized_child_text,
                partial_credit=False,
                matched_completion_points=matched_completion_points,
                missing_completion_points=missing_completion_points,
                engagement_state=engagement_state,
            )

        rule_candidate = self._build_keep_trying_candidate(
            normalized_child_text=normalized_child_text,
            normalized_for_matching=normalized_for_matching,
            current_task=current_task,
            matched_completion_points=matched_completion_points,
            missing_completion_points=missing_completion_points,
            engagement_state=engagement_state,
            partial_credit=partial_credit,
        )

        llm_resolution = self.llm_stub.resolve(
            child_input_text=child_input_text,
            normalized_child_text=normalized_child_text,
            current_task=current_task,
            rule_candidate=rule_candidate,
        )
        if llm_resolution is not None:
            return llm_resolution
        return rule_candidate

    def _build_keep_trying_candidate(
        self,
        *,
        normalized_child_text: str,
        normalized_for_matching: str,
        current_task: TaskContext,
        matched_completion_points: tuple[str, ...],
        missing_completion_points: tuple[str, ...],
        engagement_state: str,
        partial_credit: bool,
    ) -> SignalResolution:
        if not current_task.completion_points:
            reason = "当前 task 还没配置 completion points，规则层只能先保守 keep_trying。"
            confidence = 0.62
            fallback_needed = True
        elif matched_completion_points and len(matched_completion_points) >= partial_completion_threshold(current_task):
            reason = (
                f"已命中部分完成点：{', '.join(matched_completion_points)}，"
                f"但还没满足 {current_task.completion_match_mode} 模式要求。"
            )
            confidence = 0.76
            fallback_needed = True
        elif partial_credit:
            missing_label = "、".join(missing_completion_points) or current_task.expected_child_action
            reason = f"孩子已经说到动作大方向了，但还没说到关键完成点：{missing_label}。"
            confidence = 0.8
            fallback_needed = True
        elif self._contains_any(normalized_for_matching, FRUSTRATION_MARKERS):
            reason = "孩子表达了卡住或挫败，还没命中当前任务完成点。"
            confidence = 0.74
            fallback_needed = True
        elif engagement_state in {"curious", "playful", "engaged"}:
            reason = "孩子有互动意愿，但这句话还不能证明任务已经完成。"
            confidence = 0.78
            fallback_needed = True
        else:
            reason = "当前输入没有命中任务完成点，先保持在当前任务。"
            confidence = 0.7
            fallback_needed = True

        return SignalResolution(
            task_signal="keep_trying",
            confidence=confidence,
            reason=reason,
            fallback_needed=fallback_needed,
            normalized_child_text=normalized_child_text,
            partial_credit=partial_credit,
            matched_completion_points=matched_completion_points,
            missing_completion_points=missing_completion_points,
            engagement_state=engagement_state,
        )

    def _match_completion_points(
        self,
        *,
        normalized_for_matching: str,
        current_task: TaskContext,
    ) -> tuple[str, ...]:
        matched_labels: list[str] = []
        for completion_point in current_task.completion_points:
            for keyword in self._iter_completion_point_keywords(current_task, completion_point):
                keyword_normalized = self._normalize_for_matching(keyword)
                if keyword_normalized and keyword_normalized in normalized_for_matching:
                    matched_labels.append(completion_point.label)
                    break
        return tuple(matched_labels)

    def _iter_completion_point_keywords(
        self,
        current_task: TaskContext,
        completion_point: object,
    ) -> tuple[str, ...]:
        keywords = list(getattr(completion_point, "keywords", ()) or ())
        task_aliases = TASK_KEYWORD_ALIASES.get(current_task.task_id, {})
        extra_keywords: list[str] = []
        for keyword in keywords:
            extra_keywords.extend(task_aliases.get(keyword, ()))
        return tuple(dict.fromkeys([*keywords, *extra_keywords]))

    def _is_completion_satisfied(
        self,
        current_task: TaskContext,
        matched_completion_points: tuple[str, ...],
    ) -> bool:
        if not matched_completion_points:
            return False
        if not current_task.completion_points:
            return False
        required_count = current_task.required_completion_count()
        return len(matched_completion_points) >= required_count

    def _looks_like_partial_credit(
        self,
        *,
        normalized_child_text: str,
        normalized_for_matching: str,
        current_task: TaskContext,
        matched_completion_points: tuple[str, ...],
    ) -> bool:
        if matched_completion_points and len(matched_completion_points) < current_task.required_completion_count():
            return True
        if not current_task.completion_points or not normalized_for_matching:
            return False
        if self._contains_any(normalized_for_matching, END_SESSION_MARKERS):
            return False
        if self._contains_any(normalized_for_matching, FRUSTRATION_MARKERS):
            return False
        if self._contains_any(normalized_for_matching, CURIOUS_MARKERS):
            return False
        if self._contains_any(normalized_for_matching, PARTIAL_CREDIT_MARKERS):
            return True

        has_go_marker = self._contains_any(normalized_for_matching, GO_ACTION_MARKERS)
        has_action_marker = self._contains_any(normalized_for_matching, PARTIAL_ACTION_MARKERS)
        if has_go_marker and has_action_marker and len(normalized_child_text) <= 12:
            return True
        return False

    def _infer_engagement_state(self, normalized_for_matching: str) -> str:
        if not normalized_for_matching:
            return "unknown"
        if self._contains_any(normalized_for_matching, END_SESSION_MARKERS):
            return "withdrawing"
        if self._contains_any(normalized_for_matching, FRUSTRATION_MARKERS):
            return "frustrated"
        if self._contains_any(normalized_for_matching, CURIOUS_MARKERS):
            return "curious"
        if self._contains_any(normalized_for_matching, PLAYFUL_MARKERS):
            return "playful"
        if len(normalized_for_matching) <= 2:
            return "distracted"
        return "engaged"

    def _normalize_display_text(self, text: str) -> str:
        normalized = text.strip()
        for filler in FILLER_MARKERS:
            normalized = normalized.replace(filler, " ")
        normalized = " ".join(normalized.split())
        return normalized

    def _normalize_for_matching(self, text: str) -> str:
        lowered = self._normalize_display_text(text).lower()
        for source, target in COMMON_ORAL_NORMALIZATIONS.items():
            lowered = lowered.replace(source.lower(), target.lower())
        lowered = PUNCTUATION_RE.sub(" ", lowered)
        lowered = " ".join(lowered.split())
        return lowered

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)
