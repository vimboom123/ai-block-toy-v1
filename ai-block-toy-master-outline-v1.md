# AI积木玩具总纲 v1

项目：AI积木玩具  
范围：v1 总纲整合文档  
日期：2026-03-16  
状态：内部推进基线，可直接作为后续产品 / 后端 / App / 协同分工主线

## 1. 文档定位

这份文档只做总纲，不展开成长 PRD。

它的作用只有 3 个：
- 把 AI积木玩具 v1 的产品主线和工程主线定住。
- 把状态机、后端 schema、App 同步口径收成一套统一基线。
- 给工作室后续拆工、实现、评审、验收提供同一份上位参照。

一句话定性：
- 这是儿童 AI 积木玩具，不是开放式陪聊玩具。
- v1 的核心不是“会不会聊”，而是“能不能把一轮真实儿童任务稳地跑完，并让家长看懂过程和结果”。

## 2. 产品定位

AI积木玩具 v1 的目标很直接：
- 面向儿童，围绕单一主题任务组织一次完整互动。
- 让孩子在搭建、理解、回应、确认完成的过程中推进任务。
- 让家长在手机 App 上看懂“现在在干什么、顺不顺、要不要介入、结束后该怎么跟进”。

v1 不追求的东西也先说死：
- 不做开放聊天玩具。
- 不让 LLM 自己决定业务流程。
- 不让家长端直连后端原始事件和内部状态细节。

## 3. 核心架构

总原则只有一句：
- 状态机控流程，LLM 控话术。

### 3.1 状态机负责什么

- `session` 主流程推进
- `task` 激活、完成、失败、切换
- `help_level` 升级
- `self_report_confirm` 完成确认
- `session.status / public_stage` 双层口径维护
- `public_stage` 对外阶段映射
- `end_reason` 写入
- 安全打断、家长打断、异常收尾

### 3.2 LLM 负责什么

- 语义理解候选：`intent / slots / confidence`
- 话术候选：`reply_text / tts_text / style_key`

### 3.3 LLM 明确不负责什么

- 不得直接写 `session.status`
- 不得直接写 `current_state`
- 不得直接写 `task.status`
- 不得直接写 `help_level_current`
- 不得直接写 `end_reason`
- 不得跳过状态机 guard 自行宣布“完成了”

### 3.4 后端层次

- `event` 是事实源，保留完整时序事实。
- `session / task / parent_report` 是查询友好的当前态或汇总态。
- `projection / BFF view` 是家长端唯一该读的公开层。
- App 先读 projection，再读实体公开字段，不碰 raw event。

## 4. 状态机主链

v1 主链先按下面这条骨架推进：

`session_bootstrap`
→ `warming_up`
→ `task_dispatch`
→ `await_answer`
→ `interpret_input`
→ `self_report_confirm`（需要时）
→ `celebrate_success`
→ `next_task_ready`
→ `cooling_down`
→ `ended`

这条主链的工程含义：
- `session_bootstrap` 负责建会话、绑主题快照、初始化状态。
- `warming_up` 只负责把孩子拉进本轮主题，不预支后面流程。
- `task_dispatch` 负责激活当前任务并说清楚期待动作。
- `await_answer` 只等输入，不做语义判断。
- `interpret_input` 把输入变成结构化候选，再由状态机判定成功、失败、跑题或升级帮助。
- `self_report_confirm` 是积木场景的关键补丁。设备看不懂实物时，必须走封闭式确认，不然完成判定不稳。
- `celebrate_success` 只做短鼓励和计数更新。
- `next_task_ready` 只决定继续下一任务还是收尾。
- `cooling_down` 负责短收口并触发家长报告。

统一状态口径再补一句：
- `session.status=paused` 只对应 `parent_interrupt_hold`，并且必须保留恢复点。
- `session.status=ended` 只对应正常完成；`session.status=aborted` 对应不可恢复的提前终止。
- `parent_interrupted` 只作为 `end_reason`，不单独扩成 `session.status` 或 `public_stage`。
- `celebrate_success` 是状态机状态，不是 `task_type`；`task_type` 不单独设 `celebrate`。

横切兜底链默认并行存在，但不抢主线定义权：
- `reengagement`：沉默 / 走神时重新抓回注意力
- `safety_hold`：命中安全规则立即冻结
- `parent_interrupt_hold`：家长打断或家长接管
- `abort_cleanup`：统一异常终止和清理

第一版判断重点不是话术花样，而是 3 条链都要稳：
- happy path
- failover path
- safe-stop path

