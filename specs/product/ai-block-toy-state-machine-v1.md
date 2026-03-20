# AI积木玩具状态机主流程 v1

项目：AI积木玩具  
范围：A. 状态机主流程  
日期：2026-03-16  
状态：v1 草案，可直接作为产品 / 后端共用流程基线

## 1. 边界和前提

这份只解决主流程状态机，不展开成 PRD。

前提先说死：
- 这是儿童 AI 积木玩具，不是开放式陪聊玩具。
- 核心机制是“状态机控流程，LLM 控话术”。
- 状态机负责：状态推进、任务完成判定、帮助升级、结束原因、安全打断、家长打断、报告触发。
- LLM 负责：理解候选和话术候选。
- LLM 不允许直接写 `session.status`、`current_state`、`task.status`、`help_level_current`、`end_reason`、`public_stage`。

这份 A 文档和已存在文档的关系：
- A：定义 session 级主流程状态、转移和边界。
- B：定义事件、实体、枚举、投影字段。
- C：定义 App 该读哪些投影，不直接碰状态机细节。

v1 默认假设：
- 单次 `session` 只跑一个 `theme`。
- 任一时刻只允许一个 `current_state`。
- 任一时刻只允许一个 `current_task_id` 处于 `active`。
- 物理搭建结果很多时候不能被设备直接看懂，所以需要引入“自报式确认机制”。

补充约束：
- 主状态是任务推进骨架。
- 横切 / 兜底状态可从多个主状态进入。
- 进入横切状态时，除 `safety_hold` 和 `abort_cleanup` 外，默认要带 `anchor_state`，便于处理后回原主线。

## 2. 统一字段口径

下文每个状态都按这 5 个字段写：

| 字段 | 含义 |
|---|---|
| `allowed_events` | 该状态只认哪些事件；其余事件默认忽略或只记日志 |
| `required_context` | 进入该状态前必须已经准备好的上下文 |
| `system_action` | 状态机或后端必须做的事 |
| `llm_instruction` | 只给 LLM 的话术约束，不带状态写权限 |
| `exit_rule` | 什么条件下离开该状态，去哪里 |

## 3. 主状态列表

