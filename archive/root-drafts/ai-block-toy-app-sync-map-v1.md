# AI积木玩具 App 同步映射表 v1

项目：AI积木玩具  
范围：C. 状态机 / 事件流 / 家长端手机 App 同步映射  
日期：2026-03-16  
状态：v1 草案，可直接作为 App-BFF / projection 设计输入

## 1. 前提和同步原则

- 家长端是手机 App，不是桌面后台。
- App 目标不是“看后端日志”，而是让家长快速看懂 4 件事：
  - 现在孩子在干什么
  - 这一轮顺不顺
  - 需不需要家长介入
  - 结束后该看什么报告、怎么跟进
- 继续坚持“状态机控流程，LLM 控话术”：
  - 状态推进、阶段切换、任务完成与否，只认状态机和投影结果。
  - LLM 只提供理解候选和话术候选，不给前端直接暴露 raw transcript、raw reply、prompt、rule 命中细节。
- 前端读取顺序必须固定：
  1. 先读 projection / public view
  2. 再读实体公开字段
  3. 不允许直接扫 raw event
- 手机 App 只适合看“摘要态、进度态、提醒态”，不适合承载内部状态机细节。`current_state` 给工程调试，不给家长看。

## 2. v1 推荐 projection 层

| projection / view | 建议接口 | 主要来源 | 刷新触发 |
|---|---|---|---|
| `home_snapshot_view` | `GET /app/home` | `session_public_view` + 最新 `parent_report` + 内容进度聚合 | `session.started`、`session.ended`、`parent_report.generated` |
| `session_live_view` | `GET /app/sessions/:id/live` | `session` + 当前 `task` + 过滤后的事件投影 | `task.activated`、`state.transition_applied`、`help.level_changed`、`task.completed`、`task.failed`、`parent.interrupt_requested`、`parent.resume_requested`、`session.ended` |
| `session_timeline_view` | `GET /app/sessions/:id/timeline` | `event` 的安全事件子集二次投影 | 与 `session_live_view` 同步刷新 |
| `report_detail_view` | `GET /app/reports/:id` | `parent_report` + `task` 摘要聚合 | `parent_report.generated`、`publish_status` 变化 |
| `content_catalog_view` | `GET /app/content` | `theme` 公开字段 + 每主题历史进度聚合 | `theme` 更新、`parent_report.generated` |
| `settings_view` | `GET /app/settings` | 设备 / 儿童档案 / 通知偏好 / 安全偏好投影 | 设置变更、设备状态变更 |

工程上要点：
- `session_public_view` 是现有 schema 已经明确建议的公开层，App 实时能力应该在它之上继续做 `session_live_view`。
- `settings_view` 依赖额外配置实体；如果 v1 后端还没有，就单独补最小配置表，不要从 `session/event` 硬拼。

### 2.1 Projection / DTO 字段生成规则简表

字段命名和来源以 B 文档为准；projection 只负责聚合、人话化和裁剪，不重新发明机器字段语义。

| DTO / 页面字段 | 主要 B 字段 | 依赖的事件聚合 | 生成规则 |
|---|---|---|---|
| `home_snapshot_view.active_session.public_stage` | `session.public_stage` | 最新 `state.transition_applied`，`session.ended` 收口 | 直接取 `session.public_stage`；若 `session.status=paused`，不改 stage，只在 `display_status` 叠加暂停态 |
| `home_snapshot_view.active_session.display_status` | `session.status`, `session.end_reason` | 进入/离开 `parent_interrupt_hold` 的 `state.transition_applied`，`session.ended` | 输出 `active / paused / ended / aborted` 的人话版；`interrupted` 不单独做字段值 |
| `session_live_view.header.public_stage_text` | `session.public_stage` | 同上 | 只把固定 `public_stage` 枚举翻译成人话，不读取 `current_state` |
| `session_live_view.current_task` | `session.current_task_id` + `task.parent_label`, `task.help_level_current`, `task.parent_note` | `task.activated`、`help.level_changed`、`task.completed`、`task.failed` | 始终取当前激活 task；完成/失败后切到下一 task 或清空当前卡片 |
| `session_live_view.progress` | `session.turn_count`, `session.completed_task_count`, `session.retry_count` | `task.completed`、`task.failed`、有效输入计数事件 | 以 `session` 聚合字段为准，不让 App 自己累加 |
| `session_live_view.parent_action` | `task.help_level_current`, `session.status`, `session.end_reason` | `help.level_changed`、`parent.interrupt_requested`、`parent.resume_requested`、`session.ended` | `help_level_current=parent_takeover` 或 `status in (paused, aborted)` 时生成家长介入卡 |
| `session_timeline_view.items[]` | `event.payload_public`, `event.occurred_at`, `event.event_type`, `task.parent_note` | 过滤 `parent_visible=true`，按 `occurred_at + seq_no` 排序，折叠噪声事件 | 输出 `display_type / display_text / severity`，不把 raw event 直接透给前端 |
| `report_detail_view.summary / task_breakdown` | `parent_report.*`, `task.parent_label`, `task.result_code`, `task.parent_note`, `session.end_reason` | `task.completed`、`task.failed`、`session.ended` 聚合后触发 `parent_report.generated` | 报告生成前可短暂回退读 `session/task` 摘要；报告生成后统一以 `parent_report` 为准 |

