# Session Live View 实现说明 v1

项目：AI积木玩具  
对象：`GET /app/sessions/:id/live`  
日期：2026-03-16  
状态：v1 草案，可直接给后端 / BFF 实现

## 1. 这份文档解决什么

这份只解决一件事：
- 怎么把 `session + task + 安全事件聚合` 收成一个稳定的 `session_live_view`。

它不解决：
- 前端视觉稿
- 全量 timeline
- 报告生成链路

## 2. 接口定义

**Route**

`GET /app/sessions/:id/live`

**Auth / 权限**

- 只允许该 session 归属的家长账号读取。
- 任何 teacher / operator / internal debug 字段都不进响应。

**成功返回**

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
    "parent_note": "系统已给出一个关键线索",
    "awaiting_child_confirmation": false
  },
  "session_summary": {
    "parent_summary_short": "已完成 1 个任务，当前正在完成任务"
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

**`current_task = null` 示例**

```json
{
  "session_id": "ses_xxx",
  "header": {
    "public_stage": "cooling_down",
    "public_stage_text": "正在收尾",
    "display_status": "active",
    "started_at": "2026-03-16T09:30:00Z",
    "ended_at": null
  },
  "progress": {
    "turn_count": 6,
    "completed_task_count": 2,
    "retry_count": 1
  },
  "current_task": null,
  "session_summary": {
    "parent_summary_short": "已完成 2 个任务，当前正在收尾"
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

**响应头建议**

- 浏览器 / CDN 默认：`Cache-Control: no-store`
- 服务端内部读模型缓存：见 §9 的 3~5 秒短缓存
- 也就是说：对外不鼓励浏览器缓存；对内允许 BFF / projection 层做极短 TTL 缓存

**错误返回**

- `404`：session 不存在，或当前家长无权限
- `409`：仅在以下关键字段缺失时返回：
  - `session.status is null`
  - `session.public_stage is null`
  - `session.started_at is null`
  - `session.parent_user_id is null`
- 边界示例：
  - session 不存在 -> `404`
  - session 存在，但归属是别的家长 -> `404`
  - session 存在，但 `parent_user_id` 本身为空 / 损坏 -> `409`
- 除上述情况外，优先降级返回，不要轻易打 `409`

## 3. 字段来源冻结

## 3.0 基础枚举与口径先写死

### 3.0.1 `session.status` 合法值域

v1 只认这几个值：
- `active`
- `paused`
- `ended`
- `aborted`

语义分工：
- `session.status`：表达 session 当前运行态
- `session.public_stage`：表达家长可见的阶段文案语义

不要混用：
- `status` 不是给前端直接展示的文案字段
- `public_stage` 也不代替底层运行态

### 3.0.2 `retry_count` 计数口径

`retry_count` = 当前 session 内，某个 task 在 `task.failed` 之后又被重新激活一次的累计次数。

与 `task.attempt_count` 的关系写死：
- `task.attempt_count`：只统计单个 task 实例内部已经尝试了几次
- `session.retry_count`：只统计“失败后再次激活同一 task 或重开该任务”的 session 级累计次数
- 普通 hint 升级只增加 `task.attempt_count`，不增加 `session.retry_count`
- 只有出现 `task.failed`，且后续该任务再次被激活时，才增加 `session.retry_count`
- `session.retry_count` 不是所有 task 的 `attempt_count` 之和

明确排除：
- 切到下一个新 task，不算 retry
- 普通 hint 升级，不算 retry
- session pause / resume，不算 retry

空值处理：
- DB 为 `null` 时，响应统一返回 `0`

### 3.1 header

| 字段 | 规则 | 来源 |
|---|---|---|
| `public_stage` | 直接取 `session.public_stage` | `session` |
| `public_stage_text` | 固定映射，不让前端自己翻译 | projection 常量表 |
| `display_status` | 根据 `session.status + end_reason + 最近打断/恢复事件` 聚合 | `session + event` |
| `started_at` | 直接取 | `session.started_at` |
| `ended_at` | 直接取，未结束为 `null` | `session.ended_at` |

### 3.2 progress

| 字段 | 规则 | 来源 |
|---|---|---|
| `turn_count` | 直接取聚合字段；空值转 `0` | `session.turn_count` |
| `completed_task_count` | 直接取聚合字段；空值转 `0` | `session.completed_task_count` |
| `retry_count` | 直接取聚合字段；口径见 §3.0.2；空值转 `0` | `session.retry_count` |

### 3.3 current_task

| 字段 | 规则 | 来源 |
|---|---|---|
| `parent_label` | 当前激活 task 的家长可读标题 | `task.parent_label` |
| `help_level_current` | 当前 task 当前帮助等级 | `task.help_level_current` |
| `parent_note` | 严格按 §7 的回退规则生成；不是拼接补充 | `task.parent_note`（见 §7 回退规则） |
| `awaiting_child_confirmation` | 是否正在等待孩子确认自己已完成；没有该场景时返回 `false` | `session.current_state=self_report_confirm` 经安全投影后得到 |

### 3.4 session_summary

| 字段 | 规则 | 来源 |
|---|---|---|
| `parent_summary_short` | 优先取 `session.parent_summary_short`；没有就按 §3.4.1 的静态字符串模板生成一句；禁止调用 LLM | `session` |

### 3.4.1 `parent_summary_short` 静态模板写死

只能用静态模板，不允许调用 LLM。

模板规则：

1. 若 `current_task != null`：
   - `已完成 {completed_task_count} 个任务，当前任务：{current_task.parent_label}`
2. 若 `current_task = null`：
   - `已完成 {completed_task_count} 个任务，当前{public_stage_text}`
3. 若 `public_stage_text = null`：
   - `已完成 {completed_task_count} 个任务`

字段口径：
- `{completed_task_count}` 取响应里的 `progress.completed_task_count`
- `{current_task.parent_label}` 必须是家长可读文案
- 不拼接内部状态码
- 不调用模型润色

### 3.5 parent_action

| 字段 | 规则 | 来源 |
|---|---|---|
| `need_parent_intervention` | 后端判定，不让前端自己拼 | `session + task + event` |
| `intervention_reason_text` | 只有需要介入时才返回文案 | projection 规则 |
| `suggested_action_text` | 只有需要介入时才返回动作建议 | projection 规则 |

## 4. 固定映射表

### 4.1 `public_stage -> public_stage_text`

| `public_stage` | `public_stage_text` |
|---|---|
| `warming_up` | `正在热身进入状态` |
| `doing_task` | `正在完成任务` |
| `receiving_hint` | `正在接收提示` |
| `celebrating` | `刚完成一个小目标` |
| `cooling_down` | `正在收尾` |
| `ended` | `本轮已结束` |

### 4.2 `display_status` 冻结值域

v1 只允许：
- `active`
- `paused`
- `ended`
- `aborted`

判断规则：

1. `session.status = 'aborted'` -> `aborted`
2. else if `session.public_stage = 'ended' or session.status = 'ended'` -> `ended`
3. else if `session.status = 'paused'` -> `paused`
4. else -> `active`

补充：
- 终止类状态优先级高于 `paused`
- 家长暂停后若直接结束，会最终显示为 `aborted`，不会长期停留在 `paused`

不要新增 `interrupted`、`waiting`、`thinking` 这种展示态。

补充：
- 若 `public_stage` 不在 §4.1 映射表中，`public_stage_text = null`
- 同时记 warning 日志，不在 live view 里抛内部错误

## 5. 当前任务选择规则

先看强制规则，再看 fallback。

`current_task` 的挑选顺序：

1. 若 `session.public_stage in ('cooling_down', 'ended')`：
   - 直接强制 `current_task = null`
   - 不再执行后续 task 查找
2. 否则，优先取 `session.current_task_id` 对应 task
3. 若 `current_task_id` 非空，但 task record 查不到：
   - 按步骤 4 fallback
4. 若 `current_task_id` 为空，或步骤 2 查不到，且 `session.status != 'ended'`：
   - 取最近一个 `status='active'` 的 task
   - “最近”固定定义为：`order by activated_at desc, id desc limit 1`
5. 步骤 1~4 都找不到时：
   - `current_task = null`

不要做的事：
- 不要前端自己扫 task 列表推当前任务
- 不要把已经 completed 的 task 强行继续挂在当前卡片上

## 6. 家长介入判断规则 v1

### 6.1 触发 `need_parent_intervention=true` 的条件

满足任一条即可：

1. `task.help_level_current = 'parent_takeover'`
2. `session.status = 'paused'`
3. `session.status = 'aborted'`
4. 最近 20 条家长可见事件内存在：
   - `parent.interrupt_requested`
   - `session.ended`（仅用于 `session.status` 尚未完成最终聚合写入的短暂兜底窗口；不用于 `end_reason=completed` 的正常完成场景）

补充：
- 条件 4 只负责兜底，防止事件先到、session 聚合字段稍后到时出现短暂漏判
- 若 `session.status='ended'` 且属于正常完成路径，`session.ended` 不单独触发介入卡
- 若命中 `session.ended` 事件，其原因判断仍以 `session.end_reason` 为准，不从 event payload 单独取值

### 6.2 文案模板

多条件同时成立时，按这个优先级选文案：
- 场景 C（安全中止）
- 场景 A（家长接管）
- 场景 B（主动暂停）
- 场景 D（系统中止 / 其他需家长关注的结束）

#### 场景 A：家长接管
- 命中条件：`task.help_level_current = 'parent_takeover'`
- `intervention_reason_text`: `这一环节需要家长接手一下。`
- `suggested_action_text`: `先到孩子身边看一眼，再决定继续还是结束。`

#### 场景 B：主动暂停
- 命中条件：`session.status = 'paused'` 或最近 20 条事件内存在 `parent.interrupt_requested`
- `intervention_reason_text`: `当前流程已暂停，等待家长处理。`
- `suggested_action_text`: `确认孩子状态后，再选择继续。`

#### 场景 C：安全中止
- 命中条件：`session.status = 'aborted'` 且 `session.end_reason = 'safety_stop'`
- `intervention_reason_text`: `本轮已因安全原因提前结束。`
- `suggested_action_text`: `先安抚孩子，再查看报告里的提醒。`

#### 场景 D：系统中止 / 家长结束
- 命中条件：`session.status = 'aborted'` 且 `session.end_reason in ('parent_interrupted', 'network_error', 'asr_fail_exhausted', 'device_shutdown', 'theme_switched', 'system_abort')`
- `intervention_reason_text`: `本轮已提前结束，建议家长看一下当前情况。`
- `suggested_action_text`: `先确认孩子状态，再决定要不要重新开始。`

#### 其他 `end_reason`
- 不在 `('safety_stop', 'parent_interrupted', 'network_error', 'asr_fail_exhausted', 'device_shutdown', 'theme_switched', 'system_abort')` 内的值，v1 不触发额外 intervention 文案
- 仍按 `display_status` 常规规则处理

#### 默认
- 三个字段走空，不给伪提醒。

## 7. `parent_note` 补写规则

优先级：

1. `task.parent_note` 有值就直接用
2. 若 `task.help_level_current = 'parent_takeover'`：
   - `parent_note = null`
   - 该场景的提示只由 `parent_action` 承担
3. 否则尝试用最近一条家长可见事件改写：
   - `help.level_changed` -> `系统已给出一个关键线索`
   - `task.failed` -> `这个任务暂时没完成，系统准备换个方式继续`
   - `task.completed` -> `这个任务已经完成，准备进入下一步`
4. 其他事件类型默认不补文案，直接返回 `null`
5. 还没有就返回 `null`

这里是严格回退，不做字符串拼接。

禁止：
- 直接把 raw transcript 塞进 `parent_note`
- 直接把内部 reason code 暴露给家长

## 8. 查询 / 聚合建议

### 8.1 最小查询输入

后端至少需要拿到：

- 1 条 `session`
- 0/1 条当前 `task`
- 最近固定 20 条 `parent_visible=true` 的家长可见事件

### 8.2 伪 SQL

```sql
-- session
select *
from session
where id = :session_id
  and parent_user_id = :viewer_parent_id
limit 1;

-- current task by current_task_id
select *
from task
where session_id = :session_id
  and id = :current_task_id
limit 1;

-- fallback current task
select *
from task
where session_id = :session_id
  and status = 'active'
order by activated_at desc, id desc
limit 1;

-- recent visible events
select *
from event
where session_id = :session_id
  and parent_visible = true
  and event_type in (
    'help.level_changed',
    'task.failed',
    'task.completed',
    'parent.interrupt_requested',
    'parent.resume_requested',
    'session.ended',
    'task.activated',
    'state.transition_applied'
  )
order by occurred_at desc, seq_no desc
limit 20;
```

补充：
- `seq_no` 指 session 内单调递增序号，不是全局序号
- v1 默认前提：一个 session 只对应一个家长账号

### 8.3 BFF 组装伪代码

```ts
function buildSessionLiveView(session, currentTask, recentEvents) {
  const displayStatus = deriveDisplayStatus(session, recentEvents)
  const parentAction = deriveParentAction(session, currentTask, recentEvents)

  return {
    session_id: session.id,
    header: {
      public_stage: session.public_stage,
      public_stage_text: mapPublicStageText(session.public_stage),
      display_status: displayStatus,
      started_at: session.started_at,
      ended_at: session.ended_at,
    },
    progress: {
      turn_count: session.turn_count ?? 0,
      completed_task_count: session.completed_task_count ?? 0,
      retry_count: session.retry_count ?? 0,
    },
    current_task: buildCurrentTask(session, currentTask, recentEvents),
    session_summary: {
      parent_summary_short: session.parent_summary_short ?? fallbackSummaryByStaticTemplate(session, currentTask),
    },
    parent_action: parentAction,
    meta: {
      projection_version: 'v1',
      generated_at: nowIso(),
    },
  }
}
```

## 9. 缓存 / 刷新建议

v1 可以先走短缓存：
- key: `session_live_view:v1:{session_id}`
- TTL: 3~5 秒

失效触发：
- `task.activated`
- `state.transition_applied`
- `help.level_changed`
- `task.completed`
- `task.failed`
- `parent.interrupt_requested`
- `parent.resume_requested`
- `session.ended`

如果后端已经有 event-driven projection worker，优先走事件增量刷新；没有的话先做 read-time assemble 也行。

## 10. 验收样例

### 样例 1：正常进行中

- `public_stage=doing_task`
- `status=active`
- `help_level_current=guided_hint`

预期：
- `display_status=active`
- `need_parent_intervention=false`

### 样例 2：家长主动暂停

- `public_stage=doing_task`
- `status=paused`
- `end_reason=null`
- 最近事件有 `parent.interrupt_requested`

预期：
- `display_status=paused`
- `need_parent_intervention=true`
- 返回暂停类文案

### 样例 3：安全结束

- `public_stage=ended`
- `status=aborted`
- `end_reason=safety_stop`

预期：
- `display_status=aborted`
- `need_parent_intervention=true`
- 返回安全结束类文案
- `current_task` 允许为 `null`

### 样例 4：收尾阶段无当前任务

- `public_stage=cooling_down`
- `status=active`
- `current_task_id=null`

预期：
- `current_task = null`
- `display_status=active`
- `need_parent_intervention=false`

### 样例 5：`current_task_id` 丢失，走 fallback

- `public_stage=doing_task`
- `status=active`
- `current_task_id` 非空，但查不到对应 task
- 存在另一条 `status='active'` task，且 `activated_at` 最新

预期：
- 按 fallback task 返回 `current_task`
- 不直接报错

### 样例 6：普通中止

- `public_stage=ended`
- `status=aborted`
- `end_reason=system_abort`

预期：
- `display_status=aborted`
- `need_parent_intervention=true`
- 返回场景 D 文案

### 样例 7：多条件同时成立

- `status=aborted`
- `public_stage=ended`
- `task.help_level_current=parent_takeover`
- `end_reason=safety_stop`

预期：
- `display_status=aborted`
- `need_parent_intervention=true`
- 按优先级命中场景 C，而不是 A/B

### 样例 8：正常结束

- `status=ended`
- `public_stage=ended`
- `end_reason=null`

预期：
- `display_status=ended`
- `need_parent_intervention=false`
- `current_task = null`

## 11. 明确不做

v1 先不做这些：

- 不在 live view 里塞 timeline items
- 不返回 raw event_type 列表
- 不返回孩子原话、录音转写、内部提示词
- 不做多语言文案系统，先固定中文
- 不在这个接口里夹带报告摘要

## 12. 下一步

`session_live_view` 定住后，下一步就能继续拆：
1. `session_timeline_view` 的 `display_type / display_text / severity` 折叠规则
2. 再做 `report_detail_view` 的报告稳定字段

一句话：
- 先把家长“现在看到什么”定死，再谈后面的过程回放和玩后总结。