## 5. 后端事实源 / 投影层

### 5.1 事实源

后端统一按“事件为事实源，实体为物化视图”来建：
- `event`：唯一时序事实源，append-only
- `session`：会话当前态与统计摘要
- `task`：session 内任务实例
- `theme`：玩法主题配置
- `parent_report`：会后家长报告

关键约束：
- 真正驱动流程的推进来自状态机规则命中和 `state.transition_applied`
- `asr.transcribed`、`nlu.interpreted`、`assistant.reply_prepared` 都只是候选结果，不是状态事实
- `help_level`、`end_reason`、`public_stage`、`session_status` 这组枚举从 v1 就定住

### 5.2 投影层

家长端只读公开投影，不读原始事实流。

v1 推荐最小投影层：
- `session_public_view`
- `home_snapshot_view`
- `session_live_view`
- `session_timeline_view`
- `report_detail_view`
- `content_catalog_view`
- `settings_view`

统一口径：
- 投影层负责脱敏、改写、人话化、聚合
- 原始 transcript、prompt、rule、guard、内部错误栈不出现在家长端
- `public_stage` 是家长端同步主键，不是 `current_state`
- Projection / DTO 字段生成规则简表见 C 文档第 2.1 节，以那张表作为页面字段来源的统一参照

## 6. 家长端手机 App 五页定位

家长端是手机 App，不是后台控制台。五页各自只回答一个明确问题。

### 6.1 首页

- 看现在有没有在玩
- 看最近一次结果怎么样
- 给家长一个最该点的入口

### 6.2 会话页

- 看当前在做什么
- 看系统是在正常推进、给提示，还是需要家长介入
- 它是实时同步核心，消费 `session_live_view + session_timeline_view`

### 6.3 报告页

- 看这轮完成了什么
- 看亮点、卡点、帮助等级和家长跟进建议

### 6.4 内容页

- 看有哪些主题可玩
- 看适龄、难度、预计时长、是否值得继续上次进度

### 6.5 设置页

- 看设备状态、儿童档案、通知开关、内容 / 安全边界
- v1 先收成极简设置，不做全量后台配置

五页优先级也先定死：
- 第一优先：首页、会话页、报告页
- 第二优先：内容页
- 第三优先：设置页极简版

## 7. MVP 第一阶段范围

第一阶段不是把所有帮助层和页面都做满，而是把可运行闭环先打通。

### 7.1 必须打通的状态

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

### 7.2 同批必须带上的兜底

- `reengagement`
- `safety_hold`
- `parent_interrupt_hold`
- `abort_cleanup`

### 7.3 可以放第二批的细化

- `guided_hint`
- `step_by_step_help`
- `off_topic_repair`

### 7.4 第一阶段 App / 后端交付边界

- 后端先把 `event + session + task + parent_report + 最小 projection` 跑通
- App 先做 首页 / 会话页 / 报告页
- 内容页先挂轻入口
- 设置页只做设备状态、通知开关、内容边界

第一阶段完成标准：
- 一条主题能完整跑完
- 完成、失败、安全中止都能收尾
- 家长端能看懂实时阶段和结束报告
- 不需要前端自己拼状态机逻辑

## 8. 当前已完成文档索引

已存在并已纳入这份总纲的文档：
- [ai-block-toy-state-machine-v1.md](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-state-machine-v1.md)
- [ai-block-toy-backend-schema-v1.md](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-backend-schema-v1.md)
- [ai-block-toy-app-sync-map-v1.md](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-app-sync-map-v1.md)

当前工作区未检出独立文件 `ai-block-toy-parent-app-ui-v1.md`。家长端五页定位目前已由 `app-sync-map` 文档覆盖，这份总纲按现有可见文档整合。

本次新增总纲：
- [ai-block-toy-master-outline-v1.md](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-master-outline-v1.md)

## 9. 下一步执行顺序

1. 先冻结 v1 的状态机主链、`help_level`、`end_reason`、`public_stage` 枚举，别再边做边漂。
2. 按 `event -> session/task/parent_report -> projection` 的顺序落后端，不要先做 App 拼装层。
3. 先做 `session_live_view`、`session_timeline_view`、`report_detail_view` 这 3 个核心投影。
4. 用一个单主题样例把 happy path、failover path、safe-stop path 跑通。
5. App 第一批只做 首页 / 会话页 / 报告页，验证家长端价值闭环。
6. 闭环稳定后，再补 `guided_hint`、`step_by_step_help`、`off_topic_repair` 和 内容 / 设置页细化。
