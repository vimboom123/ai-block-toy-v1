# AI积木玩具后端事件 / 数据结构草案 v1

项目：AI积木玩具  
范围：B. 后端事件 / 数据结构  
日期：2026-03-16  
状态：v1 草案，可直接作为后端建模起点

## 1. 边界和原则

这份只解决后端 schema，不展开成 PRD。

系统假设：
- 架构是“状态机控流程，LLM 控话术”。
- 状态推进只能由状态机规则决定，不能让 LLM 直接改 `session.status`、`task.status`、`current_state`。
- LLM 只产出两类东西：
  - 语义理解候选：`intent / slots / confidence`
  - 话术候选：`reply_text / tts_text / style_key`
- 后端建议采用“事件为事实源，实体表为物化视图”的思路：
  - `event` 保留完整时序事实
  - `session / task / parent_report` 存查询友好的当前态或汇总态

建议硬规则：
- 所有主键用 `uuid` 或 `uuidv7`
- 所有时间字段用 `timestamptz`
- 所有置信度同时保留：
  - `confidence_score`：`decimal(5,4)`，给机器判断
  - `confidence_level`：枚举，给业务规则和报表看
- 所有面向家长的字段必须走“安全可读层”，不要直接读原始 `event.payload_private`

---

## 2. 核心实体

### 2.1 `session`

一次儿童与玩具的完整互动会话。它是状态机运行容器。

| 字段 | 类型 | 必填 | 说明 | 家长端直读 |
|---|---|---:|---|---|
| `id` | `uuid` | 是 | session 主键 | 否 |
| `device_id` | `uuid` | 是 | 玩具设备 ID | 否 |
| `child_profile_id` | `uuid` | 是 | 儿童档案 ID | 否 |
| `theme_id` | `uuid` | 是 | 当前主题 | 否 |
| `theme_version` | `int` | 是 | 绑定时的主题版本快照 | 否 |
| `status` | `enum(session_status)` | 是 | `active / paused / ended / aborted`；`paused` 只用于可恢复暂停，`aborted` 用于异常/提前终止 | 否 |
| `current_state` | `varchar(64)` | 是 | 内部状态机节点，如 `await_answer` | 否 |
| `public_stage` | `enum(public_stage)` | 是 | 给家长看的人类可读阶段，如 `warming_up / doing_task / celebrating / ended` | 是 |
| `current_task_id` | `uuid` | 否 | 当前激活任务 | 否 |
| `help_level_peak` | `enum(help_level)` | 是 | 本 session 内最高帮助等级 | 是 |
| `turn_count` | `int` | 是 | 儿童有效互动轮数 | 是 |
| `completed_task_count` | `int` | 是 | 完成任务数 | 是 |
| `retry_count` | `int` | 是 | 重试次数 | 是 |
| `last_understanding_confidence` | `decimal(5,4)` | 否 | 最近一次语义理解置信度 | 否 |
| `risk_flags` | `jsonb` | 否 | 安全、内容、设备异常等内部标记 | 否 |
| `started_at` | `timestamptz` | 是 | 开始时间 | 是 |
| `ended_at` | `timestamptz` | 否 | 结束时间 | 是 |
| `end_reason` | `enum(end_reason)` | 否 | 结束原因 | 是 |
| `parent_summary_short` | `text` | 否 | 给家长看的简短摘要 | 是 |
| `internal_summary` | `text` | 否 | 内部总结，允许带系统判断 | 否 |
| `created_at` | `timestamptz` | 是 | 创建时间 | 否 |
| `updated_at` | `timestamptz` | 是 | 更新时间 | 否 |

建议：
- 家长端实时页不要直接展示 `current_state`，读 `public_stage`。
- `status` 和 `public_stage` 分开，前者服务系统，后者服务展示。

`session.status` 和 `public_stage` 的口径补充：
- `active`：主线状态和绝大多数非终态横切状态的默认值。
- `paused`：只在状态机进入 A 文档的 `parent_interrupt_hold` 且保留恢复点时写入。
- `ended`：只用于正常完成路径，和 `session.ended(end_reason=completed)` 一起落库。
- `aborted`：进入 `abort_cleanup` 且确认不可恢复时写入，覆盖家长终止、安全停止、网络异常等提前结束场景。
- `interrupted` 不是 `session_status` 枚举值；机器字段统一用 `paused` 或 `end_reason=parent_interrupted`。
- `public_stage` 不编码 `paused`；暂停时沿用 `anchor_state` 对应的阶段值，由 App 的 `display_status` 叠加“已暂停”。

