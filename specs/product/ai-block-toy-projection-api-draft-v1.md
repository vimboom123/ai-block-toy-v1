# AI积木玩具 Projection / API 草案 v1

项目：AI积木玩具  
范围：Projection / App BFF 接口合同草案  
日期：2026-03-16  
状态：v1 草案，可直接作为状态机 / 后端 schema / 家长端 App 对接口径

## 1. 文档定位

这份只解决 3 个核心 projection / API：

- `session_live_view` -> `GET /app/sessions/:id/live`
- `session_timeline_view` -> `GET /app/sessions/:id/timeline`
- `report_detail_view` -> `GET /app/reports/:id`

目标只有一个：
- 把状态机、后端实体 / 事件、家长端页面之间的读接口合同定住。

不是这份文档要做的事：
- 不展开成长 PRD
- 不让 App 直接扫 raw `event`
- 不重新发明状态名、枚举和值域

## 2. 统一口径

- App 只读 projection / public view，不读 `session.current_state`、`event.payload_private`、raw transcript、prompt、`rule_id`、`guard_expr`。
- `public_stage` 继续只用既有枚举：`warming_up / doing_task / receiving_hint / celebrating / cooling_down / ended`。
- `session.status=paused` 只对应 `parent_interrupt_hold`；暂停不单独生成新的 `public_stage`，只在展示层叠加 `display_status`。
- `help_level`、`end_reason`、`task_result_code` 继续沿用 B 文档枚举，不在本草案里扩值。
- 事件聚合只在后端做。App 读到的是“脱敏 + 人话化 + 聚合后”的字段，不自己重建状态机逻辑。

## 3. `session_live_view`

**接口**

`GET /app/sessions/:id/live`

**用途**

- 给家长端会话页提供“当前会话快照”。
- 回答 3 个问题：现在进行到哪、当前任务是什么、要不要家长介入。

**给哪个 App 页面用**

- 主用：会话页
- 复用：首页里的“正在进行中的会话卡片”

**主要来源表 / 投影**

- `session_public_view` 作为公开基线
- `session`：会话当前态、阶段、计数、结束信息
- 当前 `task`：当前任务卡片
- `event` 过滤聚合：用于生成 `display_status`、介入提示和展示文案

**建议刷新触发**

- `task.activated`
- `state.transition_applied`
- `help.level_changed`
- `task.completed`
- `task.failed`
- `parent.interrupt_requested`
- `parent.resume_requested`
- `session.ended`

**关键返回字段**

```json
{
  "session_id": "ses_xxx",
  "header": {
    "public_stage": "doing_task",
    "public_stage_text": "正在完成任务",
    "display_status": "active",
    "started_at": "2026-03-16T09:30:00Z",
    "ended_at": null
  },
  "progress": {
    "turn_count": 4,
    "completed_task_count": 1,
    "retry_count": 2
  },
  "current_task": {
    "parent_label": "搭一座小桥",
    "help_level_current": "guided_hint",
    "parent_note": "系统已给出一个关键线索"
  },
  "session_summary": {
    "parent_summary_short": "这一轮正在推进第二个任务"
  },
  "parent_action": {
    "need_parent_intervention": false,
    "intervention_reason_text": null,
    "suggested_action_text": null
  },
  "meta": {
    "projection_version": "v1",
    "generated_at": "2026-03-16T09:31:40Z"
  }
}
```

**字段说明**

| 字段 | 字段说明 | 主要来源 | 来源归类 |
|---|---|---|---|
| `header.public_stage` | 家长端主阶段，App 只认这个，不认 `current_state` | `session.public_stage` | `session` |
| `header.public_stage_text` | `public_stage` 的人话文案 | `session.public_stage` + projection 文案映射 | `session` |
| `header.display_status` | 展示态；用于叠加 `paused / ended / aborted` | `session.status`、`session.end_reason`、最近打断/恢复/结束事件 | `session + event 聚合` |
| `header.started_at` | 会话开始时间 | `session.started_at` | `session` |
| `header.ended_at` | 会话结束时间；未结束时为 `null` | `session.ended_at` | `session` |
| `progress.turn_count` | 当前有效互动轮数 | `session.turn_count` | `session` |
| `progress.completed_task_count` | 已完成任务数 | `session.completed_task_count` | `session` |
| `progress.retry_count` | 当前会话累计重试次数 | `session.retry_count` | `session` |
| `current_task.parent_label` | 当前任务给家长看的名称 | 当前 `task.parent_label` | `task` |
| `current_task.help_level_current` | 当前任务帮助等级 | 当前 `task.help_level_current` | `task` |
| `current_task.parent_note` | 当前任务家长摘要 | 当前 `task.parent_note`，必要时补一点事件聚合摘要 | `task` 为主，必要时 `event 聚合` |
| `session_summary.parent_summary_short` | 会话级短摘要 | `session.parent_summary_short` | `session` |
| `parent_action.need_parent_intervention` | 是否需要家长马上看一眼 | `task.help_level_current`、`session.status`、`session.end_reason`、最近介入类事件 | `session + task + event 聚合` |
| `parent_action.intervention_reason_text` | 为什么需要介入 | `help.level_changed`、`parent.interrupt_requested`、`session.ended` 的安全改写结果 | `event 聚合` |
| `parent_action.suggested_action_text` | 建议家长怎么做一句 / 做一步 | 按 `help_level_current`、`session.status`、`end_reason` 走模板 | `session + task + event 聚合` |

