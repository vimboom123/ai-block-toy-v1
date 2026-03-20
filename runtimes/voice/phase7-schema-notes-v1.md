# Phase 7 Schema Notes v1

已落地 schema：
- `schemas/signal_resolution.schema.json`
- `schemas/interaction_generation.schema.json`
- `schemas/interaction_context.schema.json`

## signal_resolution 作用
给系统内部推进用。

核心字段：
- `task_signal`
- `confidence`
- `reason`
- `fallback_needed`
- `normalized_child_text`

补充字段：
- `matched_completion_points`
- `missing_completion_points`
- `engagement_state`

## interaction_generation 作用
给孩子互动输出用。

核心字段：
- `reply_text`
- `interaction_mode`
- `emotion_tone`
- `redirect_strength`

补充字段：
- `acknowledged_child_point`
- `followup_question`

## interaction_context 作用
给 interaction generator 和 provider 之间传结构化上下文，用来组织 prompt，而不是继续塞一串薄字段。

核心字段：
- `child_input_text`
- `normalized_child_text`
- `task_signal`
- `engagement_state`
- `matched_completion_points`
- `missing_completion_points`
- `interaction_goal`
- `scene_style`
- `redirect_strength`
- `expected_child_action`

补充字段：
- `interaction_mode`
- `emotion_tone`
- `preferred_acknowledged_child_point`
- `preferred_followup_question`
- `recent_turn_summary`
- `rule_reason`
- `scene_context`
- `session_memory`

## 当前设计原则
1. signal 和 interaction 强制拆开
2. 内部推进允许保守，外部互动必须自然
3. provider 吃的是结构化 context，不再靠 prompt 里散落的扁平行文本硬拼
4. schema 先控制输出形状，后续再接模型与真实 runtime
