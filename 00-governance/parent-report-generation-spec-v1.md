# Parent Report 生成机制说明 v1

项目：AI积木玩具  
对象：`parent_report` 生成链路  
日期：2026-03-16  
状态：v1 草案，供后端 / 报告 worker / App 报告页联调使用

## 1. 这份文档解决什么

这份只解决一件事：
- `parent_report` 到底什么时候生成、由谁生成、字段怎么来、异常终止时怎么降级。

它解决的是：
- 生成触发时机
- normal / aborted / safety_stop 路径下是否生成报告
- `parent_summary / follow_up_suggestion / achievement_tags / notable_moments / confidence_overall` 的来源规则
- partial report 的定义

## 2. 生成责任

v1 统一采用：
- `system/report-worker` 负责组装 `parent_report`
- LLM 只参与生成“可读文本候选”，不直接写最终 report 主记录
- 最终 `parent_report` 必须由后端 worker 落库

## 3. 触发时机

### 3.0 worker 竞态处理策略

`parent_report` worker 读取 `session.ended` 相关事实时，v1 统一采用：
- 以 `session.status + session.end_reason + ended_at` 为最终事实源
- 若收到生成触发时 `session.end_reason` 仍未落库：
  1. 先做短退避重试（例如 200ms / 500ms / 1000ms）
  2. 重试后仍为空，则允许按 `publish_status='partial'` 生成降级报告
  3. 同一 `session_id + report_version` 必须幂等，避免重复生成多份报告
- 不要求 event 与 session 主记录完全原子写入，但 worker 必须容忍这个短暂窗口

### 3.1 正常完成

当出现：
- `session.status='ended'`
- 且 `session.end_reason='completed'`

则：
- 触发 `parent_report` 生成
- 生成成功后写 `parent_report.generated`

### 3.2 提前终止 / 中止

当出现：
- `session.status='aborted'`

则默认仍生成报告，但可降级为 partial report。

适用 `end_reason`：
- `parent_interrupted`
- `network_error`
- `asr_fail_exhausted`
- `device_shutdown`
- `theme_switched`
- `system_abort`
- `safety_stop`

### 3.3 partial report 规则

若满足任一条：
- `end_reason='safety_stop'`
- 关键任务统计不完整
- 事件窗口不完整但 session 已结束

则允许生成 partial report。

建议把 `parent_report.publish_status` 扩成：
- `draft`
- `published`
- `partial`
- `withdrawn`

## 4. 字段来源规则

### 4.1 结构化字段

- `theme_name_snapshot`：来自 session 绑定 theme 快照
- `duration_sec`：`ended_at - started_at`
- `completed_task_count`：来自 session 聚合字段
- `task_completion_rate`：
  - 分子 = `status='completed'` 的 task 数
  - 分母 = 本 session 中被激活过的 task 数
- `help_level_peak`：来自 session 聚合字段
- `confidence_overall`：
  - v1 规则：取本轮 task 结果相关置信等级的保守聚合
  - 若存在 `very_low` -> `very_low`
  - 否则若存在 `low` -> `low`
  - 否则若存在 `medium` -> `medium`
  - 否则 `high`

### 4.2 文本字段

#### `parent_summary`
- 允许 LLM 参与生成候选
- 但必须基于结构化输入模板生成
- 输入不得包含 raw transcript / prompt / internal note
- 若 LLM 不可用，回退静态模板：
  - `本轮完成了 {completed_task_count} 个任务，整体已结束。`

#### `follow_up_suggestion`
- 允许 LLM 参与生成候选
- 若 LLM 不可用，回退静态模板：
  - `可以让孩子复述刚才完成的关键步骤。`

#### `achievement_tags`
- 优先由规则模板 + task result 生成
- LLM 仅可补候选，不得自由发散

#### `notable_moments`
- 来自 `task.completed / task.failed / help.level_changed / session.ended` 等事件的安全改写摘要
- 必须脱敏、压缩、去掉内部状态机术语

## 5. 任务结果写入边界

### 5.1 `failed` vs `skipped`

v1 规则先写死：
- `failed`：任务已实际进入尝试，且未达成完成条件就结束
- `skipped`：任务未真正展开尝试，因上层中断 / 切换 / 提前终止被跳过

典型例子：
- 孩子尝试过但最终未完成 -> `failed`
- 还没轮到这个任务，session 就结束 -> `skipped`
- `safety_stop` 导致当前正在尝试任务中断 -> 当前 task 记 `failed`

### 5.2 `retry_count`

v1 对家长端统一口径：
- `session.retry_count` 只统计“任务失败后再次激活”的重试次数
- `task.attempt_count` 负责表达单任务内的多次尝试
- 普通 hint 升级、同一 task 内继续尝试，只增加 `task.attempt_count`
- 只有 task 明确走到 `failed`，且后续该任务再次被激活，才增加 `session.retry_count`
- `session.retry_count` 不是各 task `attempt_count` 的求和
- 报告文案不得把 `retry_count=0` 误写成“没有多次尝试”

## 6. self_report_confirm 可见性

v1 不把内部状态名直接暴露给前端。

若 session 处于 `self_report_confirm`：
- 默认仍归在 `public_stage='doing_task'`
- v1 正式建议在 `session_live_view` 增加公开字段：
  - `awaiting_child_confirmation: boolean`
- 该字段只表达“当前正在等孩子确认是否完成”，不暴露内部状态机节点名
- 在该字段尚未落地前，报告链路不依赖前端识别该内部状态

## 7. 事件与落库

生成完成后应写：
- `parent_report.generated`

建议 payload：
- `report_id`
- `report_version`
- `publish_status`
- `source_event_range`

## 8. 下一步

这份补完后，下一步继续：
1. `report_detail_view` 独立实现说明
2. 首页 / 报告页字段复用矩阵
3. 软件全链路 mock runner 所需 fixture 设计
