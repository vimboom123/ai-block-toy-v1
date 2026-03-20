# Software E2E Fixtures v1

项目：AI积木玩具  
阶段：软件全链路跑通准备  
日期：2026-03-17  
状态：v1 草案，供 mock runner / golden case / projection 对齐使用

## 1. 这份文档解决什么

这份不写实现代码，只先把软件闭环验证要喂给系统的 fixture 定下来。

目标只有两个：
1. 让 mock runner 有稳定输入
2. 让 projection / report 有可比对的 golden output

fixture 默认分两层：
- `input script`：模拟孩子、系统、家长、超时、安全中断等外部触发
- `expected output`：期望的状态迁移、事件序列、实体结果、projection 结果

---

## 2. fixture 统一结构

每个 fixture 建议至少包含这些块：

```yaml
id: fx_happy_path_basic
category: happy_path
theme_code: build_bridge_v1
seed_profile:
  child_age: 5
  locale: zh-CN
session_bootstrap:
  public_stage: warming_up
  initial_state: warming_up
steps:
  - at: 0s
    actor: system
    type: session.started
  - at: 8s
    actor: child
    type: child.intent_recognized
    payload:
      intent: ready_to_start
      confidence_score: 0.94
expected:
  terminal_session_status: ended
  terminal_public_stage: ended
  completed_task_count: 2
  report_publish_status: published
```

硬规则：
- `steps` 按业务发生顺序写，不按写库时间写
- 每一步都必须能映射到标准 event 或状态机 guard
- `expected` 里必须同时写终态实体和关键 projection 断言
- 不允许只验 event，不验 projection；也不允许只验 projection，不验状态迁移

---

## 3. fixture 分类与最小样本集

本轮先冻结 6 个最小 fixture，够把主链路跑起来。

### 3.1 FX-01 Happy path / 基础顺跑

**fixture id**：`fx_happy_path_basic`

**目标**：验证最正常的一条闭环。

**路径**：
- session 创建
- warming_up
- task_1 激活
- 孩子正常回答 / 搭建
- task_1 completed
- task_2 激活
- task_2 completed
- celebrating / cooling_down
- session ended
- report published

**必须断言**：
- `session.status = ended`
- `session.public_stage = ended`
- `completed_task_count = 2`
- `retry_count = 0`
- `help_level_peak = none | light_nudge`（按脚本固定）
- `session_live_view.current_task = null`
- `report_detail_view.summary` 有完整摘要
- timeline 至少出现：`session.started`、`task.activated`、`task.completed`、`session.ended`、`report.generated`

### 3.2 FX-02 Hint escalation / 提示升级后完成

**fixture id**：`fx_hint_escalation_complete`

**目标**：验证 `none -> light_nudge -> guided_hint` 这条升级链。

**路径**：
- task 激活
- 孩子低置信 / 错答
- 进入 `light_nudge`
- 再次失败
- 进入 `guided_hint`
- 孩子完成
- task completed
- session 正常结束

**必须断言**：
- `task.help_level_peak = guided_hint`
- `session.help_level_peak = guided_hint`
- `retry_count = 0`
- live view 的 `current_task.parent_note` 会反映“系统已给出关键线索”一类安全文案
- report 里有“在提示下完成”这类 achievement，而不是“独立完成”

### 3.3 FX-03 Timeout / no response 后重新拉回

**fixture id**：`fx_timeout_reengagement_resume`

**目标**：验证等待超时、reengagement、继续主线。

**路径**：
- task 激活
- 超时无响应
- 进入 reengagement
- 系统轻提醒或 demo
- 孩子恢复参与
- task completed
- session ended

**必须断言**：
- timeline 有 timeout 相关事件
- `session.status` 不能错误写成 `paused`
- `display_status` 仍应是 `active`
- report 中允许出现“中途分心后重新投入”这类已脱敏总结

### 3.4 FX-04 Parent takeover / 家长接管后恢复

**fixture id**：`fx_parent_takeover_resume`

**目标**：验证连续失败后进入家长介入，再恢复继续。

**路径**：
- task 连续失败
- 进入 `parent_takeover`
- 触发 `parent_interrupt_hold`
- 家长恢复
- 回到主任务或降级任务
- session 完成

**必须断言**：
- `session.status` 在 hold 期间为 `paused`
- `display_status` 对家长端呈现为“已暂停/待家长处理”
- timeline 有 parent intervention 片段，但不暴露内部 prompt
- `report.publish_status = published`
- report 对家长只写结果与建议，不写原始失败细节

### 3.5 FX-05 Parent takeover / 家长终止

**fixture id**：`fx_parent_takeover_terminate`

**目标**：验证家长中断后直接结束。

**路径**：
- task 连续失败
- 进入家长介入
- 家长选择终止
- 进入 `abort_cleanup`
- 生成 partial report