| 状态 | purpose | allowed_events | required_context | system_action | llm_instruction | exit_rule |
|---|---|---|---|---|---|---|
| `session_bootstrap` | 建 session，绑定主题快照，初始化计数器和初始状态 | `session.start_requested`, `theme.bound`, `theme.bind_failed` | `device_id`, `child_profile_id`, `theme_id`, `state_machine_version` | 创建 `session`，写 `session.started`，装载主题配置，初始化 `help_level_peak=none` | 不生成自由话术；只允许系统占位开场 | `theme.bound` 成功后进 `warming_up`；配置失败进 `abort_cleanup` |
| `warming_up` | 把孩子拉进本轮主题，建立“现在要玩什么” | `assistant.reply_prepared`, `tts.playback_finished`, `safety.checked`, `parent.interrupt_requested` | 主题开场模板、儿童年龄段、当前主题目标 | 设 `public_stage=warming_up`，请求开场话术，播报开场 | 开场只说主题、规则、第一步，不预支后面流程 | 开场播报完成进 `task_dispatch`；安全/家长打断走横切状态 |
| `task_dispatch` | 激活当前任务，把任务目标说清楚 | `task.activated`, `assistant.reply_prepared`, `tts.playback_finished`, `parent.interrupt_requested` | `current_task_id`, `sequence_no`, `attempt_count=0`, `help_level_current=none` | 激活 task，写 `task.activated`，重置任务级计时器和重试计数 | 用一句短话说清当前任务和期待动作 | 指令播报完成进 `await_answer` |
| `await_answer` | 等孩子做动作、说答案或按键，不在这一步做语义判断 | `device.signal_received`, `child.audio_captured`, `child.no_response_timeout`, `safety.checked`, `parent.interrupt_requested` | 当前任务快照、等待时长、`help_level_current`、`attempt_count` | 开启等待计时器，接收语音 / 按键 / 动作信号 | 默认不主动多说；只有状态机发指令时才给一个短 keep-alive | 有输入就进 `interpret_input`；超时优先进 `reengagement`；安全/家长打断走横切状态 |
| `interpret_input` | 把孩子输入变成可判定的结构化候选 | `asr.transcribed`, `nlu.interpreted`, `device.signal_received`, `input.parse_failed`, `safety.checked` | 最近输入、任务成功条件、`confidence_score`, `confidence_level` | 调用 ASR/NLU 或设备信号解析，得到 `intent / slots / confidence`；决定是否需要自报确认 | 只允许产出理解候选，不允许鼓励或自行宣布成功 | 满足成功条件时进 `self_report_confirm` 或 `celebrate_success`；不满足时按帮助等级进提示状态；严重异常进 `abort_cleanup` |
| `self_report_confirm` | 在设备无法直接验证积木结果时，让孩子做一次收口式确认 | `nlu.interpreted`, `device.signal_received`, `child.no_response_timeout`, `parent.interrupt_requested` | `task.requires_self_report=true`、候选成功结果、确认超时、确认次数 | 发起封闭式确认，记录 `confirm_done / not_done / unsure`，必要时补记确认事件 | 只能问封闭问题，比如“搭好了就说好了，没好就说还没好” | `confirm_done` 且 guard 通过进 `celebrate_success`；`not_done/unsure` 进 `guided_hint` 或 `step_by_step_help`；超时进 `guided_hint` |
| `give_hint` | 给最轻量提示，不直接给答案 | `assistant.reply_prepared`, `tts.playback_finished`, `parent.interrupt_requested` | `help_level_current=none`，首次失败或低置信 | 写 `help.level_changed none->light_nudge`，增加 `task.attempt_count`，不增加 `session.retry_count` | 只给方向，不说完整答案，不拆很多步 | 提示播报完成回 `await_answer` |
| `guided_hint` | 明确给一个关键线索，比轻提示更具体 | `assistant.reply_prepared`, `tts.playback_finished`, `parent.interrupt_requested` | `help_level_current in (light_nudge, guided_hint)`，已有一次失败 / 确认未过 | 视情况写 `help.level_changed -> guided_hint`，标记关键线索已给出 | 说一个关键线索，但不替孩子完成 | 提示播报完成回 `await_answer`；若已到阈值，下次失败进 `step_by_step_help` |
| `step_by_step_help` | 把任务拆成 1 步 1 步带着做 | `assistant.reply_prepared`, `tts.playback_finished`, `device.signal_received`, `nlu.interpreted`, `parent.interrupt_requested` | `help_level_current in (guided_hint, step_by_step)`，`substep_plan`，`substep_index` | 写 `help.level_changed -> step_by_step`，生成子步骤，逐步推进 | 一次只说一步，等孩子做完再继续，不要一次把所有步骤倒完 | 当前子步说完回 `await_answer`；多次无效后进 `demo_mode` |
| `demo_mode` | 系统先示范一次，再邀请孩子模仿 | `assistant.reply_prepared`, `tts.playback_finished`, `device.signal_received`, `nlu.interpreted`, `child.no_response_timeout` | `help_level_current=step_by_step` 或连续超时，`demo_count=0` | 写 `help.level_changed -> demo_mode`，记录已示范，准备模仿回合 | 先示范一个最短正确路径，再明确邀请孩子照着做一次 | 示范播报完成回 `await_answer`；示范后仍失败则升到 `parent_takeover` 并进 `parent_interrupt_hold` |
| `celebrate_success` | 正式确认任务完成，给短鼓励，更新计数 | `task.completed`, `assistant.reply_prepared`, `tts.playback_finished` | 已命中成功 guard、`result_code`、当前帮助等级 | 写 `task.completed`，更新 `completed_task_count`、`help_level_peak`、`parent_note` | 表扬努力和结果，话短，别把成功讲成长篇复盘 | 鼓励播报完成进 `next_task_ready` |
| `next_task_ready` | 决定进入下一任务还是结束本轮 | `task.next_selected`, `task.none_left`, `theme.complete_ready` | 任务流配置、已完成任务数、主题剩余任务 | 选下一个 task 或标记主题完成，必要时写下一任务摘要 | 若还有任务，用一句自然过渡；没有任务就准备收尾 | 有下一个任务进 `task_dispatch`；没有任务进 `cooling_down` |
| `cooling_down` | 结束前做短收口，并触发家长报告生成 | `assistant.reply_prepared`, `tts.playback_finished`, `parent_report.generated`, `parent.interrupt_requested` | `completed_task_count`, `retry_count`, `help_level_peak`, 主题名快照 | 设 `public_stage=cooling_down`，生成 `parent_summary_short`，异步触发报告 | 只做短收口，不再开启新任务 | 收口完成后写 `session.status=ended` 和 `session.ended(end_reason=completed)`，进 `ended`；若收尾异常进 `abort_cleanup` |
| `ended` | 终态；本轮流程停止，对外只读 | `session.ended` | `ended_at`, `end_reason`, 最终投影数据 | 冻结 session，停止计时器，发布 projection / report 入口 | 不再生成新话术 | 终态，不再跳转 |

