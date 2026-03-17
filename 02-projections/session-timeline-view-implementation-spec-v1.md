# Session Timeline View 实现说明 v1

项目：AI积木玩具  
对象：`GET /app/sessions/:id/timeline`  
日期：2026-03-16  
状态：v1 草案，供后端 / BFF / 前端联调使用

## 1. 这份文档解决什么

这份只解决一件事：
- 怎么把 session 里的离散 event 折叠成家长可看的 `session_timeline_view`。

它解决的是：
- timeline item 的稳定 schema
- `display_type / display_text / severity` 的折叠规则
- 哪些 event 能进家长时间线，哪些不能进
- 同类事件怎么去噪、合并、降级

它不解决：
- live card 顶部状态
- 最终报告总结
- 前端视觉样式
- 录音/逐字稿展示

## 2. 接口定义

**Route**

`GET /app/sessions/:id/timeline`

**Auth / 权限**

- 只允许该 session 归属的家长账号读取。
- 只返回家长可见 timeline，不暴露 teacher / operator / debug / raw transcript。

**成功返回**

```json
{
  "session_id": "ses_xxx",
  "items": [
    {
      "timeline_item_id": "tl_ses_xxx_a1b2c3d4e5f6",
      "occurred_at": "2026-03-16T09:30:15Z",
      "display_type": "session_started",
      "display_text": "这一轮开始了。",
      "severity": "info",
      "related_task": null,
      "meta": {
        "source_event_count": 1,
        "source_event_types": ["session.started"]
      }
    },
    {
      "timeline_item_id": "tl_ses_xxx_b2c3d4e5f6a7",
      "occurred_at": "2026-03-16T09:31:10Z",
      "display_type": "task_progress",
      "display_text": "孩子开始尝试“搭一座小桥”。",
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
      "timeline_item_id": "tl_ses_xxx_c3d4e5f6a7b8",
      "occurred_at": "2026-03-16T09:33:40Z",
      "display_type": "hint_given",
      "display_text": "系统给了一个提示，孩子继续尝试中。",
      "severity": "info",
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
    "generated_at": "2026-03-16T09:34:00Z",
    "events_until": "2026-03-16T09:33:40Z",
    "has_earlier_items": false
  }
}
```

`has_earlier_items` 口径补充：
- v1 默认基于当前已读取并折叠的候选窗口来判断
- 它不额外承诺数据库里不存在窗口外的更早候选 event

`meta` 口径：
- `generated_at`：server 生成这份 projection 的 wall clock 时间
- `events_until`：本次纳入 timeline 的最后一个 event 的 `occurred_at`
- `has_earlier_items`：是否还有更早的折叠 item 没有放进当前响应

**错误返回**

- `404`：session 不存在，或当前家长无权限
- `500`：仅在 session 主记录损坏，但底层仍存在该 session 的孤儿 event 时返回
- 单个坏 event（如缺 `occurred_at` / 缺 `seq_no`）默认丢弃或降级排序并记 warning，不整页报错

## 3. 时间线目标

家长时间线不是 raw event dump，而是“发生了什么”的可读折叠流。

v1 目标：
- 一眼能看懂这一轮发生过什么
- 不把细碎状态抖动全塞给家长
- 关键风险/中止/家长接管必须看得见
- 同类事件在短时间内要合并，不要刷屏

## 4. timeline item schema 冻结

## 4.0 `session.end_reason` 正式枚举与 timeline 映射

这份 timeline spec 不自己发明新的 `end_reason`，统一沿用 session / backend schema：

- `completed`
- `child_quit`
- `timeout_no_input`
- `network_error`
- `asr_fail_exhausted`
- `safety_stop`
- `parent_interrupted`
- `device_shutdown`
- `theme_switched`
- `system_abort`

映射规则写死：

| `session.end_reason` | `display_type` | `severity` |
|---|---|---|
| `completed` | `session_ended` | `info` |
| `child_quit` | `session_ended` | `warning` |
| `timeout_no_input` | `session_ended` | `warning` |
| `network_error` | `session_ended` | `warning` |
| `asr_fail_exhausted` | `session_ended` | `warning` |
| `safety_stop` | `safety_alert` | `critical` |
| `parent_interrupted` | `session_ended` | `warning` |
| `device_shutdown` | `session_ended` | `warning` |
| `theme_switched` | `session_ended` | `warning` |
| `system_abort` | `session_ended` | `warning` |