**必须断言**：
- `session.status = aborted`
- `end_reason = parent_interrupted`
- `report.publish_status = partial`
- live view 不再暴露 current task
- home snapshot 正确显示“已提前结束”

### 3.6 FX-06 Safety stop / 安全停止

**fixture id**：`fx_safety_stop_partial_report`

**目标**：验证命中安全停止的兜底链。

**路径**：
- session 运行中
- 命中 safety stop
- 进入 `abort_cleanup`
- 生成 partial report
- session aborted

**必须断言**：
- `session.status = aborted`
- `end_reason = safety_stop`
- `report.publish_status = partial`
- `safety_notice_level` 正确落到 report
- timeline / live view 只展示安全可读层，不暴露敏感 payload

---

## 4. 事件层断言模板

每个 fixture 至少校验这 4 类事件：

1. **session 级**
   - `session.started`
   - `session.ended` 或 `session.aborted`

2. **state 级**
   - `state.transition_applied`
   - 必须能串出完整状态迁移链

3. **task 级**
   - `task.activated`
   - `task.completed / task.failed / task.skipped`

4. **projection / report 级**
   - `projection.live.updated`
   - `projection.timeline.updated`
   - `report.generated`

建议：
- golden 对比不要比全量 JSON；优先比固定关键字段，避免无意义抖动
- 但 `state_before / state_after / seq_no / report_publish_status` 这类关键字段必须精确比

---

## 5. projection 层最小断言

### 5.1 live view

每个 fixture 至少断言：
- `header.public_stage`
- `header.display_status`
- `progress.completed_task_count`
- `progress.retry_count`
- `current_task.parent_label`（若存在）
- `parent_action.need_parent_intervention`

### 5.2 timeline view

每个 fixture 至少断言：
- item 数量大于 0
- 首尾事件合理
- 关键阶段节点存在
- 文案全部来自安全可读层

### 5.3 report detail view

结束型 fixture 至少断言：
- `theme_name_snapshot`
- `duration_sec`
- `completed_task_count`
- `help_level_peak`
- `parent_summary`
- `follow_up_suggestion`
- `publish_status`

### 5.4 home snapshot view

至少断言：
- 最近一次 session 状态
- 最近主题名
- 是否显示“进行中 / 已结束 / 已提前结束”
- 摘要卡片不泄漏内部状态码

---

## 6. 建议目录与命名

建议后续真实 fixture 文件落到：

- `fixtures/fx_happy_path_basic.yaml`
- `fixtures/fx_hint_escalation_complete.yaml`
- `fixtures/fx_timeout_reengagement_resume.yaml`
- `fixtures/fx_parent_takeover_resume.yaml`
- `fixtures/fx_parent_takeover_terminate.yaml`
- `fixtures/fx_safety_stop_partial_report.yaml`

golden output 建议分目录：

- `goldens/live/*.json`
- `goldens/timeline/*.json`
- `goldens/report/*.json`
- `goldens/home/*.json`

---

## 7. 先后顺序建议

不要一口气把 6 个都写满。

建议顺序：
1. `fx_happy_path_basic`
2. `fx_hint_escalation_complete`
3. `fx_parent_takeover_resume`
4. `fx_parent_takeover_terminate`
5. `fx_timeout_reengagement_resume`
6. `fx_safety_stop_partial_report`

原因：
- 先把正常链路跑通
- 再补帮助升级
- 再补暂停 / 终止
- 最后处理安全与超时这种更容易引爆边界的链路

---

## 8. 当前落地状态

这阶段 fixture 不求花哨，先求**覆盖主状态迁移、主实体物化、四类 projection 断言**。

现在不只是"把 6 个最小样本冻住"了，而是已经把它们真正落成了 fixture 文件，并顺手把 phase 3 coverage matrix 里点名缺失的 5 个高风险样本也一并补成了真实 YAML：

- `fixtures/fx_happy_path_basic.yaml`
- `fixtures/fx_hint_escalation_complete.yaml`
- `fixtures/fx_timeout_reengagement_resume.yaml`
- `fixtures/fx_parent_takeover_resume.yaml`
- `fixtures/fx_parent_takeover_terminate.yaml`
- `fixtures/fx_safety_stop_partial_report.yaml`
- `fixtures/fx_timeout_escalation_abort.yaml`
- `fixtures/fx_safety_warn_continue.yaml`
- `fixtures/fx_parent_takeover_reenter.yaml`
- `fixtures/fx_network_error_partial_report.yaml`
- `fixtures/fx_system_abort_partial_report.yaml`

也就是说，后面 Codex 再开写 mock runner / golden case，不用再从文档里手抄 fixture 口径，已经有真实输入骨架可以直接消费了。