### 2.2 `theme`

主题是一个可复用的玩法包，定义目标、任务骨架、状态机版本和家长说明。

| 字段 | 类型 | 必填 | 说明 | 家长端直读 |
|---|---|---:|---|---|
| `id` | `uuid` | 是 | theme 主键 | 否 |
| `code` | `varchar(64)` | 是 | 稳定业务编码，如 `build_bridge_v1` | 否 |
| `version` | `int` | 是 | 主题版本 | 否 |
| `name` | `varchar(128)` | 是 | 主题名 | 是 |
| `subtitle` | `varchar(255)` | 否 | 给家长或运营看的副标题 | 是 |
| `age_band_min` | `smallint` | 是 | 适龄下限 | 是 |
| `age_band_max` | `smallint` | 是 | 适龄上限 | 是 |
| `difficulty_level` | `enum(difficulty_level)` | 是 | 难度等级 | 是 |
| `state_machine_version` | `varchar(64)` | 是 | 绑定的状态机版本 | 否 |
| `task_flow_ref` | `varchar(128)` | 是 | 任务流配置引用 | 否 |
| `expected_duration_sec` | `int` | 否 | 预计时长 | 是 |
| `objective` | `text` | 是 | 主题目标，偏内部 | 否 |
| `parent_goal_copy` | `text` | 否 | 给家长看的“这次在练什么” | 是 |
| `parent_tip_template` | `text` | 否 | 会后给家长的互动建议模板 | 是 |
| `safety_policy_ref` | `varchar(128)` | 是 | 安全策略版本 | 否 |
| `status` | `enum(config_status)` | 是 | `draft / active / archived` | 否 |
| `created_at` | `timestamptz` | 是 | 创建时间 | 否 |
| `updated_at` | `timestamptz` | 是 | 更新时间 | 否 |

### 2.3 `task`

`task` 建议定义为 session 内的任务实例，不是纯模板。模板信息通过 `theme_task_key` 关联配置。

| 字段 | 类型 | 必填 | 说明 | 家长端直读 |
|---|---|---:|---|---|
| `id` | `uuid` | 是 | task 实例主键 | 否 |
| `session_id` | `uuid` | 是 | 所属 session | 否 |
| `theme_id` | `uuid` | 是 | 所属 theme | 否 |
| `theme_task_key` | `varchar(64)` | 是 | 主题内稳定任务 key | 否 |
| `node_key` | `varchar(64)` | 是 | 当前状态机节点绑定 key | 否 |
| `sequence_no` | `int` | 是 | 本 session 内任务顺序 | 否 |
| `task_type` | `enum(task_type)` | 是 | 如 `listen / identify / build / answer`；`celebrate_success` 是状态机状态，不单独建成 `task_type` | 否 |
| `title` | `varchar(128)` | 是 | 内部标题 | 否 |
| `parent_label` | `varchar(128)` | 否 | 家长端可读任务名 | 是 |
| `status` | `enum(task_status)` | 是 | `pending / active / completed / failed / skipped` | 否 |
| `attempt_count` | `smallint` | 是 | 当前已尝试次数 | 否 |
| `max_attempts` | `smallint` | 是 | 最大尝试次数 | 否 |
| `help_level_current` | `enum(help_level)` | 是 | 当前任务帮助级别 | 是 |
| `success_condition` | `jsonb` | 是 | 完成条件快照 | 否 |
| `result_code` | `enum(task_result_code)` | 否 | 完成/失败结果码 | 是 |
| `started_at` | `timestamptz` | 否 | 开始时间 | 否 |
| `finished_at` | `timestamptz` | 否 | 结束时间 | 否 |
| `parent_note` | `text` | 否 | 家长端摘要，如“今天能在提示下完成 2 步搭建” | 是 |
| `created_at` | `timestamptz` | 是 | 创建时间 | 否 |
| `updated_at` | `timestamptz` | 是 | 更新时间 | 否 |

### 2.4 `event`

`event` 是唯一时序事实源。原则上 append-only，不做业务含义上的更新覆盖。