### 4.1 顶层字段

| 字段 | 规则 |
|---|---|
| `timeline_item_id` | projection 生成的稳定 id，强制格式：`tl_{session_id}_{bucket_token}`；同一批 source event 重跑必须生成相同 id；`bucket_token` 不是简单行号，算法见 §4.3 |
| `occurred_at` | 该 item 对外展示时间；默认取该 bucket 最早事件时间；Rule B 例外见 §8.4 |
| `display_type` | 固定枚举，见 §5 |
| `display_text` | 固定模板文案，见 §6 |
| `severity` | 固定枚举：`info / warning / critical` |
| `related_task` | 没有关联 task 就返回 `null` |
| `meta.source_event_count` | 最终实际折叠进该 item 的 source event 数量；只统计最终归属到该 item 的 event，不把被其他高优先级规则截走或改归别的 item 的 event 算进来 |
| `meta.source_event_types` | 去重后的 source event type 列表；仅供前端调试，不应作为业务逻辑依据，v2 可能移除 |

### 4.2 `related_task`

```json
{
  "task_id": "task_001",
  "parent_label": "搭一座小桥"
}
```

规则：
- 能关联 task 就带 `task_id + parent_label`
- `related_task.task_id` 默认来自触发该 timeline item 的 source event payload.task_id
- 若一个 item 由多条 event 合并而成，`task_id` 取该合并组里一致的那个 task_id；若无法稳定得出唯一 task_id，则返回 `null`
- `paused_for_parent` 若来自 `help.level_changed(to_level='parent_takeover')` 路径，则仍沿用该组 hint 的 `task_id`
- 不能稳定关联时返回 `null`
- 不返回内部 task prompt / internal note

### 4.3 `bucket_seq` 稳定算法

- `bucket_seq` 必须由该 item 最终归属的 source event 集合稳定导出，不能直接使用“当前响应里的第几个 item”
- v1 写死算法：
  1. 先取该 item 的全部 source event 主键 `event_id`
  2. 按稳定顺序排序：`occurred_at asc nulls last` → `seq_no asc nulls last` → `event_id asc`
  3. 拼成 canonical key：`event_id_1,event_id_2,...`
  4. 对 canonical key 计算 `SHA-256`
  5. 取 hex digest 的前 12 位，作为 `bucket_token`
  6. 最终 `timeline_item_id = tl_{session_id}_{bucket_token}`
- 只要 item 归属的 source event 集合不变，重跑 projection 时 `timeline_item_id` 就不得变化
- 后续新 event 到来时，旧 item 若其 source event 集合未变，既有 id 也不得漂移

测试向量示例：
- 若 source event ids 排序后为：`evt_a,evt_b,evt_c`
- canonical key 固定为：`evt_a,evt_b,evt_c`
- 任一实现都必须先得到同一 canonical key，再做 hash；不得跳过排序直接按输入顺序 hash

## 5. `display_type` 冻结值域

v1 只允许这些：

- `session_started`
- `task_progress`
- `hint_given`
- `task_completed`
- `task_failed`
- `paused_for_parent`
- `session_resumed`
- `session_ended`
- `safety_alert`

不要新增：
- `thinking`
- `state_changed`
- `debug_transition`
- `internal_guardrail`

## 6. `display_text` 固定模板

### 6.1 session_started
- `这一轮开始了。`

### 6.2 task_progress
- 有 task：`孩子开始尝试“{parent_label}”。`
- 无 task：`孩子开始进行新的一个步骤。`

### 6.3 hint_given
- 默认：`系统给了一个提示，孩子继续尝试中。`
- 若 `help.level_changed.to_level = 'parent_takeover'`：不生成 `hint_given`，固定改走 `paused_for_parent`
- 若 `to_level` 缺失、为空、或是其他未知值：默认仍走 `hint_given`

### 6.4 task_completed
- 有 task：`孩子完成了“{parent_label}”。`
- 无 task：`孩子完成了当前任务。`

### 6.5 task_failed
- 有 task：`“{parent_label}”暂时没完成，系统准备换个方式继续。`
- 无 task：`这个步骤暂时没完成，系统准备换个方式继续。`