**实现要点**

- `public_stage` 直接取 `session.public_stage`，不要让 App 自己从状态机状态反推。
- `display_status` 叠加暂停 / 提前结束信息，但不新增 stage。
- `current_task` 在 `cooling_down` 或 `ended` 时允许为 `null`。
- 介入卡的判断权在后端，不在前端。

## 4. `session_timeline_view`

**接口**

`GET /app/sessions/:id/timeline`

**用途**

- 给会话页提供“家长可读”的实时过程时间线。
- 只保留安全、关键、可理解的过程节点，不暴露 raw event 流。

**给哪个 App 页面用**

- 主用：会话页里的时间线模块

**主要来源表 / 投影**

- `event` 的安全事件子集二次投影
- 辅助用当前 / 历史 `task.parent_note` 做任务完成或失败的人话补充

**建议刷新触发**

- 与 `session_live_view` 保持同频
- 主要消费这些事件：`task.activated`、`state.transition_applied`、`help.level_changed`、`task.completed`、`task.failed`、`parent.interrupt_requested`、`parent.resume_requested`、`session.ended`

**关键返回字段**

```json
{
  "session_id": "ses_xxx",
  "items": [
    {
      "timeline_item_id": "tl_ses_xxx_bucket01",
      "occurred_at": "2026-03-16T09:31:10Z",
      "display_type": "task_progress",
      "display_text": "开始当前任务：搭一座小桥",
      "severity": "info",
      "related_task": {
        "task_id": "task_001",
        "parent_label": "搭一座小桥"
      },
      "meta": {
        "source_event_count": 2,
        "source_event_types": ["task.activated", "state.transition_applied"]
      }
    },
    {
      "timeline_item_id": "tl_ses_xxx_bucket02",
      "occurred_at": "2026-03-16T09:31:40Z",
      "display_type": "hint_given",
      "display_text": "系统已给出一个关键线索，正在继续引导",
      "severity": "warning",
      "related_task": {
        "task_id": "task_001",
        "parent_label": "搭一座小桥"
      },
      "meta": {
        "source_event_count": 1,
        "source_event_types": ["help.level_changed"]
      }
    }
  ],
  "meta": {
    "projection_version": "v1",
    "generated_at": "2026-03-16T09:31:40Z",
    "events_until": "2026-03-16T09:31:40Z",
    "has_earlier_items": false
  }
}
```

**字段说明**

| 字段 | 字段说明 | 主要来源 | 来源归类 |
|---|---|---|---|
| `items[].occurred_at` | 时间线显示时间 | `event.occurred_at` | `event 聚合` |
| `items[].timeline_item_id` | timeline item 稳定 id；格式和稳定性规则以后续 timeline spec 为准 | projection 生成 | `event 聚合` |
| `items[].display_type` | 给 App 选卡片样式的展示类型；不是 raw `event_type` 直出，v1 以 timeline spec 冻结值域为准 | `event.event_type` 折叠改写 | `event 聚合` |
| `items[].display_text` | 给家长看的过程描述 | `event.payload_public` 为主，必要时结合 `task.parent_note` 改写 | `event 聚合`，必要时补 `task` |
| `items[].severity` | 提醒强弱；用于普通、提醒、异常样式区分 | `event.caution_level` 或事件类型映射 | `event 聚合` |
| `items[].related_task` | 当前 timeline item 关联的任务信息；不能稳定关联时为 `null` | `task + event` | `event 聚合`，必要时补 `task` |
| `items[].meta.source_event_count` | 折叠进该 item 的 source event 数量 | projection 生成 | `event 聚合` |
| `items[].meta.source_event_types` | 去重后的 source event type 列表 | projection 生成 | `event 聚合` |

**实现要点**

- 排序规则由后端定：按 `occurred_at + seq_no` 排序；App 不自己重排。
- 只投 `parent_visible=true` 或被 projection 明确挑中的安全事件。
- 连续噪声事件要在后端折叠，比如连续等待、重复低价值切换，不要把手机时间线刷成日志墙。
- `assistant.reply_prepared`、`asr.transcribed`、`nlu.interpreted` 不直接透给 App；只能变成摘要后再进入时间线。

## 5. `report_detail_view`

**接口**

`GET /app/reports/:id`

**用途**

- 给报告页提供完整报告详情。
- 回答 4 个问题：这轮完成了什么、亮点在哪、卡点在哪、家长接下来怎么跟。

**给哪个 App 页面用**

- 主用：报告页
- 复用：首页“最新报告预览”可只取其中摘要子集

**主要来源表 / 投影**