建议和 C 文档对齐的 `public_stage` 映射：
- `warming_up` -> `warming_up`
- `task_dispatch`、`await_answer`、`interpret_input`、`self_report_confirm` -> `doing_task`
- `give_hint`、`guided_hint`、`step_by_step_help`、`demo_mode`、`off_topic_repair`、`reengagement` -> `receiving_hint`
- `celebrate_success` -> `celebrating`
- `next_task_ready` -> `doing_task`（若判定无后续任务，则立即转入 `cooling_down`）
- `cooling_down` -> `cooling_down`
- `parent_interrupt_hold` -> 不单独新造 `public_stage`；沿用 `anchor_state` 对应映射，暂停信息单走 `session.status=paused`
- `ended`、`abort_cleanup` -> `ended`

建议和 B / C 文档对齐的 `session.status` 规则：
- 默认主线状态和大多数横切状态都写 `active`。
- 只有进入 `parent_interrupt_hold` 且明确保留恢复点时，状态机才写 `session.status=paused`；这对应 A 文档里的 `parent_interrupt_hold`。
- 从 `parent_interrupt_hold` 收到 `parent.resume_requested` 返回 `anchor_state` 时，写 `session.status=active`。
- 若家长在 `parent_interrupt_hold` 阶段直接结束本轮，则状态必须从 `paused` 继续推进到 `abort_cleanup`，最终写成 `session.status=aborted + end_reason=parent_interrupted`；不允许把 `paused` 当作终态保留。
- 进入 `abort_cleanup` 且已判定本轮不可恢复时，写 `session.status=aborted`；家长端这时统一看到 `public_stage=ended`。
- 只有 `cooling_down -> ended` 的正常完结路径才写 `session.status=ended`，对应 `end_reason=completed`。
- `interrupted` 不是 `session.status` 也不是 `public_stage`；机器字段统一用 `paused` 或 `end_reason=parent_interrupted`。

## 4. 横切 / 兜底状态

说明：
- 这些状态不是主题任务主线，但必须从 v1 就建。
- `off_topic_repair`、`reengagement`、`parent_interrupt_hold` 默认要带 `anchor_state`。
- `safety_hold` 和 `abort_cleanup` 默认优先级最高。

