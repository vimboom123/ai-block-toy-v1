# Report Detail View 实现说明 v1

项目：AI积木玩具  
对象：`GET /app/reports/:id`  
日期：2026-03-16  
状态：v1 草案，供后端 / BFF / App 报告页联调使用

## 1. 这份文档解决什么

这份只解决一件事：
- 怎么把 `parent_report + session + task` 收成报告页稳定可读的 `report_detail_view`。

它解决的是：
- 报告页稳定字段
- normal / partial report 的返回口径
- task_breakdown 的字段合同
- 报告页空态 / 降级 / 安全提醒

它不解决：
- 报告生成时的 LLM prompt 细节
- 首页摘要卡片
- 会话中的实时态

## 2. 接口定义

**Route**

`GET /app/reports/:id`

**Auth / 权限**

- 只允许报告归属的家长账号读取。
- 不返回内部 prompt、raw transcript、内部 reason code。

**成功返回**

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
    "end_reason": "completed",
    "publish_status": "published"
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
      "task_id": "task_001",
      "parent_label": "搭一座小桥",
      "status": "completed",
      "result_code": "completed_with_hint",
      "attempt_count": 2,
      "help_level_peak": "guided_hint",
      "parent_note": "在关键线索后完成搭建"
    }
  ],
  "meta": {
    "projection_version": "v1",
    "generated_at": "2026-03-16T22:00:00Z"
  }
}
```

## 3. 顶层字段

### 3.1 `summary`

来源：
- 以 `parent_report` 为主
- `end_reason` 从 `session.end_reason` 同步而来

字段：
- `theme_name_snapshot`
- `report_date`
- `duration_sec`
- `completed_task_count`
- `task_completion_rate`
- `help_level_peak`
- `confidence_overall`
- `end_reason`
- `publish_status`

### 3.2 `highlights`

来源：
- `parent_report.achievement_tags`
- `parent_report.notable_moments`

空值规则：
- 没有则返回空数组，不返回 `null`

### 3.3 `parent_text`

来源：
- `parent_report.parent_summary`
- `parent_report.follow_up_suggestion`

降级规则：
- `parent_summary` 为空 -> `本轮已结束。`
- `follow_up_suggestion` 为空 -> `可以让孩子复述刚才完成的关键步骤。`

### 3.4 `safety`

来源：
- `parent_report.safety_notice_level`

规则：
- 无提醒时返回 `none`
- 不把内部安全原因码直接暴露给家长端

### 3.5 `task_breakdown`

来源：
- 当前 report 覆盖范围内的 task 实例列表

每项字段：
- `task_id`
- `parent_label`
- `status`
- `result_code`
- `attempt_count`
- `help_level_peak`
- `parent_note`

规则：
- `parent_label` 为空时，回退 `当前任务`
- `parent_note` 为空允许返回 `null`
- `failed` 与 `skipped` 必须保持和 report generation spec 一致口径

## 4. partial report 规则

若 `publish_status='partial'`：
- 报告页仍正常可读
- `summary.publish_status='partial'`
- `task_breakdown` 允许不完整
- `parent_text.parent_summary` 必须避免装成完整复盘
- 建议前端显示“本轮提前结束，以下为已生成部分总结”

适用场景：
- `safety_stop`
- `network_error`
- `system_abort`
- 关键数据链不完整但 session 已结束

## 5. 查询建议

最小输入：
- 1 条 `parent_report`
- 1 条关联 `session`
- 0~N 条 report 覆盖范围内 task

## 6. 下一步

这份定住后，下一步继续：
1. `session_public_view` 口径说明
2. 软件全链路 fixture / mock runner 设计
3. 首页 / 会话页 / 报告页三页联动样例
