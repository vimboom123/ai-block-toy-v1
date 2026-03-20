# Dialog Runtime

`runtimes/dialog` 是项目当前最小的“真实模型对话运行时”模块。它负责把 Fire Station 场景定义、prompt 构建和 Ark 文本调用接起来，输出结构化的 task-level guidance 结果。

这个目录的职责是验证真实模型回复是否能进入项目约定的数据形状，不承担正式会话服务、报告服务或通用后端框架的职责。

## 当前定位

- 为 `classic_world_fire_station` 场景生成真实文本引导结果
- 提供 task 级别的 smoke CLI，便于快速验证 prompt 和模型回包
- 提供一个超薄 HTTP bridge，方便 `apps/parent-view/` 联调
- 作为 [`../runtimes/session/README.md`](../runtimes/session/README.md) 的底层 responder 来源

## 当前能力

- 读取 `scenes/classic_world_fire_station.scene.yaml`
- 按 `task_id` 选择候选任务并构建 system / context / user prompt
- 调用 Ark 文本接口
- 将模型输出整理为固定结构：
  - `reply_text`
  - `guidance_type`
  - `next_expected_action`
  - `error`
- 在请求失败或配置缺失时，返回同形状的 `runtime_error` 结果，而不是静默失败

## 目录说明

- `runtime/scene_loader.py`
  - 场景 YAML 读取与 task 选择
- `runtime/dialog_prompt_builder.py`
  - 场景与 task 对应的 prompt 组装
- `runtime/ark_client.py`
  - 最小 Ark client，兼容 `/chat/completions` 和 `/responses`
- `runtime/dialog_runtime.py`
  - Fire Station dialog runtime 主封装
- `runtime/fire_station_smokes.py`
  - 推荐 smoke CLI
- `runtime/ui_runtime_server.py`
  - 提供最小 runtime API，并同时服务 `apps/parent-view/`
- `scenes/classic_world_fire_station.scene.yaml`
  - 当前唯一接通真实运行时的场景包

## 运行前准备

默认从本目录的 `.env.local` 读取 Ark 配置。至少需要提供一个模型标识和 API key：

- `ARK_API_KEY`
- `ARK_MODEL` 或 `ARK_MODEL_ID` 或 `ARK_ENDPOINT_ID`

可选项：

- `ARK_REQUEST_URL` 或 `ARK_CHAT_COMPLETIONS_URL`
- `ARK_API_BASE_URL`
- `ARK_API_CHAT_PATH`
- `ARK_TIMEOUT_SECONDS`
- `ARK_TEMPERATURE`
- `ARK_MAX_TOKENS`
- `ARK_REASONING_EFFORT`

说明：

- 这条链路目前只依赖 `ARK_API_KEY`
- `VOLCENGINE_ACCESS_KEY_ID` / `VOLCENGINE_SECRET_ACCESS_KEY` 不参与当前最小文本运行时
- 如果本机没有 `PyYAML`，loader 会退回系统 Ruby 的 `YAML.safe_load`

## 快速运行

在本目录执行：

```bash
python3 -m runtime.fire_station_smokes
```

默认顺序执行 `fs_002`、`fs_003`、`fs_004`。

只跑一个 task：

```bash
python3 -m runtime.fire_station_smokes --task-id fs_003
```

兼容旧入口：

```bash
python3 runtime/dialog_runtime_smoke.py --task-id fs_002
```

成功时会输出单个对象或对象数组；如果请求失败，会保留同样的 JSON 结构，并把异常写入 `error`。

## 最小 HTTP Bridge

如果需要给 `apps/parent-view/` 提供真实 runtime 数据，可以直接启动本目录的本地 server：

```bash
python3 -m runtime.ui_runtime_server --port 4173
```

启动后提供：

- UI：`http://127.0.0.1:4173/`
- Runtime API：`http://127.0.0.1:4173/api/fire-station/runtime`
- Health：`http://127.0.0.1:4173/api/health`

按需指定 task：

```bash
python3 -m runtime.ui_runtime_server --task-id fs_002 --task-id fs_003
```

`GET /api/fire-station/runtime` 返回三块内容：

- `session`
  - 本次快照的元信息，`session_scope=request_scoped_snapshot`
- `meta`
  - 数据来源、摘要模式、免责声明、错误标志
- `tasks`
  - 每个 task 的真实 runtime 结果

这条 API 的设计目标是“让 UI 能读到真实 runtime”，不是提供长期稳定的正式接口契约。

## 当前边界

- 只覆盖 `classic_world_fire_station` 场景，不是通用多场景平台
- 不保存 session，不记录 turn history，不维护 task lifecycle
- 每次请求都会直接跑 prompt / LLM，没有结果缓存
- 不包含鉴权、限流、观测、重试队列或任务编排
- UI bridge 只是联调层，不是正式 BFF

## 在项目中的位置

如果你想看“当前用户可见的完整演示链路”，优先从 [`../runtimes/session/README.md`](../runtimes/session/README.md) 开始。

如果你想确认“真实模型能不能按项目格式回包”，这里就是第一入口。

## 下一步

- 稳定 Fire Station 场景下的 prompt 口径和错误处理
- 为上层 session runtime 提供更稳定的 responder 行为
- 在确认 task-level 结果稳定后，再扩到更多场景或更完整的接口层