| 状态 | purpose | allowed_events | required_context | system_action | llm_instruction | exit_rule |
|---|---|---|---|---|---|---|
| `off_topic_repair` | 孩子开始闲聊、乱答、跳题时，把话题拉回任务 | `nlu.interpreted`, `tts.playback_finished`, `child.no_response_timeout` | `anchor_state`, `current_task_id`, `off_topic_count` | 保留原任务不变，增加 `off_topic_count`，不修改完成计数 | 先轻承接一句，再立刻把当前任务重说清楚 | 孩子回到任务信号后回 `anchor_state`；连续跑偏则进 `reengagement` 或更高帮助等级 |
| `reengagement` | 孩子走神或沉默时，先尝试重新抓回注意力 | `assistant.reply_prepared`, `tts.playback_finished`, `device.signal_received`, `child.no_response_timeout` | `anchor_state`, `reengage_count`, 最近活跃时间 | 发送一次轻量重招呼，缩短下一次等待时长 | 话短，要有动作指令，比如“现在把红色那块放上去试一下” | 有响应就回 `anchor_state` 或进 `interpret_input`；再次超时则升级到 `guided_hint` / `demo_mode` / `abort_cleanup` |
| `safety_hold` | 命中安全规则时立刻冻结主流程 | `safety.checked`, `parent.resume_requested`, `parent.end_session_requested` | `risk_flags`, 安全原因、`anchor_state` | 停止当前任务，压住普通提示，必要时设 `end_reason=safety_stop` | 只允许安全话术：停止、转移、等家长，不做任务推进 | v1 默认直接进 `abort_cleanup`；只有明确可恢复场景才允许回安全后的主状态 |
| `parent_interrupt_hold` | 家长主动打断或需要家长接管时，暂停主流程 | `parent.interrupt_requested`, `parent.resume_requested`, `parent.end_session_requested`, `child.no_response_timeout` | `anchor_state`, 当前任务快照、暂停原因 | 写 `session.status=paused`，暂停计时器，保存恢复点，必要时把 `help_level_current` 提到 `parent_takeover` | 只承认“先暂停 / 请家长协助”，不继续追任务 | 家长恢复则写 `session.status=active` 后回 `anchor_state`；家长结束或超时则进 `abort_cleanup` |
| `abort_cleanup` | 统一做异常终止、收尾写库和资源清理 | `system.cleanup_finished` | `end_reason`, 活动 task 快照、失败原因、资源句柄 | 结束当前 task，必要时写 `task.failed`，写 `session.status=aborted` 和 `session.ended`，停计时器，收尾投影 | 如果设备链路还活着，只说一句结束说明；否则不补说话 | `system.cleanup_finished` 后进 `ended` |

## 5. 核心转移表

这张表只列 v1 主骨架，不把所有细枝末节都摊开。