## 3. 页面映射

### 3.1 首页

- 页面要回答的问题：
  - 现在有没有正在进行的会话
  - 最近一次玩了什么，结果怎么样
  - 家长下一步最该点哪里
- 直接读取哪些后端字段 / 投影：
  - `home_snapshot_view.active_session`: `session_public_view.public_stage`、`display_status`、`started_at`、`parent_summary_short`、`completed_task_count`、`retry_count`
  - `home_snapshot_view.active_task`: 当前 `task.parent_label`、`task.help_level_current`
  - `home_snapshot_view.latest_report`: `parent_report.theme_name_snapshot`、`generated_at`、`parent_summary`、`follow_up_suggestion`、`safety_notice_level`
  - `home_snapshot_view.content_resume`: `content_catalog_view` 里的最近主题、预计时长、难度、最近进度
- 不该直接读取哪些内部字段：
  - `session.current_state`
  - `event.state_before/state_after`
  - `event.payload_private`
  - `asr.transcribed.transcript`
  - `assistant.reply_prepared.reply_text`
  - `device_id`、`child_profile_id`
- 推荐展示模块：
  - 正在进行中的会话卡片
  - 最新报告卡片
  - 继续上次内容卡片
  - 异常提醒条：只显示“会话中断 / 需要关注”，不显示内部错误栈

### 3.2 会话页

- 页面要回答的问题：
  - 孩子现在进行到哪一阶段
  - 当前任务是什么
  - 系统是在正常推进、给提示，还是需要家长介入
- 直接读取哪些后端字段 / 投影：
  - `session_live_view.header`: `public_stage`、`display_status`、`started_at`、`ended_at`
  - `session_live_view.progress`: `turn_count`、`completed_task_count`、`retry_count`
  - `session_live_view.current_task`: `task.parent_label`、`task.help_level_current`、`task.parent_note`
  - `session_live_view.session_summary`: `parent_summary_short`
  - `session_timeline_view.items`: `display_type`、`display_text`、`occurred_at`、`severity`
  - `session_live_view.parent_action`: 投影后的 `need_parent_intervention`、`intervention_reason_text`、`suggested_action_text`
- 不该直接读取哪些内部字段：
  - `session.current_task_id`
  - `task.node_key`、`theme_task_key`
  - `state_transition_rule.rule_id`、`guard_expr`、`trigger_filter`
  - `event.confidence_score`
  - `nlu.interpreted.intent/slots`
  - 原始 `payload_public` 散字段
- 推荐展示模块：
  - 阶段头部条：`warming_up / doing_task / receiving_hint / celebrating / cooling_down / ended`
  - 当前任务卡
  - 实时时间线
  - 家长介入提示卡
  - 会话结束后跳报告入口

### 3.3 报告页

- 页面要回答的问题：
  - 这次到底完成了什么
  - 哪些地方做得好
  - 哪些地方需要继续练
  - 家长接下来怎么跟一句、玩一轮
