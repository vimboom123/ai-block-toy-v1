# Session Runtime

`runtimes/session` 是当前项目最完整、最接近“产品主链路”的可运行模块。它在 [`../dialog/README.md`](../dialog/README.md) 的真实对话能力之上，增加了 session、turn、task 推进、最小持久化，以及同端口 UI + HTTP API。

如果只看一个运行入口，优先看这里。

## 当前模块负责什么

- 创建并维护 stateful session
- 接收一轮一轮的 `child_input_text`
- 记录 turn history，并驱动 task lifecycle 变化
- 通过最小 API 暴露 `session / current_task / current_turn / tasks / turns`
- 为 `apps/parent-view/` 提供当前默认的数据源

## 当前已经实现的能力

- `POST /api/session-runtime/sessions`
  - 新建 session
- `GET /api/session-runtime/sessions/:session_id`
  - 读取当前 session snapshot
- `POST /api/session-runtime/sessions/:session_id/turns`
  - 提交新 turn，并返回最新 snapshot
- 默认新建 session 会使用完整 Fire Station 链 `fs_001 -> fs_006`
- 默认本地 JSON 持久化
  - 服务重启后可恢复 session
- 同端口静态 UI
  - 直接打开首页就能查看 session，并继续提 turn

## 返回结构

所有成功响应使用同一套快照形状：

```json
{
  "ok": true,
  "api_version": "phase6_session_runtime_v1",
  "snapshot_kind": "session_state",
  "session": {},
  "current_task": {},
  "current_turn": {},
  "tasks": [],
  "turns": [],
  "viewer_context": {},
  "meta": {}
}
```

其中最关键的语义是：

- `session`
  - 当前会话的顶层状态，包括 `lifecycle_state`、`status`、当前 task / turn 指针，以及完成度统计
- `current_task`
  - 当前活跃 task
- `current_turn`
  - 当前 session 的最新一轮 turn，不会因为 UI 回看旧 task 而改语义
- `tasks[*].last_turn_id / last_turn_index`
  - 每个 task 自己对应的最新 turn
- `viewer_context`
  - UI 默认落点和查看规则

也就是说，这里已经不是 task 快照，而是一套最小 session runtime。

## 目录说明

- `session_runtime/core.py`
  - session / task / turn 状态模型与推进逻辑
- `session_runtime/phase5_bridge.py`
  - 复用 `runtimes/dialog` 的 scene 和 Ark responder
- `session_runtime/persistence.py`
  - 最小 JSON store
- `session_runtime/server.py`
  - HTTP API + 静态 UI 服务入口
- `tests/`
  - 当前模块的单元测试和接口测试

## 快速运行

默认方式：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session
python3 -m session_runtime.server --port 4183
```

启动后提供：

- UI：`http://127.0.0.1:4183/`
- API root：`http://127.0.0.1:4183/api/session-runtime`
- Health：`http://127.0.0.1:4183/api/health`

默认 JSON store 路径：

```text
runtimes/session/state/session-runtime-store.json
```

如果只想跑临时内存态：

```bash
python3 -m session_runtime.server --port 4183 --memory-only
```

## 最小接口示例

创建 session：

```bash
curl -s -X POST http://127.0.0.1:4183/api/session-runtime/sessions \
  -H 'Content-Type: application/json' \
  -d '{}'
```

提交 turn：

```bash
curl -s -X POST http://127.0.0.1:4183/api/session-runtime/sessions/<session_id>/turns \
  -H 'Content-Type: application/json' \
  -d '{"child_input_text":"外面着火了","task_signal":"task_completed"}'
```

读取 session：

```bash
curl -s http://127.0.0.1:4183/api/session-runtime/sessions/<session_id>
```

## 当前 task signal

当前支持：

- `auto`
- `keep_trying`
- `task_completed`
- `end_session`

说明：

- `auto` 仍然只是最小 heuristic
- `task_completed` 和 `end_session` 仍是当前主要推进开关
- 当前重点是把会话状态链路跑通，不是假装已经完成稳定 NLU

## 当前边界

- 当前默认只围绕 Fire Station 场景工作，不是通用多场景 session 平台
- 持久化只有本地 JSON 文件或进程内内存，不包含数据库、用户体系、并发隔离和后台治理
- turn completion 与 task completion 的自动判断仍然很薄
- UI 是内部演示界面，不是最终产品前端
- report / live / timeline projection 规格已存在，但这里还没有落成独立正式服务
- 如果 Ark 配置缺失，API 会明确返回 runtime error，而不是伪造正常回复

## 测试

在本目录执行：

```bash
pytest -q
```

当前测试覆盖核心 session 逻辑与 HTTP server 的基础行为。

## 下一步

- 提升 turn 级 completion / intent 判定，减少手工 signal 依赖
- 补强 store 损坏恢复、session 清理和持久化策略
- 让 projection 规格逐步接到真实 runtime 输出
- 在现有单场景稳定后，再考虑更完整的多场景和多端接入