| current_state | event | guard | action | next_state |
|---|---|---|---|---|
| `session_bootstrap` | `theme.bound` | 主题配置加载成功 | 写 `session.started`，装载主题版本快照 | `warming_up` |
| `session_bootstrap` | `theme.bind_failed` | 任一关键配置缺失 | 设 `end_reason=system_abort`，进入清理 | `abort_cleanup` |
| `warming_up` | `tts.playback_finished` | 开场已完整播报 | 选首个 task，写 `task.activated` | `task_dispatch` |
| `task_dispatch` | `tts.playback_finished` | 当前任务指令已播完 | 启动等待计时器 | `await_answer` |
| `await_answer` | `device.signal_received` 或 `child.audio_captured` | 收到孩子输入 | 记录输入并发起解析 | `interpret_input` |
| `await_answer` | `child.no_response_timeout` | `reengage_count < 1` | 发送一次重招呼 | `reengagement` |
| `await_answer` | `child.no_response_timeout` | `reengage_count >= 1` 且 `help_level_current=none` | 升级 `help_level -> light_nudge` | `give_hint` |
| `interpret_input` | `nlu.interpreted` | 命中成功条件且 `requires_self_report=false` | 写 `task.completed(result_code=correct 或 completed_with_hint)` | `celebrate_success` |
| `interpret_input` | `nlu.interpreted` | 命中成功条件且 `requires_self_report=true` | 发起封闭式确认 | `self_report_confirm` |
| `interpret_input` | `nlu.interpreted` | `intent=off_topic_chat` | 保留当前任务，记录跑题 | `off_topic_repair` |
| `interpret_input` | `nlu.interpreted` | 低置信 / 错答且 `help_level_current=none` | 写 `help.level_changed none->light_nudge` | `give_hint` |
| `interpret_input` | `nlu.interpreted` | 低置信 / 错答且 `help_level_current=light_nudge` | 写 `help.level_changed -> guided_hint` | `guided_hint` |
| `interpret_input` | `nlu.interpreted` | 低置信 / 错答且 `help_level_current=guided_hint` | 写 `help.level_changed -> step_by_step` | `step_by_step_help` |
| `interpret_input` | `nlu.interpreted` | 低置信 / 错答且 `help_level_current=step_by_step` | 写 `help.level_changed -> demo_mode` | `demo_mode` |
| `self_report_confirm` | `nlu.interpreted` 或 `device.signal_received` | `intent=confirm_done` 或确认按键命中 | 写 `task.completed(result_code=correct 或 completed_with_hint)` | `celebrate_success` |
| `self_report_confirm` | `nlu.interpreted` | `intent=not_done` 或 `intent=unsure` | 根据失败次数升级帮助 | `guided_hint` 或 `step_by_step_help` |
| `self_report_confirm` | `child.no_response_timeout` | 首次确认超时 | 给更明确线索 | `guided_hint` |
| `give_hint` | `tts.playback_finished` | 提示已播完 | 重开等待计时器 | `await_answer` |
| `guided_hint` | `tts.playback_finished` | 提示已播完 | 重开等待计时器 | `await_answer` |
| `step_by_step_help` | `tts.playback_finished` | 当前子步已播完 | 等孩子执行当前子步 | `await_answer` |
| `demo_mode` | `tts.playback_finished` | 示范已播完 | 开启模仿回合等待 | `await_answer` |
| `demo_mode` | `child.no_response_timeout` 或 `nlu.interpreted` | 示范后仍失败 / 明显卡住 | 升级 `help_level -> parent_takeover`，请求家长接管 | `parent_interrupt_hold` |
| `celebrate_success` | `tts.playback_finished` | 鼓励已播完 | 判断是否还有下一任务 | `next_task_ready` |
| `next_task_ready` | `task.next_selected` | 还有下一任务 | 激活下一 task | `task_dispatch` |
| `next_task_ready` | `task.none_left` | 主题任务已清空 | 触发报告生成和收口 | `cooling_down` |
| `cooling_down` | `tts.playback_finished` | 收口话术已播完 | 写 `session.status=ended` 和 `session.ended(end_reason=completed)` | `ended` |
| `off_topic_repair` | `nlu.interpreted` | 孩子已回任务 | 恢复原等待 / 解析链路 | `anchor_state` |
| `reengagement` | `device.signal_received` 或 `child.audio_captured` | 孩子重新响应 | 恢复主线 | `anchor_state` 或 `interpret_input` |
| `reengagement` | `child.no_response_timeout` | 二次沉默且 `help_level_current<demo_mode` | 升级帮助 | `guided_hint` 或 `demo_mode` |
| `safety_hold` | `safety.checked` | 命中 hard stop | 设 `end_reason=safety_stop`，统一收尾 | `abort_cleanup` |
| `parent_interrupt_hold` | `parent.resume_requested` | 家长确认恢复 | 写 `session.status paused->active`，恢复计时器和任务上下文 | `anchor_state` |
| `parent_interrupt_hold` | `parent.end_session_requested` | 家长要求终止 | 设 `end_reason=parent_interrupted` | `abort_cleanup` |
| `abort_cleanup` | `system.cleanup_finished` | 清理动作已完成 | 发布最终投影 | `ended` |