### 6.6 paused_for_parent
- `这一轮已暂停，等家长处理。`

### 6.7 session_resumed
- `这一轮继续了。`

### 6.8 session_ended
- `end_reason='completed'`：`这一轮结束了。`
- `end_reason='parent_interrupted'`：`这一轮已由家长结束。`
- `end_reason='child_quit'`：`孩子这轮不想继续了，这一轮先结束。`
- `end_reason='timeout_no_input'`：`这轮因为长时间没有继续互动，先结束了。`
- `end_reason in ('network_error', 'asr_fail_exhausted', 'device_shutdown', 'theme_switched', 'system_abort')`：`这一轮提前结束了。`
- `end_reason is null` 或未知值：`这一轮结束了。`

### 6.9 safety_alert
- `本轮因安全原因提前结束，建议家长看一下当前情况。`

## 7. `severity` 规则

- `info`：
  - `session_started`
  - `task_progress`
  - `hint_given`
  - `task_completed`
  - `session_resumed`
  - 正常 `session_ended`
- `warning`：
  - `task_failed`
  - `paused_for_parent`
  - `session_ended` with `end_reason in ('child_quit', 'timeout_no_input', 'network_error', 'asr_fail_exhausted', 'parent_interrupted', 'device_shutdown', 'theme_switched', 'system_abort')`
  - `session_ended` with unknown `end_reason`
- `critical`：
  - `safety_alert`
- `info` fallback：
  - `session_ended` with `end_reason is null`

## 8. event -> timeline 折叠规则

### 8.1 允许进入时间线的 source event

v1 只消费这些：
- `session.started`
- `task.activated`
- `help.level_changed`
- `task.completed`
- `task.failed`
- `parent.interrupt_requested`
- `parent.resume_requested`
- `session.ended`
- `state.transition_applied`

补充字段前提：
- `event.parent_visible` 是 event 表布尔列，由事件生产方写入
- v1 进入 timeline 的白名单事件默认都应满足 `parent_visible = true`
- `state.transition_applied` 若要参与 Rule A 合并，payload 里必须带 `task_id`

### 8.2 直接过滤掉的 event

这些不进家长 timeline：
- 纯 internal debug event
- transcript chunk
- token / latency / model event
- guardrail 内部细节码
- 高频、无家长价值的状态抖动 event

### 8.3 基础映射

| source event | display_type | 备注 |
|---|---|---|
| `session.started` | `session_started` | 直接生成 |
| `task.activated` | `task_progress` | 可与紧随其后的 `state.transition_applied` 合并 |
| `help.level_changed` | `hint_given` | 默认生成；若 `to_level='parent_takeover'`，固定改生成 `paused_for_parent` |
| `task.completed` | `task_completed` | 直接生成 |
| `task.failed` | `task_failed` | 直接生成 |
| `parent.interrupt_requested` | `paused_for_parent` | 直接生成 |
| `parent.resume_requested` | `session_resumed` | 直接生成 |
| `session.ended` + `end_reason='safety_stop'` | `safety_alert` | 优先级最高 |
| `session.ended` + `end_reason='completed'` | `session_ended` | 正常结束 |
| `session.ended` + `end_reason in ('child_quit', 'timeout_no_input', 'network_error', 'asr_fail_exhausted', 'parent_interrupted', 'device_shutdown', 'theme_switched', 'system_abort')` | `session_ended` | 提前结束 / 中断 |
| `session.ended` + `end_reason is null` | `session_ended` | 结束原因暂未落库时的保守回退 |
| `session.ended` + unknown `end_reason` | `session_ended` | 兼容未来新枚举，按 warning 降级 |
| `state.transition_applied` | 默认不单独生成 | 仅作为邻近事件的补充上下文；没有 `task_id` 就不参与 Rule A |

补充：
- 这里的 `end_reason` 统一以 `session` 主记录上的 `end_reason` 为准
- `session.ended` event 只负责表明“结束事件发生了”，不作为 v1 的最终 `end_reason` 事实源
- 若 event payload 里也带了 `end_reason`，v1 不以它为准；发现与 `session.end_reason` 不一致时记 warning
- 若 `session.end_reason` 暂时仍为 `null`，而 `session.ended` event 已经出现，则先按上表的 `null` fallback 生成 `session_ended`；v1 不回退读取 event payload.end_reason 作为最终事实源