- `parent_report`：报告主内容
- `task`：任务拆解列表
- `session`：补充结束原因等会话终态字段
- 这些字段背后由报告生成链路吸收 `task.completed`、`task.failed`、`session.ended` 等事件，但 App 不直接读事件

**建议刷新 / 生成触发**

- `parent_report.generated`
- `publish_status` 变化

**关键返回字段**

```json
{
  "report_id": "rpt_xxx",
  "summary": {
    "theme_name_snapshot": "搭桥小工程师",
    "report_date": "2026-03-16",
    "duration_sec": 420,
    "completed_task_count": 3,
    "task_completion_rate": 0.75,
    "help_level_peak": "guided_hint",
    "confidence_overall": "medium",
    "end_reason": "completed"
  },
  "highlights": {
    "achievement_tags": ["能跟着两步指令完成搭建"],
    "notable_moments": ["在关键提示后独立完成桥面拼接"]
  },
  "parent_text": {
    "parent_summary": "这轮能在提示下完成主要搭建步骤。",
    "follow_up_suggestion": "可以让孩子再讲一遍桥为什么需要支撑。"
  },
  "safety": {
    "safety_notice_level": "none"
  },
  "task_breakdown": [
    {
      "parent_label": "搭一座小桥",
      "result_code": "completed_with_hint",
      "parent_note": "在关键线索后完成搭建"
    }
  ]
}
```

**字段说明**

| 字段 | 字段说明 | 主要来源 | 来源归类 |
|---|---|---|---|
| `summary.theme_name_snapshot` | 报告主题名快照 | `parent_report.theme_name_snapshot` | `report` |
| `summary.report_date` | 报告日期 | `parent_report.report_date` | `report` |
| `summary.duration_sec` | 本轮时长 | `parent_report.duration_sec` | `report` |
| `summary.completed_task_count` | 已完成任务数 | `parent_report.completed_task_count` | `report` |
| `summary.task_completion_rate` | 任务完成率 | `parent_report.task_completion_rate` | `report` |
| `summary.help_level_peak` | 本轮最高帮助等级 | `parent_report.help_level_peak` | `report` |
| `summary.confidence_overall` | 本轮整体置信度等级 | `parent_report.confidence_overall` | `report` |
| `summary.end_reason` | 会话结束原因 | `session.end_reason`，必要时同步进 projection | `session` |
| `highlights.achievement_tags` | 本轮亮点标签 | `parent_report.achievement_tags` | `report` |
| `highlights.notable_moments` | 精选亮点时刻 | `parent_report.notable_moments` | `report` |
| `parent_text.parent_summary` | 给家长看的总结 | `parent_report.parent_summary` | `report` |
| `parent_text.follow_up_suggestion` | 家长下一步跟进建议 | `parent_report.follow_up_suggestion` | `report` |
| `safety.safety_notice_level` | 是否需要额外提醒 | `parent_report.safety_notice_level` | `report` |
| `task_breakdown[].parent_label` | 任务可读名称 | `task.parent_label` | `task` |
| `task_breakdown[].result_code` | 任务结果码 | `task.result_code` | `task` |
| `task_breakdown[].parent_note` | 任务级家长摘要 | `task.parent_note` | `task` |

**实现要点**

- 报告生成后，以 `parent_report` 为主，不让 App 反查原始事件。
- `task_breakdown` 是报告页理解“亮点 / 卡点”最关键的结构，必须稳定。
- `end_reason` 保持和状态机 / session 同口径，不单独发明报告版枚举。
- `source_event_from_seq`、`source_event_to_seq` 不给 App。

## 6. 三个接口的字段来源分层总结

| 接口 | 主要来自 event 聚合 | 主要来自 `session / task / report` |
|---|---|---|
| `session_live_view` | `display_status`、`parent_action.*`、必要时 `current_task.parent_note` 的补充摘要 | `header.public_stage`、`progress.*`、`current_task.parent_label`、`help_level_current`、`session_summary.parent_summary_short` |
| `session_timeline_view` | `items[].*` 基本都来自 `event` 二次投影 | 仅在 `display_text` 补全时引用 `task.parent_note` |
| `report_detail_view` | 事件只参与报告生成过程，不直接作为 App 读字段 | `summary.*` 主要来自 `parent_report` + `session.end_reason`，`task_breakdown[]` 来自 `task` |

## 7. 实现顺序建议

建议后端按这个顺序做：

1. 先做 `session_live_view`
   - 这是会话页主骨架。
   - 会逼着后端先把 `session.public_stage`、`session.status`、当前 `task`、介入判断收成一套稳定规则。
2. 再做 `session_timeline_view`
   - 它建立在事件过滤、脱敏、折叠规则已经清楚的前提上。
   - 等 `session_live_view` 口径稳了，再补时间线，前后不会互相打架。
3. 最后做 `report_detail_view`
   - 这一步依赖会话结束、任务汇总、报告生成链路。
   - 放最后最稳，不会在主链还没跑通时先做一堆静态报告壳子。

一句话收口：
- 先把“玩中看懂”做通，再补“过程回放”，最后做“玩后总结”。