| 字段 | 类型 | 必填 | 说明 | 家长端直读 |
|---|---|---:|---|---|
| `id` | `uuid` | 是 | event 主键 | 否 |
| `session_id` | `uuid` | 是 | 所属 session | 否 |
| `seq_no` | `bigint` | 是 | session 内单调递增序号 | 否 |
| `event_type` | `varchar(64)` | 是 | 事件名 | 否 |
| `producer` | `enum(event_producer)` | 是 | `device / asr / nlu / state_engine / llm / tts / system` | 否 |
| `task_id` | `uuid` | 否 | 关联 task | 否 |
| `theme_id` | `uuid` | 否 | 关联 theme | 否 |
| `state_before` | `varchar(64)` | 否 | 触发前状态 | 否 |
| `state_after` | `varchar(64)` | 否 | 触发后状态 | 否 |
| `caution_level` | `enum(caution_level)` | 否 | 安全/异常提示级别 | 否 |
| `confidence_score` | `decimal(5,4)` | 否 | 本事件相关置信度 | 否 |
| `confidence_level` | `enum(confidence_level)` | 否 | 离散置信度等级 | 否 |
| `causation_event_id` | `uuid` | 否 | 由哪个事件触发 | 否 |
| `correlation_id` | `uuid` | 否 | 一条链路上的关联 ID | 否 |
| `idempotency_key` | `varchar(128)` | 否 | 去重键 | 否 |
| `payload_public` | `jsonb` | 否 | 安全可读字段，允许投给家长端 | 条件允许 |
| `payload_private` | `jsonb` | 否 | 原始/内部字段，如 prompt、分类器结果、调试数据 | 否 |
| `parent_visible` | `boolean` | 是 | 是否允许出现在家长端事件时间线 | 否，建议走 projection |
| `occurred_at` | `timestamptz` | 是 | 业务发生时间 | 否 |
| `ingested_at` | `timestamptz` | 是 | 写库时间 | 否 |

建议：
- 家长端不要直接扫 `event` 表，应该读后端投影出来的 `session_public_view` 或 `parent_report`。
- `payload_public` 只放已经脱敏和翻译成人话的内容。

### 2.5 `parent_report`

家长报告是会话完成后的安全摘要，不是 raw log。

| 字段 | 类型 | 必填 | 说明 | 家长端直读 |
|---|---|---:|---|---|
| `id` | `uuid` | 是 | report 主键 | 否 |
| `session_id` | `uuid` | 是 | 来源 session | 否 |
| `child_profile_id` | `uuid` | 是 | 所属儿童 | 否 |
| `report_date` | `date` | 是 | 报告日期 | 是 |
| `report_version` | `int` | 是 | 报告版本 | 否 |
| `generated_at` | `timestamptz` | 是 | 生成时间 | 否 |
| `theme_name_snapshot` | `varchar(128)` | 是 | 主题名快照 | 是 |
| `duration_sec` | `int` | 是 | 会话时长 | 是 |
| `completed_task_count` | `int` | 是 | 完成任务数 | 是 |
| `task_completion_rate` | `decimal(5,4)` | 是 | 完成率 | 是 |
| `help_level_peak` | `enum(help_level)` | 是 | 最高帮助等级 | 是 |
| `confidence_overall` | `enum(confidence_level)` | 是 | 整体置信度等级 | 是 |
| `achievement_tags` | `jsonb` | 否 | 达成标签，如“能跟着两步指令完成搭建” | 是 |
| `notable_moments` | `jsonb` | 否 | 精选亮点，必须是安全改写后文本 | 是 |
| `parent_summary` | `text` | 是 | 给家长看的简明总结 | 是 |
| `follow_up_suggestion` | `text` | 否 | 家长下一步怎么接话 | 是 |
| `safety_notice_level` | `enum(caution_level)` | 否 | 是否需要额外提醒 | 是 |
| `source_event_from_seq` | `bigint` | 是 | 来源事件起点 | 否 |
| `source_event_to_seq` | `bigint` | 是 | 来源事件终点 | 否 |
| `publish_status` | `enum(report_status)` | 是 | `draft / published / withdrawn` | 否 |

---

## 3. 统一事件 Schema

建议所有事件统一走一个 envelope，事件名不同，`payload_*` 不同。

```json
{
  "id": "evt_01...",
  "session_id": "ses_01...",
  "seq_no": 18,
  "event_type": "state.transition_applied",
  "producer": "state_engine",
  "task_id": "tsk_01...",
  "theme_id": "thm_01...",
  "state_before": "await_answer",
  "state_after": "give_hint",
  "confidence_score": 0.42,
  "confidence_level": "low",
  "causation_event_id": "evt_00...",
  "correlation_id": "corr_01...",
  "payload_public": {
    "display_text": "孩子暂时没答上来，系统已切换到提示模式"
  },
  "payload_private": {
    "rule_id": "rule_give_hint_on_low_conf",
    "guard_result": "matched",
    "slot_values": {"target_color": "red"}
  },
  "parent_visible": false,
  "occurred_at": "2026-03-16T09:30:11Z",
  "ingested_at": "2026-03-16T09:30:11Z"
}
```

