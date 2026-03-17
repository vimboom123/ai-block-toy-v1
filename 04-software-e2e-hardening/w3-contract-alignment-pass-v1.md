# W3 Contract Alignment Pass v1

项目：AI积木玩具  
阶段：Phase 4 / W3  
日期：2026-03-17  
范围：`software-e2e/` 对齐 `00-governance/`、`01-product-spec/`、`02-projections/`

补充：
- 本文记录的是 W3 首轮对齐结果。
- 后续剩余 contract/blocker 清理与 reviewer handoff 结论见 `w4-final-contract-cleanup-pass-v1.md`。

图例：
- `+` 已对齐
- `~` 兼容 shim / 历史残影，当前可跑，但不是未来权威合同
- `-` 真实 mismatch，继续放着会把 UI / backend 带偏

## 0. 本轮已落地的低风险修正

+ `public_stage` 从 canonical runner 合同里移除了多余的 `active`，只保留 spec 冻结值域。
+ `EndReason` 移除了文档里不存在的 `no_response_timeout`。
+ `retry_count` 改回 spec 口径：只在失败 task 后再次激活时增加，不再在 `parent.resume_requested` 时误增。
+ `parent.resume_requested` 不再把 `public_stage` 强行写回 `doing_task`，恢复时保留当前公开阶段语义。
+ `awaiting_child_confirmation` 已接到 `session.current_state === self_report_confirm`。
+ timeline 结束态改为优先读 `session.end_reason`，不再把 `session.ended.payload.end_reason` 当最终事实源。
+ timeline 不再把 `parent_report.generated`、`parent.end_session_requested` 直接投成家长时间线 item。
+ report/home 的几个低风险 DTO 偏差已收口：
  - `report_detail_view` fallback 文案改回 spec 口径
  - `task_breakdown.parent_label` fallback 改为 `当前任务`
  - `home_snapshot_view.active_session.entry_cta_text` 改为 `进入会话`
  - home DTO 里多余的 `latest_session_status` / `latest_summary` 已移除
+ batch CLI 过滤掉 `._*` 垃圾 sidecar，`check:phase3` 不再被 macOS 伴生文件打爆。
+ phase3 老 fixture 里的 `public_stage: active` 现在只在 loader 里做兼容归一：`active -> doing_task`，不再污染 canonical 合同。

## 1. Event Names

+ 已对齐：
  - `session.started`
  - `task.activated`
  - `help.level_changed`
  - `task.completed`
  - `task.failed`
  - `parent.interrupt_requested`
  - `parent.resume_requested`
  - `parent.end_session_requested`
  - `safety.checked`
  - `session.ended`
  - `parent_report.generated`
  - `state.transition_applied` 作为内部状态事实事件仍成立

~ 兼容 shim：
  - phase3 老 fixture 仍会喂进 `public_stage: active`；runner 现在只在 loader 里把它归一到 `doing_task`，这不是正式枚举。

- 真实 mismatch：
  - 无。当前 runner 已把 `child.intent_recognized` / `child.answer_incorrect` 收成 fixture 输入别名，真实输出统一为 `nlu.interpreted`；`parent_report.generated` 也已补齐稳定 payload。

## 2. Terminal Statuses

+ 已对齐：
  - `session.status`: `active / paused / ended / aborted`
  - `display_status`: `active / paused / ended / aborted`
  - `report.publish_status`: `draft / published / partial / withdrawn`
  - `task.status`: `pending / active / completed / failed / skipped`
  - `task.result_code`: `correct / completed_with_hint / demo_followed / skipped / failed_confusion / failed_timeout`
  - `public_stage`: `warming_up / doing_task / receiving_hint / celebrating / cooling_down / ended`

~ 兼容 shim：
  - 旧 fixture 输入里的 `public_stage=active` 仍被接受，但只作为 loader 兼容，不属于 canonical runnable contract。

- 真实 mismatch：
  - 无新增枚举 mismatch；这轮把最明显的漂移值域已经清掉了。

## 3. `display_status` / `public_stage` Semantics

+ 已对齐：
  - `deriveDisplayStatus` 现在和 live spec 的优先级一致：`aborted > ended > paused > active`
  - `parent.interrupt_requested` 只写 `status=paused`，不把 `public_stage` 改成伪 stage
  - `parent.resume_requested` 恢复时不再强制回 `doing_task`
  - `session.ended` 统一收口到 `public_stage=ended`

~ 兼容 shim：
  - `awaiting_child_confirmation` 线路已经接上，但目前 fixture 集还没有真正覆盖 `self_report_confirm` 分支，所以这条仍属于“已接线、未充分证明”。