### 8.4 合并规则

#### Rule A：task start 合并
若满足：
- 同一 `task_id`
- `task.activated` 后 5 秒内出现 `state.transition_applied`
- 且该 `state.transition_applied.payload.task_id` 存在

则合并成 1 条 `task_progress` item。

补充：
- Rule A 的 `occurred_at` 固定取 `task.activated.occurred_at`
- 若 5 秒窗口内出现多条同 `task_id` 的 `state.transition_applied`，只吸收最早那一条进入该 `task_progress`
- 同窗口内其余 `state.transition_applied` 默认静默丢弃并记 warning，不再额外生成 item

若 `state.transition_applied` 没有 `task_id`：
- 不参与合并
- 继续只保留 `task.activated` 生成的 `task_progress`

补充：
- 当前 §10 查询前提已在候选 event 层过滤 `parent_visible = true`
- 因此 `parent_visible = false` 的 `state.transition_applied` 默认不会进入 projection 候选集
- 这类事件不会触发 Rule A，属于查询前提决定的预期静默行为，不视为漏数或投影错误

#### Rule B：连续 hint 去噪
若满足：
- 连续多个 `help.level_changed`
- 采用链式分组：只要相邻两条 `help.level_changed` 的时间间隔 <= 30 秒，就继续归入同一组；不是固定 30 秒总窗口
- 同一 `task_id`
- 且相邻两条 `help.level_changed` 之间没有插入其他会改变任务进度语义的白名单 event（如 `task.completed` / `task.failed` / `parent.interrupt_requested` / `parent.resume_requested` / `session.ended`）
- 且这组 event 内不存在 `to_level='parent_takeover'`

则只保留最后 1 条 `hint_given`。

输出类型补充：
- Rule B 的默认输出类型仍是 `hint_given`
- 若这组连续 hint 里任一条已命中 `to_level='parent_takeover'`，则这组不再按普通 hint 去噪
- 此时优先按更高优先级状态生成 `paused_for_parent`，不输出 `hint_given`
- 在出现 `parent_takeover` 后、直到明确 `session_resumed` 前再次出现的普通 hint，默认静默丢弃，不再另起新组；必要时可记 warning
- 后续新的普通 hint 只有在明确 `session_resumed` 之后，才作为新一组重新计算

计数口径补充：
- `meta.source_event_count` 必须统计整个被合并进去的 hint 总数
- `meta.source_event_types` 仍只需返回去重后的 event type 列表

这里是 `occurred_at` 的特例：
- `hint_given.occurred_at` 取最后一个被保留的 `help.level_changed.occurred_at`
- 若该组因 `parent_takeover` 改落 `paused_for_parent`，则 `occurred_at` 取第一条命中 `parent_takeover` 的 `help.level_changed.occurred_at`
- 这条规则覆盖 §4.1 默认“取 bucket 最早事件时间”的通用口径

#### Rule C：pause / resume 去抖
若满足：
- `parent.interrupt_requested` 后 15 秒内又 `parent.resume_requested`
- 且中间无 `task.failed / task.completed / session.ended`

则这对 pause/resume 两条都不进 timeline。

补充：
- Rule C 只处理来自 `parent.interrupt_requested` / `parent.resume_requested` 这一对 source event 的去抖
- 不作用于 Rule B 中 `help.level_changed(to_level='parent_takeover')` 路径产出的 `paused_for_parent`

补充：
- 每一对 pause/resume 独立计算 15 秒窗口
- 第一对去抖完成后，后续新的 pause/resume 重新单独计算，不把多轮连串事件合成一个总窗口

#### Rule D：结束态覆盖
若出现 `session.ended`：
- 若 `end_reason='safety_stop'`，生成 `safety_alert`
- 否则按 §4.0 的映射生成 `session_ended`
- 不再额外生成同时间点的第二条结束类 item

## 9. 排序与分页

### 9.1 排序

- 按 `occurred_at asc`
- 若同秒冲突，按 session 内 `seq_no asc`
- 若 `seq_no` 缺失：
  - 同秒内固定排最后
  - 同时记 warning

### 9.2 分页

v1 先不做 cursor 分页。
默认整页返回最近 100 条折叠后 item。