工程实现说明：
- 上表中的 `anchor_state` 是变量，不是固定状态名。
- 同一轮事件判定里只允许命中一条转移规则。
- `state.transition_applied` 仍然应该由状态机在每次真实跳转后补写，A 文档不替代 B 文档里的事件事实层。

## 6. `help_level` 升级机制

沿用 B 文档枚举：
- `none`
- `light_nudge`
- `guided_hint`
- `step_by_step`
- `demo_mode`
- `parent_takeover`

建议规则：

| 当前 `help_level_current` | 典型触发 | 状态机动作 | 下一等级 |
|---|---|---|---|
| `none` | 第一次低置信、第一次错答、第一次确认超时 | 记一次失败，发轻提示 | `light_nudge` |
| `light_nudge` | 再次低置信 / 错答 / 走神后回不来 | 给一个关键线索 | `guided_hint` |
| `guided_hint` | 关键线索后仍做不出来 | 拆成步骤带做 | `step_by_step` |
| `step_by_step` | 分步引导后仍卡住或长时间无输入 | 系统示范一次 | `demo_mode` |
| `demo_mode` | 示范后仍失败或孩子明显需要成人帮助 | 请求家长接管 | `parent_takeover` |

硬规则：
- 同一 task 内默认只升不降，避免来回抖动。
- 只有进入新 task 时，`help_level_current` 才重置为 `none`。
- `help_level_peak` 在 session 级持续记录最高值，供报告和 App 展示。
- 帮助升级只看结构化信号：失败次数、超时次数、低置信次数、确认失败次数、家长接管请求。LLM 不得自己说“我觉得该升一级了”然后直接改状态。

## 7. `end_reason` 结束机制

A 与 B 保持同一组枚举，不新造值：

| `end_reason` | 何时写入 |
|---|---|
| `completed` | 所有主任务完成，正常收口 |
| `child_quit` | 孩子明确表达“不玩了 / 不要了”，且重试后仍拒绝继续 |
| `timeout_no_input` | 经 `reengagement`、帮助升级、示范后仍长期无输入 |
| `network_error` | 关键网络链路断开且无法恢复 |
| `asr_fail_exhausted` | ASR 连续失败达到阈值，已无法继续依赖语音链路 |
| `safety_stop` | `safety_hold` 判定必须停止 |
| `parent_interrupted` | 家长主动终止本轮 |
| `device_shutdown` | 设备掉电、关机、离线 |
| `theme_switched` | 中途换主题，当前 session 需要终止 |
| `system_abort` | 状态机 / 服务端出现不可恢复异常 |

建议硬规则：
- `end_reason` 只允许在 `cooling_down` 正常完结路径或 `abort_cleanup` 异常终止路径写入。
- LLM 可以生成结束话术，但不能决定结束原因。
- `session.ended` 一旦写入，不允许再回主线状态。

## 8. 自报式确认机制

这个机制是 A 文档里必须单独拉出来的，因为儿童积木任务经常不是“听到正确答案就算完成”，而是“孩子说自己搭好了，但设备看不懂实物”。

### 8.1 什么时候必须走自报确认

满足任一条件就建议走 `self_report_confirm`：
- 当前任务结果主要是物理搭建，设备没有足够传感器能直接确认。
- 语义上看起来像完成了，但只靠一句自由表达不够稳。
- 当前任务配置 `requires_self_report=true`。

### 8.2 机器只认什么确认信号

状态机只认结构化确认结果，不直接认长篇自由文本：
- `nlu.interpreted.intent=confirm_done`
- `nlu.interpreted.intent=not_done`
- `nlu.interpreted.intent=unsure`
- `device.signal_received.signal_type=confirm_button`

### 8.3 判定规则

推荐 guard：
- 已先命中过一次“候选成功条件”，不是完全没依据就让孩子自报。
- 确认窗口在短时限内，比如 3 到 5 秒。
- 若孩子确认完成后立刻又说“没有 / 还没”，以后一个为准，避免双写成功。