- 真实 mismatch：
  - 无。当前内置 fixture 和 phase3 fixture 已按 Rule A / B / C 跑通：
    - `task.activated + state.transition_applied` 按单条 `task_progress` 折叠
    - 连续 `help.level_changed` 按链式 30 秒窗口去噪
    - 15 秒内 `parent.interrupt_requested + parent.resume_requested` 会被去抖过滤

## 4. Report Publish Status

+ 已对齐：
  - `session.status=ended && end_reason=completed` -> `publish_status=published`
  - `session.status=aborted` 或未完整结束 -> `publish_status=partial`
  - home 只展示 `published` 报告卡，partial 不冒充“最新报告”
  - report detail DTO 已稳定包含 `summary.publish_status`

~ 兼容 shim：
  - phase3 老 fixture 还在断言一些 home 级旧字段，但这些字段已经不再属于 canonical home DTO。

- 真实 mismatch：
  - 无。`parent_report.generated` 现已稳定带上 `report_id / report_version / summary_version / publish_status / source_event_range`。

## 5. Retry / Help / Safety / Parent Takeover

+ 已对齐：
  - `retry_count` 已改回“failed 后再次激活”才记数
  - `help_level_peak` 仍按 `none -> light_nudge -> guided_hint -> step_by_step -> demo_mode -> parent_takeover` 累计
  - `safety.checked` producer 已改为 `system`
  - `parent_takeover` 时 live `current_task.parent_note=null`，由 `parent_action` 承担介入提示
  - `safety_stop` 会把 live `display_status` 置为 `aborted`，report 置为 `partial`，home 置顶 `safety_stop` alert

~ 兼容 shim：
  - 老 fixture 的 `public_stage=active` 仍在 loader 层归一，说明 phase3 素材库还没彻底清干净。

- 真实 mismatch：
  - `achievement_tags / notable_moments / parent_text` 仍是最小模板实现，但这不再阻塞本轮 UI 开工。

## 6. Key DTO Fields

### 6.1 Live

+ 已对齐：
  - `header.public_stage`
  - `header.public_stage_text`
  - `header.display_status`
  - `progress.turn_count / completed_task_count / retry_count`
  - `current_task.parent_label / help_level_current / parent_note`
  - `current_task.awaiting_child_confirmation`
  - `parent_action.*`

- 真实 mismatch：
  - 无。新增 `fx_self_report_confirm.yaml`，已用 checkpoint 证明 `awaiting_child_confirmation=true`，并覆盖后续 hint / report / timeline 表现。

### 6.2 Timeline

+ 已对齐：
  - `timeline_item_id`
  - `display_type`
  - `display_text`
  - `severity`
  - `related_task`
  - 结束态已优先以 `session.end_reason` 为准

~ 兼容 shim：
  - 无。已正式拍板：`child.no_response_timeout`、`safety.checked` 不是 direct timeline source event；runner / README / fixture 断言已按这套口径统一。

- 真实 mismatch：
  - 无。

### 6.3 Report

+ 已对齐：
  - `summary.publish_status`
  - `summary.end_reason`
  - `safety.safety_notice_level`
  - `task_breakdown.task_id / parent_label / status / result_code / attempt_count / help_level_peak / parent_note`

- 真实 mismatch：
  - `achievement_tags / notable_moments / parent_text` 现在只是最小模板级实现，离治理文档里的“规则聚合 + worker payload 合同”还有距离

### 6.4 Home

+ 已对齐：
  - canonical DTO 现在只保留 spec 里的 `active_session / latest_report / continue_entry / alerts / meta`
  - 多余的 `latest_session_status / latest_summary` 已清掉
  - `active_session.entry_cta_text` 已回到 `进入会话`

~ 兼容 shim：
  - `buildHomeSnapshotView` 本身合同已对齐，但 `runFixture()` 里的 home 组装仍只是单 session 演示，不是完整首页 BFF 的历史聚合实现。

- 真实 mismatch：
  - `runFixture()` 里的 `continue_entry` 仍只演示“aborted 且非 safety_stop”这一条，不代表 home spec 里完整的候选选择逻辑

## 7. Must Fix Before UI

1. `fixture step -> canonical event` 适配已落地：fixture 可继续吃旧别名，但 runner 输出只发 canonical `nlu.interpreted`。
2. timeline 折叠规则已覆盖当前 fixture 路径：hint 去噪、pause/resume 去抖、自带 task start 吸收。
3. `parent_report.generated` payload 合同与 `report.confidence_overall` 最小保守聚合已落地。
4. `self_report_confirm` fixture 已补上，并通过 step checkpoint 证明 `awaiting_child_confirmation`。
5. 已正式拍板：`child.no_response_timeout` / `safety.checked` 不直接进 timeline source whitelist。

## 8. Verification

+ 已验证：`cd software-e2e && npm run check:all-fixtures`
+ 结果：built-in fixtures 全绿，phase3 fixture bank 全绿