若折叠后仍超过 100 条：
- 若折叠结果里存在 `session_started`，强制保留第一条 `session_started` 作为起点
- 其余位置取最后 99 条 item
- 若第一条 `session_started` 本身已经落在最后 99 条里，不重复插入，仍只返回 100 条内去重后的 item
- 若折叠结果里根本不存在 `session_started`，不额外伪造，直接返回最后 100 条 item
- `meta.has_earlier_items = true`

若折叠后不超过 100 条：
- 默认 `meta.has_earlier_items = false`

查询窗口口径补充：
- §10 当前只拉最近 300 条候选 event，这只是 v1 的读取窗口，不代表 session 历史天然只到这里
- 因此 `meta.has_earlier_items=false` 只表示“在当前已读取并完成折叠的候选窗口内，没有更早 item 还未放进响应”
- 它不额外承诺数据库里一定不存在更早、但本次查询窗口未读入的候选 event
- 若后端未来改成可精确感知窗口外是否还有更早候选 event，可再升级这个字段语义

## 10. 查询输入建议

后端至少需要拿到：
- 1 条 `session`
- 当前 session 下最近 300 条候选 event
- 必要时补 0~N 条 task 基础信息（`task_id -> parent_label`）

任务基础信息补查口径：
- 只补查当前折叠结果里实际引用到的 `task_id`
- 去重后批量查询，不要按 item 一条条查
- 查不到 task 时，`related_task=null`，同时 `display_text` 回退到无 task 模板
- task 已删除 / 已归档 / label 为空，都不应让整个 projection 失败
- `parent_label` 取家长侧稳定展示名；不要回填内部 prompt、teacher note、system label

伪 SQL：

```sql
select id, parent_user_id, end_reason
from session
where id = :session_id
  and parent_user_id = :viewer_parent_id
limit 1;

select *
from event
where session_id = :session_id
  and parent_visible = true
  and event_type in (
    'session.started',
    'task.activated',
    'help.level_changed',
    'task.completed',
    'task.failed',
    'parent.interrupt_requested',
    'parent.resume_requested',
    'session.ended',
    'state.transition_applied'
  )
order by occurred_at desc nulls last, seq_no desc nulls last
limit 300;

select id, parent_label
from task
where id in (:referenced_task_ids);
```

补充：
- event 查询阶段先取最近 300 条候选 event；进入 projection 前需在内存里再反转为 `occurred_at asc, seq_no asc` 的正向顺序后再做折叠
- `seq_no` 指 session 内单调递增序号
- 若 event 缺 `occurred_at`，该 event 默认丢弃并记 warning
- 若 event 缺 `seq_no`，不丢弃；同秒内固定排最后，并记 warning
- `parent_visible = null` 视同不可见，v1 默认不过滤补救、不自动当 true 处理
- task 基础信息补查不强绑定 `task.session_id`；具体 join/filter 以真实 schema 为准，这里只表达“按 referenced_task_ids 批量补 label”

## 11. 组装伪代码

```ts
function buildSessionTimelineView(session, eventsDesc, taskMap) {
  const events = reverseToAsc(eventsDesc)
  const normalized = filterBadEvents(events)
  const folded = foldEventsToTimelineItems(normalized, session, taskMap)
  const items = keepSessionStartedAndTail(folded, 100)

  return {
    session_id: session.id,
    items,
    meta: {
      projection_version: 'v1',
      generated_at: nowIso(),
      events_until: lastOccurredAt(normalized),
      has_earlier_items: folded.length > items.length,
    },
  }
}
```

`events_until` 口径补充：
- 取本次纳入 `normalized` 候选集的最后一个 event 的 `occurred_at`
- 若 `normalized` 为空，则 `events_until = null`
- 它表示“这次 projection 已处理到哪一个原始事件时间点”
- 它不保证该 event 最终一定生成了 timeline item
- 因此 `events_until` 可以晚于 `items[-1].occurred_at`

``````

## 12. 验收样例

### 样例 1：开始 + 进入第一个任务
- `session.started`
- `task.activated(task_1)`
- 3 秒后 `state.transition_applied(task_1)`

预期：
- 2 条 item：`session_started`、`task_progress`
- 不是 3 条