### 8.4 LLM 话术限制

LLM 在这个状态只干一件事：把确认问清楚。

建议模板风格：
- 说短句
- 给固定选项
- 不要开放追问

合格例子：
- “搭好了就说好了，没好就说还没好。”
- “如果你已经放好了，按一下确认键。”

不合格例子：
- “你能详细描述一下你刚才是怎么搭的吗？”

原因很简单：
- v1 目标是让状态机拿到一个可判定的确认结果，不是继续开自由聊天。

## 9. 哪些判断交给状态机，哪些只允许 LLM 负责话术

| 事项 | 必须由状态机决定 | LLM 只可负责 |
|---|---|---|
| 当前任务是否完成 | 根据结构化事件、guard、确认结果决定是否写 `task.completed` | 把成功鼓励说得自然一点 |
| 进入哪个状态 | 根据 `current_state + event + guard` 命中唯一规则 | 不得自己选状态 |
| `help_level` 是否升级 | 看失败次数、超时、低置信、确认失败、家长介入 | 生成对应等级的提示措辞 |
| 是否进入 `demo_mode` / `parent_takeover` | 由状态机按阈值决定 | 说“我先示范一次”或“请家长帮一下” |
| 是否走 `self_report_confirm` | 看任务配置和传感器覆盖能力 | 问确认句式 |
| 是否跑题 / 需不需要拉回 | 状态机依据结构化 `intent=off_topic_chat` 决定是否切 `off_topic_repair` | 生成拉回语气 |
| 是否触发安全停止 | 安全规则 / 分类器 / guard 决定 | 用安全口吻停下 |
| `end_reason`、`public_stage`、报告触发 | 只能由系统写 | 结束话术、短总结 |

禁止项直接写死：
- LLM 不得直接改 `session.status`。
- LLM 不得直接改 `current_state`。
- LLM 不得直接改 `task.status`。
- LLM 不得直接改 `help_level_current`。
- LLM 不得直接写 `end_reason`。
- LLM 不得因为“感觉像完成了”就跳过 `self_report_confirm`。

## 10. v1 MVP 建议：第一版先实现哪些状态最值

第一版最值的，不是把所有状态都做满，而是先把“完整主链 + 最小失败链 + 最小安全链”打通。

### 10.1 第一优先，必须做

- `session_bootstrap`
- `warming_up`
- `task_dispatch`
- `await_answer`
- `interpret_input`
- `self_report_confirm`
- `give_hint`
- `demo_mode`
- `celebrate_success`
- `next_task_ready`
- `cooling_down`
- `ended`

### 10.2 同批必须带上的兜底

- `reengagement`
- `safety_hold`
- `parent_interrupt_hold`
- `abort_cleanup`

### 10.3 可以第二批再补细

- `guided_hint`
- `step_by_step_help`
- `off_topic_repair`

原因：
- 没有 `self_report_confirm`，积木任务很容易误判完成。
- 没有 `demo_mode`，第一版失败链会断在“提示没用但也不知道怎么继续”。
- 没有 `parent_interrupt_hold` 和 `safety_hold`，就不适合真实儿童场景。
- `guided_hint` 和 `step_by_step_help` 很值，但它们更像帮助层细化；如果资源紧，先用 `give_hint -> demo_mode` 也能把 MVP 跑通。

## 11. 这版 v1 的核心结论

- A 状态机应该把“任务推进、帮助升级、结束机制、家长打断、安全打断”一次性定死，别把这些判断散给 LLM。
- 儿童积木任务的关键不是会不会说话，而是会不会在“看不见实物真相”的情况下稳地确认完成；所以 `self_report_confirm` 必须从 v1 就有。
- 第一版最该追求的是：一条稳定 happy path、一条稳定 failover path、一条稳定 safe-stop path，而不是把话术做得很花。
