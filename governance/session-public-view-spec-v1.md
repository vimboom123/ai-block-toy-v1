# Session Public View 口径说明 v1

项目：AI积木玩具  
对象：`session_public_view`  
日期：2026-03-16  
状态：v1 草案，供后端 projection / DTO 设计使用

## 1. 这是什么

`session_public_view` 不是给 App 直接长期依赖的最终页面 DTO，
而是后端内部的一层“公开基线视图”：
- 只保留家长端允许读取的 session 级公开字段
- 给 `session_live_view`、`home_snapshot_view` 这类 projection 复用

## 2. 为什么要有这层

因为：
- `session` 实体里既有公开字段，也有内部字段
- App 不该直接读 `session.current_state`、`risk_flags`、`internal_summary`
- 但多个 projection 又都要复用 `public_stage / status / counts / started_at / ended_at / end_reason`

所以 `session_public_view` 的作用是：
- 先做字段裁剪
- 再给更上层 projection 复用

## 3. 建议最小字段

```json
{
  "session_id": "ses_xxx",
  "parent_user_id": "par_xxx",
  "public_stage": "doing_task",
  "status": "active",
  "current_task_id": "task_001",
  "help_level_peak": "guided_hint",
  "turn_count": 4,
  "completed_task_count": 1,
  "retry_count": 2,
  "started_at": "2026-03-16T09:30:00Z",
  "ended_at": null,
  "end_reason": null,
  "parent_summary_short": "已完成 1 个任务，当前正在完成任务"
}
```

## 4. 明确不暴露

以下字段不进入 `session_public_view`：
- `current_state`
- `risk_flags`
- `internal_summary`
- raw transcript 相关字段
- prompt / rule / guard / debug 字段

## 5. 与上层 projection 的关系

- `session_live_view`：以 `session_public_view` 为基线，再补 current task 和事件聚合
- `home_snapshot_view`：复用其子集作为 `active_session` 的基线
- `report_detail_view`：不直接依赖它，以 `parent_report + session` 为主

## 6. v1 结论

v1 保留这层，但把它视作：
- **后端内部公开基线 view**
- 不是必须单独给 App 暴露的独立 API

也就是说：
- 可以有 `session_public_view` 这层投影
- 但 App v1 不需要单独请求 `GET /app/session-public/...`