- 直接读取哪些后端字段 / 投影：
  - `report_detail_view.summary`: `theme_name_snapshot`、`duration_sec`、`completed_task_count`、`task_completion_rate`、`help_level_peak`、`confidence_overall`
  - `report_detail_view.highlights`: `achievement_tags`、`notable_moments`
  - `report_detail_view.parent_text`: `parent_summary`、`follow_up_suggestion`
  - `report_detail_view.safety`: `safety_notice_level`
  - `report_detail_view.task_breakdown`: `task.parent_label`、`task.result_code`、`task.parent_note`
- 不该直接读取哪些内部字段：
  - `parent_report.source_event_from_seq`、`source_event_to_seq`
  - `session.internal_summary`
  - 原始 `event.payload_public`
  - 原始 `reason_codes`
  - 安全模型原始分数
- 推荐展示模块：
  - 报告总览卡
  - 亮点标签区
  - 任务拆解列表
  - 家长跟进建议卡
  - 安全提醒区

### 3.4 内容页

- 页面要回答的问题：
  - 有哪些主题可玩
  - 哪些适合当前孩子
  - 上次玩到哪里，值不值得继续
- 直接读取哪些后端字段 / 投影：
  - `content_catalog_view.items`: `theme.name`、`subtitle`、`age_band_min`、`age_band_max`、`difficulty_level`、`expected_duration_sec`、`parent_goal_copy`
  - `content_catalog_view.progress`: 最近游玩时间、最近报告摘要、主题完成次数、最近一次会话阶段
  - `content_catalog_view.recommendation`: 推荐原因文本、是否可继续上次进度
- 不该直接读取哪些内部字段：
  - `theme.code`
  - `theme.version`
  - `state_machine_version`
  - `task_flow_ref`
  - `objective`
  - `safety_policy_ref`
- 推荐展示模块：
  - 内容卡片流
  - 继续上次主题入口
  - 年龄 / 难度标签
  - “这次主要练什么”说明块

### 3.5 设置页

- 页面要回答的问题：
  - 设备是不是在线
  - 报告和提醒怎么推送
  - 内容和安全边界怎么设
  - 当前孩子档案是什么
- 直接读取哪些后端字段 / 投影：
  - `settings_view.device_status`: 设备展示名、在线状态、最近心跳时间
  - `settings_view.child_profile`: 孩子展示名、年龄段、默认难度偏好
  - `settings_view.notifications`: 报告推送、会话结束推送、家长介入提醒开关
  - `settings_view.guardrails`: 年龄内容边界、是否开启强提醒
- 不该直接读取哪些内部字段：
  - 原始 `device_id`
  - 原始 `child_profile_id`
  - 推送 token
  - 内部策略版本号
  - 内部审计日志
- 推荐展示模块：
  - 设备状态卡
  - 儿童档案卡
  - 通知设置区
  - 内容 / 安全偏好区

补一句：
- 设置页需要补最小配置模型；如果后端 v1 先不补齐，这页就不要假装做“全量后台设置”，先收成设备状态 + 通知开关 + 内容边界 3 个模块。

## 4. 核心映射表：状态机状态 / 关键事件 / 家长端显示

说明：
- 左侧状态名是工程对照用，前端不直接显示。
- 前端真正显示的是投影后的 `public_stage`、任务摘要、提示摘要和报告摘要。
- `public_stage` 只用固定枚举，不承载 `paused`。
- `session.status=paused` 只对应 A 文档的 `parent_interrupt_hold`。
- `display_status=aborted` 表示不可恢复提前终止；`parent_interrupted` 只放在 `end_reason`，不单独做 stage。

