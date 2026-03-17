# Home Snapshot View 实现说明 v1

项目：AI积木玩具  
对象：`GET /app/home`  
日期：2026-03-16  
状态：v1 草案，供后端 / BFF / App 首页联调使用

## 1. 这份文档解决什么

这份只解决一件事：
- 怎么把首页需要的“当前会话 + 最新报告 + 继续入口 + 异常提醒”收成一个稳定的 `home_snapshot_view`。

它解决的是：
- 首页首屏信息来源
- 首页卡片优先级
- `active_session / latest_report / continue_entry / alerts` 的字段合同
- 空态 / 冲突态 / 降级规则

它不解决：
- 内容页完整列表
- 设置页完整信息
- 原始事件时间线

## 2. 接口定义

**Route**

`GET /app/home`

**Auth / 权限**

- 只允许当前家长账号读取其归属儿童 / 设备 / 报告的首页快照。
- 不返回其他家长、teacher、operator 的数据。

**成功返回**

```json
{
  "active_session": {
    "session_id": "ses_xxx",
    "public_stage": "doing_task",
    "public_stage_text": "正在完成任务",
    "display_status": "active",
    "started_at": "2026-03-16T09:30:00Z",
    "completed_task_count": 1,
    "retry_count": 2,
    "parent_summary_short": "已完成 1 个任务，当前正在完成任务",
    "entry_cta_text": "进入会话"
  },
  "latest_report": {
    "report_id": "rpt_xxx",
    "theme_name_snapshot": "搭桥小工程师",
    "report_date": "2026-03-16",
    "achievement_tags": ["能跟着两步指令完成搭建"],
    "parent_summary": "这轮能在提示下完成主要搭建步骤。",
    "entry_cta_text": "查看报告"
  },
  "continue_entry": {
    "theme_id": "thm_xxx",
    "theme_name": "搭桥小工程师",
    "entry_reason_text": "上次进行到一半，可以继续。",
    "entry_cta_text": "继续这个主题"
  },
  "alerts": [],
  "meta": {
    "projection_version": "v1",
    "generated_at": "2026-03-16T21:40:00Z"
  }
}
```

## 3. 顶层结构

### 3.1 `active_session`

- 有进行中 session 才返回对象
- 没有则返回 `null`
- 首页最多只显示 1 个 active session

字段：
- `session_id`
- `public_stage`
- `public_stage_text`
- `display_status`
- `started_at`
- `completed_task_count`
- `retry_count`
- `parent_summary_short`
- `entry_cta_text`

来源：
- 直接复用 `session_live_view` 的 header/progress 子集

### 3.2 `latest_report`

- 优先取最近一条 `publish_status='published'` 的 `parent_report`
- 没有就返回 `null`
- 首页只显示 1 条最新报告摘要

字段：
- `report_id`
- `theme_name_snapshot`
- `report_date`
- `achievement_tags`
- `parent_summary`
- `entry_cta_text`

来源：
- `report_detail_view` 的摘要子集
- 不让首页自行反拼 task/event

### 3.3 `continue_entry`

- 用于“继续上次主题”的轻入口
- 没有合适候选就返回 `null`

v1 候选规则：
1. 最近存在 `status in ('ended', 'aborted')` 的 session
2. 对应 theme 不是 archived
3. 最近一次 report 或 session 不是安全中止强提醒场景
4. 最近一次完成率 < 1.0，或明确存在未完成任务

字段：
- `theme_id`
- `theme_name`
- `entry_reason_text`
- `entry_cta_text`

### 3.4 `alerts`

- 首页全局提醒数组
- 没有提醒时返回空数组，不返回 `null`

v1 只允许两类：
- `device_offline`
- `safety_stop`

字段：
- `alert_type`
- `severity`
- `title`
- `body`
- `entry_cta_text`

## 4. 组装优先级

首页渲染优先级写死：
1. `alerts`（若有高优先提醒）
2. `active_session`
3. `latest_report`
4. `continue_entry`

说明：
- 有 `active_session` 时，首页首屏主卡优先显示进行中会话
- `latest_report` 不因为存在 `active_session` 而消失，只是下移
- `continue_entry` 是补充入口，不抢主卡位

## 5. 空态与降级

### 5.1 完全空首页

若以下都不存在：
- `active_session`
- `latest_report`
- `continue_entry`
- `alerts`

则返回：
- `active_session = null`
- `latest_report = null`
- `continue_entry = null`
- `alerts = []`

由前端展示 onboarding / 去内容页入口。

### 5.2 报告缺字段

若 `parent_report` 存在，但：
- `achievement_tags` 为空 -> 返回空数组
- `parent_summary` 为空 -> 回退到 `已完成 {completed_task_count} 个任务。`

### 5.3 安全中止优先级

若最近一轮 `end_reason='safety_stop'`：
- `alerts` 必须出现一条 `safety_stop`
- `continue_entry` 默认不展示
- `latest_report` 仍可展示，但不抢过安全提醒

## 6. 查询建议

最小输入：
- 当前家长最近 1 条进行中 session
- 最近 1 条 published report
- 最近 1 条可继续主题候选
- 当前设备状态或最近安全中止信号

## 7. 下一步

`home_snapshot_view` 定住后，下一步继续补：
1. `parent_report` 生成机制
2. `report_detail_view` 实现说明
3. 首页 / 会话页 / 报告页三页联动样例