关键点：
- `payload_public` 和 `payload_private` 分层，别把脱敏交给前端。
- `state.transition_applied` 必须由状态机产出，不能由 LLM 产出。
- `llm.*` 事件可以带 `reply_text`，但不能带状态写入权限。

---

## 4. 事件类型建议

下面给一版 v1 够用的事件集合。已经覆盖设备输入、状态推进、理解、回复、兜底、报告。

| 事件名 | 触发方 | 必带 payload 字段 | 用途 | 家长端直读 |
|---|---|---|---|---|
| `session.started` | `system` | `entry_trigger`, `theme_id`, `initial_state` | 建立一次会话 | 否 |
| `theme.bound` | `state_engine` | `theme_id`, `theme_version`, `state_machine_version` | 把配置快照绑到 session | 否 |
| `task.activated` | `state_engine` | `task_id`, `theme_task_key`, `node_key`, `sequence_no` | 激活当前任务 | 否 |
| `device.signal_received` | `device` | `signal_type`, `signal_value`, `signal_ts` | 接按钮、摇一摇、拿起放下 | 否 |
| `child.audio_captured` | `device` | `audio_uri`, `duration_ms`, `input_source` | 录音完成并上传 | 否 |
| `asr.transcribed` | `asr` | `transcript`, `language`, `confidence_score` | 语音转文本结果 | 否 |
| `nlu.interpreted` | `nlu` | `intent`, `slots`, `confidence_score`, `confidence_level` | 产出语义理解候选 | 否 |
| `safety.checked` | `system` | `policy_version`, `result`, `reason_codes` | 内容安全、儿童安全过滤 | 否 |
| `child.no_response_timeout` | `state_engine` | `timeout_sec`, `waiting_state` | 等待超时 | 否 |
| `parent.interrupt_requested` | `system` | `source`, `reason` | 家长要求暂停或接管 | 否 |
| `parent.resume_requested` | `system` | `source` | 家长确认恢复主流程 | 否 |
| `parent.end_session_requested` | `system` | `source`, `reason` | 家长确认结束本轮 | 否 |
| `state.transition_applied` | `state_engine` | `rule_id`, `from_state`, `to_state`, `trigger_event` | 真正推进状态机 | 否 |
| `help.level_changed` | `state_engine` | `from_level`, `to_level`, `reason` | 调整引导强度 | 条件允许 |
| `assistant.reply_prepared` | `llm` | `reply_text`, `tts_text`, `style_key`, `utterance_id` | 生成要说的话 | 不建议直读 raw，建议读改写后摘要 |
| `tts.playback_requested` | `tts` | `utterance_id`, `voice_id`, `audio_uri` | 准备播报 | 否 |
| `tts.playback_finished` | `device` | `utterance_id`, `actual_duration_ms` | 播放结束 | 否 |
| `task.completed` | `state_engine` | `task_id`, `result_code`, `attempt_count` | 任务完成 | 是，建议投影后给家长看 |
| `task.failed` | `state_engine` | `task_id`, `result_code`, `attempt_count`, `failure_reason` | 任务失败或终止 | 是，建议投影后给家长看 |
| `system.cleanup_finished` | `system` | `cleanup_result`, `released_resources` | 异常收尾和资源释放完成 | 否 |
| `session.ended` | `system` | `end_reason`, `duration_sec`, `completed_task_count` | 会话结束 | 是 |
| `parent_report.generated` | `system` | `report_id`, `source_event_range`, `summary_version` | 家长报告生成完成 | 是 |

补充约束：
- `asr.transcribed`、`nlu.interpreted`、`assistant.reply_prepared` 都是“候选结果事件”，不是状态事实。
- 真正可驱动流程的关键事件建议只认：
  - `device.signal_received`
  - `child.no_response_timeout`
  - `state.transition_applied`
  - `task.completed`
  - `task.failed`
  - `session.ended`

---

## 5. 状态转移规则表字段设计