| 内部状态机状态 | 关键事件 | 投影层要产出什么 | 家长端该显示什么 |
|---|---|---|---|
| `warming_up` | `session.started`、`theme.bound` | `public_stage=warming_up`，带主题名和开场说明 | “已开始，正在进入本轮玩法” |
| `await_answer` | `task.activated`、`tts.playback_finished` | 当前任务名、当前阶段、已完成数、等待中状态 | “正在做第一个任务 / 当前任务：搭桥” |
| `give_hint` | `nlu.interpreted(low)` + `state.transition_applied` + `help.level_changed` | `public_stage=receiving_hint`，提示级别改为 `light_nudge/guided_hint` | “系统已给提示，正在继续引导” |
| `step_by_step_help` | 再次低置信或错误理解 + `help.level_changed` | 介入等级上调，生成家长可读原因 | “进入分步引导，建议先观察” |
| `demo_mode` | `child.no_response_timeout` 或多次失败 | `help_level_current=demo_mode`，生成示范态摘要 | “系统正在示范一次，可让孩子跟着做” |
| `celebrate_success` | `task.completed` | 完成数 +1，生成成功时刻摘要，`public_stage=celebrating` | “这一小步完成了，系统正在鼓励孩子” |
| `next_task_ready` | 新一条 `task.activated` | 切换当前任务，重置当前任务卡 | “准备进入下一步任务” |
| `cooling_down` | 最后任务完成后收尾事件 | 会话摘要雏形、报告生成中状态 | “本轮快结束，报告生成中” |
| `parent_interrupt_hold` | `parent.interrupt_requested`、`help.level_changed(parent_takeover)` | `session.status=paused`，`public_stage` 沿用 `anchor_state` 对应值，给出暂停原因和恢复动作 | “已暂停，等待家长协助” |
| `abort_cleanup` | `session.ended`，常见伴随 `network_error / safety_stop / parent_interrupted` | `public_stage=ended`，`display_status=aborted`，输出 `end_reason`、是否可恢复 | “本轮已提前结束” / “因安全原因已停止” |
| `ended` | `session.ended(end_reason=completed)` | `public_stage=ended`，`display_status=ended`，输出简短总结、报告入口 | “本轮已结束，可查看报告” |

## 5. 哪些内容只能走 projection，不能让前端直接扫 raw event

| 前端要看的东西 | 只能怎么来 | 不能怎么来 | 原因 |
|---|---|---|---|
| 实时时间线 | `session_timeline_view.items` | 前端直扫 `event` 表或订阅全量 raw event | 手机端不该承担排序、去重、翻译、人话改写 |
| 当前阶段文案 | `session_live_view.public_stage_text` | 直接显示 `current_state` | `current_state` 是实现细节，不是家长语言 |
| 家长介入提示 | `session_live_view.parent_action` | 前端自己读 `help.level_changed` + `confidence_score` 拼逻辑 | 介入判断必须和状态机口径一致 |
| 报告亮点 | `report_detail_view.highlights` | 直接读 `asr.transcribed` 或 `assistant.reply_prepared` | raw transcript / raw reply 易泄露隐私，也很乱 |
| 风险提醒 | `report_detail_view.safety` 或 `home_snapshot_view.alerts` | 直接读 `reason_codes`、安全模型分数 | 家长只需要提醒结论，不需要分类器内部细节 |
| 首页摘要 | `home_snapshot_view` | App 自己拼 `session + task + report + event` | 客户端 fan-in 太重，口径也容易漂 |
| 内容进度 | `content_catalog_view.progress` | 前端按主题去扫历史 `session/event` | 聚合应由后端做，不该把报表逻辑塞进手机端 |

硬约束：
- 即使某些 event 带 `payload_public`，也建议先经过 projection 层再给前端，不要让 App 直接消费事件颗粒。
- App 的实时同步对象应该是“投影变更”或“BFF DTO 变更”，不是 raw event feed。

## 6. v1 MVP 建议：先做哪 3 个页面最值

建议先做：

1. 首页
2. 会话页
3. 报告页

原因很直接：
- 这 3 页已经覆盖“开始前看什么、进行中看什么、结束后看什么”的完整主链。
- 它们直接验证这个产品是不是对家长有价值，而不只是后端自己跑通状态机。
- 内容页可以先挂成首页里的内容卡片入口；设置页可以先做极简版，不要抢第一版资源。

## 7. 这版 v1 的核心结论

- App 前端不该读状态机内部细节，只该读投影后的 `public_stage`、任务摘要、报告摘要。
- 会话页是同步核心：它消费的是 `session_live_view + session_timeline_view`，不是 raw event。
- 如果 v1 资源有限，优先把 首页 / 会话页 / 报告页 做通，内容页和设置页都可以先缩。
