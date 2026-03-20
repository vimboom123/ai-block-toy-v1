# AI Block Toy Parent View

这版不再主打 task snapshot viewer。
现在页面已经开始按 `session / current_turn / turns / tasks` 这套最小会话结构来跑。

## 这页现在是什么

它现在更像一个最小会话查看器 + turn 提交器：

1. 首页：看 session 状态、当前 task、最新 turn、数据来源
2. 会话页：看 `current_turn`、`turn history`、`task timeline`
3. 会话页：直接提一轮 `child_input_text + task_signal`
4. 摘要页：按当前 session 压一个最小摘要

## 推荐预览方式

直接起 Phase 6 server：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session
python3 -m session_runtime.server --port 4183
```

然后打开：

```text
http://127.0.0.1:4183/
```

这时候同一个端口会同时给你：

- UI 根路径 `/`
- Session API `/api/session-runtime`
- 健康检查 `/api/health`

## 页面接数顺序

页面现在按这个顺序取数据：

1. 真实 `Phase 6 session runtime`
2. 本地 `fire-station-runtime-sample.json`
3. 内置 fallback session

也就是说，即便后端没起，页面至少也会按 Phase 6 的 session shape 展示，不会再退回旧 task-only 心智。

默认 session 选择顺序是：

1. URL 里的 `?session_id=...`
2. `health.latest_session_id`
3. `health.latest_active_session_id`
4. localStorage 里的上次 session

这样页面默认会跟最新一次完整语音流程走，不会被一个 bootstrapped 的旧 active session 卡在 `0 turns`。

## 当前最小核心字段

### session 层

- `session_id`
- `lifecycle_state`
- `status`
- `current_task_id / current_task_index`
- `current_turn_id / current_turn_index`
- `turn_count / task_count / completed_task_count`
- `session_scope`
- `runtime_mode`

### current_turn / turns 层

- `turn_id / turn_index`
- `task_id / task_index / task_name`
- `child_input_text`
- `requested_task_signal / resolved_task_signal`
- `task_progress`
- `assistant_reply.reply_text`
- `assistant_reply.guidance_type`
- `assistant_reply.next_expected_action`

### tasks 层

- `task_id`
- `name`
- `goal`
- `expected_child_action`
- `status`
- `turn_count`

## 这轮 UI 有什么变化

- 页面不再把“task 回复”错当成“当前会话”
- 会话页新增了 `session 状态卡`
- 会话页新增了 `当前回合` 卡
- 会话页新增了 `turn history`
- 会话页新增了最小 turn 提交表单
- 页面会自动轮询 Phase 6 snapshot，所以 voice session 在后台推进时，父端页能自己跟上进度
- task timeline 现在退成辅助视图，不再冒充会话主视图

## 当前边界

- session 仍然只在进程内内存里
- 页面没有做用户登录 / 多 session 管理
- `task_signal=auto` 还是薄 heuristic
- 报告页仍然只是 session 摘要，不是真报告系统