建议单独建 `state_transition_rule` 配置表。它是“流程骨架”，LLM 只是被它调用。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `id` | `uuid` | 是 | 规则主键 |
| `theme_id` | `uuid` | 是 | 所属主题 |
| `theme_version` | `int` | 是 | 所属主题版本 |
| `state_machine_version` | `varchar(64)` | 是 | 状态机版本 |
| `from_state` | `varchar(64)` | 是 | 起始状态 |
| `trigger_event` | `varchar(64)` | 是 | 触发事件名 |
| `trigger_filter` | `jsonb` | 否 | 对 payload 做过滤，如 `intent=correct_answer` |
| `guard_expr` | `jsonb` | 否 | 额外条件，如 `attempt_count < max_attempts` |
| `min_confidence_score` | `decimal(5,4)` | 否 | 命中该规则所需最小置信度 |
| `max_confidence_score` | `decimal(5,4)` | 否 | 命中该规则所需最大置信度 |
| `required_help_level` | `enum(help_level)` | 否 | 命中前提的帮助等级 |
| `timeout_sec` | `int` | 否 | 该规则关联的等待时长 |
| `to_state` | `varchar(64)` | 是 | 目标状态 |
| `task_action` | `enum(task_action)` | 否 | `activate / complete / fail / retry / noop` |
| `next_task_selector` | `jsonb` | 否 | 如何选下一个 task |
| `emit_events` | `jsonb` | 否 | 命中后额外发哪些事件 |
| `reply_policy_key` | `varchar(128)` | 否 | 调用哪套 LLM 话术模板 |
| `parent_note_template` | `varchar(128)` | 否 | 家长摘要模板 key |
| `priority` | `smallint` | 是 | 多规则并存时的优先级 |
| `enabled` | `boolean` | 是 | 是否生效 |
| `created_at` | `timestamptz` | 是 | 创建时间 |
| `updated_at` | `timestamptz` | 是 | 更新时间 |

推荐判定顺序：
1. 先按 `from_state + trigger_event` 找候选规则
2. 再跑 `trigger_filter / guard_expr`
3. 再看 `confidence_score`
4. 命中后只允许一条规则生效
5. 生效后由状态机写 `state.transition_applied`
6. 然后再触发 LLM 生成对应话术

### 5.1 简化样例

| `from_state` | `trigger_event` | `trigger_filter` | `to_state` | `task_action` | `reply_policy_key` |
|---|---|---|---|---|---|
| `interpret_input` | `nlu.interpreted` | `{"intent":"correct_answer"}` | `celebrate_success` | `complete` | `praise_success_v1` |
| `interpret_input` | `nlu.interpreted` | `{"confidence_level":"low"}` | `give_hint` | `retry` | `hint_light_v1` |
| `await_answer` | `child.no_response_timeout` | `{"timeout_sec":">=8","reengage_count":"<1"}` | `reengagement` | `noop` | `reengage_once_v1` |
| `guided_hint` | `nlu.interpreted` | `{"intent":"wrong_answer"}` | `step_by_step_help` | `retry` | `hint_stronger_v1` |
| `celebrate_success` | `tts.playback_finished` | `{}` | `next_task_ready` | `activate` | `next_task_intro_v1` |

---

## 6. 关键枚举建议

### 6.1 `end_reason`

建议枚举：

| 枚举值 | 含义 |
|---|---|
| `completed` | 正常完成主题或本轮任务 |
| `child_quit` | 孩子主动退出 / 明显不想继续 |
| `timeout_no_input` | 长时间无输入 |
| `network_error` | 网络异常中断 |
| `asr_fail_exhausted` | ASR 连续失败达到阈值 |
| `safety_stop` | 安全策略要求终止 |
| `parent_interrupted` | 家长手动打断 |
| `device_shutdown` | 设备掉电 / 关机 |
| `theme_switched` | 中途切换玩法主题 |
| `system_abort` | 服务端异常中止 |

### 6.2 `help_level`

建议不要只记“是否帮助过”，而是记录帮助强度。

| 枚举值 | 含义 |
|---|---|
| `none` | 不给提示，鼓励孩子自己完成 |
| `light_nudge` | 轻提示，只给方向 |
| `guided_hint` | 明确提示一个关键线索 |
| `step_by_step` | 一步一步带着做 |
| `demo_mode` | 直接示范一次，再邀请模仿 |
| `parent_takeover` | 需要家长介入协助 |

### 6.3 `confidence_level`

建议和原始分数一起保存。

| 枚举值 | 分数建议 |
|---|---|
| `very_low` | `< 0.20` |
| `low` | `0.20 - 0.49` |
| `medium` | `0.50 - 0.74` |
| `high` | `0.75 - 0.89` |
| `very_high` | `>= 0.90` |