### 样例 2：同一任务连续给提示
- 20 秒内连续 3 条 `help.level_changed(task_1)`

预期：
- 只保留 1 条 `hint_given`
- `meta.source_event_count=3`

### 样例 2B：连续 hint 中出现 parent takeover
- 同一 `task_id` 下连续出现多条 `help.level_changed`
- 最后一条 `to_level='parent_takeover'`

预期：
- 不生成 `hint_given`
- 按映射生成 `paused_for_parent`

### 样例 2C：parent takeover 之后又来普通 hint
- 同一 `task_id` 下 30 秒内依次出现：`hint -> parent_takeover -> hint`
- 中间没有 `session_resumed`

预期：
- 这一组不按普通 hint 去噪成最后一个 `hint_given`
- 优先生成 1 条 `paused_for_parent`
- 最后那条普通 hint 静默丢弃，不覆盖这次 `paused_for_parent`
- 如需诊断可记 warning，但不进入家长 timeline

### 样例 3：短暂 pause 又 resume
- `parent.interrupt_requested`
- 10 秒后 `parent.resume_requested`
- 中间无其他关键事件

预期：
- pause / resume 两条都被去抖过滤

### 样例 4：任务失败后继续
- `task.failed(task_1)`
- 随后 `task.activated(task_2)`

预期：
- 有 1 条 `task_failed`
- 有 1 条新的 `task_progress`

### 样例 5：安全中止
- `session.ended`
- `session.end_reason='safety_stop'`

预期：
- 只生成 1 条 `safety_alert`
- `severity=critical`

### 样例 6：家长结束
- `session.ended`
- `session.end_reason='parent_interrupted'`

预期：
- 生成 1 条 `session_ended`
- `display_text=这一轮已由家长结束。`
- `severity=warning`

### 样例 7：系统异常结束
- `session.ended`
- `session.end_reason='system_abort'`

预期：
- 生成 1 条 `session_ended`
- `display_text=这一轮提前结束了。`
- `severity=warning`

### 样例 8：正常结束
- `session.ended`
- `session.end_reason='completed'`

预期：
- 生成 1 条 `session_ended`
- `display_text=这一轮结束了。`
- `severity=info`

### 样例 9：超过 100 条 item 的截断
- 折叠后共有 130 条 item
- 第 1 条是 `session_started`

预期：
- 返回 100 条 item
- 第 1 条 `session_started` 必须保留
- 其余返回最后 99 条
- `meta.has_earlier_items=true`

### 样例 9B：`session_started` 已落在尾部窗口内
- 异常数据下，折叠结果超过 100 条
- 第一条 `session_started` 同时也已落在最后 99 条窗口里

预期：
- 不重复插入第二份 `session_started`
- 最终仍返回去重后的最多 100 条 item
- `timeline_item_id` 保持稳定

### 样例 9C：折叠结果里没有 `session_started`
- 异常数据下，折叠后 item 总数超过 100
- 但整个结果里不存在 `session_started`

预期：
- 不额外伪造 `session_started`
- 直接返回最后 100 条 item
- `meta.has_earlier_items=true`

### 样例 10：紧邻 transition 对家长不可见
- `task.activated(task_1, parent_visible=true)`
- 2 秒后 `state.transition_applied(task_1, parent_visible=false)`
- 查询前提：event SQL 已过滤 `parent_visible=true`

预期：
- 候选集里只剩 `task.activated`
- 只生成 1 条 `task_progress`
- Rule A 不触发
- 这是查询前提决定的预期静默行为，不视为漏数

### 样例 11：task 基础信息缺失
- `task.completed(task_9)`
- task 表里查不到 `task_9`

预期：
- 仍生成 1 条 `task_completed`
- `related_task=null`
- `display_text=孩子完成了当前任务。`
- projection 不报错

## 13. 明确不做

v1 先不做这些：
- 不显示孩子逐字原话
- 不显示模型推理过程
- 不做富文本 explanation
- 不做按 task 分组折叠 UI
- 不做 timeline 搜索

## 14. 下一步

`session_timeline_view` 定住后，下一步就能继续拆：
1. `report_detail_view` 的报告稳定字段
2. timeline / live / report 三份 projection 的字段复用关系

一句话：
- live view 解决“现在怎样了”，timeline view 解决“刚才发生了什么”。