### 6.4 其他顺手建议的枚举

| 枚举名 | 建议值 |
|---|---|
| `session_status` | `active / paused / ended / aborted`（`paused` 只对应可恢复暂停，`aborted` 对应不可恢复提前终止） |
| `public_stage` | `warming_up / doing_task / receiving_hint / celebrating / cooling_down / ended` |
| `task_type` | `listen / identify / build / answer`（不单独设 `celebrate`；`celebrate_success` 是状态，不是任务类型） |
| `task_status` | `pending / active / completed / failed / skipped` |
| `task_result_code` | `correct / completed_with_hint / demo_followed / skipped / failed_confusion / failed_timeout` |
| `caution_level` | `none / low / medium / high` |
| `event_producer` | `device / asr / nlu / state_engine / llm / tts / system` |

---

## 7. 家长端可读字段和禁止直曝字段

本项目建议强制分三层：
- 可直接给家长端读
- 必须后端裁剪/改写后再给
- 不能直曝

### 7.1 可直接给家长端页面读

优先从 `parent_report` 读，其次读 `session` 的公开字段。

| 来源 | 字段 |
|---|---|
| `theme` | `name`, `subtitle`, `age_band_min`, `age_band_max`, `difficulty_level`, `expected_duration_sec`, `parent_goal_copy` |
| `session` | `public_stage`, `help_level_peak`, `turn_count`, `completed_task_count`, `retry_count`, `started_at`, `ended_at`, `end_reason`, `parent_summary_short` |
| `task` | `parent_label`, `help_level_current`, `result_code`, `parent_note` |
| `parent_report` | `theme_name_snapshot`, `duration_sec`, `task_completion_rate`, `help_level_peak`, `confidence_overall`, `achievement_tags`, `notable_moments`, `parent_summary`, `follow_up_suggestion`, `safety_notice_level` |

### 7.2 只能裁剪后给，不能直接把原字段甩出去

| 原始数据 | 处理方式 |
|---|---|
| `asr.transcribed.transcript` | 可摘要成“孩子表达了想继续搭高桥”这种改写文本，不直接给 raw transcript |
| `assistant.reply_prepared.reply_text` | 可以抽成“系统给了轻提示”，不要直接展示全部内部话术 |
| `event.payload_public` | 也建议先经投影层组装成时间线 DTO，再给前端 |
| `confidence_score` | 面向家长建议转成等级或描述，不直接给小数 |
| `reason_codes` | 先翻译成人话，再展示 |

### 7.3 不能直接暴露

| 字段/信息 | 原因 |
|---|---|
| `device_id`, `child_profile_id`, `correlation_id`, `idempotency_key` | 纯内部标识 |
| `current_state`, `node_key`, `rule_id`, `guard_expr` | 纯状态机内部实现细节 |
| `payload_private` 全量 | 可能含 prompt、分类器结果、debug 信息 |
| 原始音频地址 `audio_uri`、TTS 地址 `audio_uri` | 隐私和存储安全问题 |
| LLM prompt、system prompt、策略版本细节 | 内部策略资产 |
| 安全模型原始分、风险标签明细 | 易误读，也不该给家长看内部分类器细节 |
| 内部错误栈、ASR/LLM 原始报错 | 工程噪音，对家长无意义 |

一句话收口：
- 家长端读“安全改写后的结果”
- 后台读“原始时序事实”
- 不要让家长页直接扫原始事件表

---

## 8. 落地建议

- `event` 表加唯一约束：`unique(session_id, seq_no)`
- `event_type`, `session_id + occurred_at`, `task_id` 建索引
- `state_transition_rule` 至少建索引：`theme_id, theme_version, from_state, trigger_event, enabled, priority`
- `parent_report` 建唯一约束：`unique(session_id, report_version)`
- 后端至少做两个 projection：
  - `session_public_view`
  - `parent_report`
- App / BFF 的字段来源简表放在 C 文档第 2.1 节，别在 B 文档外另发明一套 DTO 语义。

## 9. 这版 v1 的核心结论

- 后端最好按“`event` 为事实源，`session/task/parent_report` 为投影”的方式建。
- 状态推进权必须锁在 `state_transition_rule + state.transition_applied`，LLM 不碰业务状态写入。
- 家长端不要直接读 raw event，而是读专门裁剪过的公开字段和报告。
- `help_level`、`end_reason`、`confidence` 这些枚举应该从第一版就定住，不然后面报表和状态机会一起乱。
