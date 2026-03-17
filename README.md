# AI Block Toy v1

`AI Block Toy v1` 是一个面向儿童引导式互动玩具的软件原型项目。当前仓库覆盖了场景与任务定义、确定性软件验证、真实文本对话运行时、最小会话运行时，以及一套用于演示的移动端 UI。

项目当前的目标不是一次性做完整产品，而是先把一条可验证、可运行、可继续扩展的主链路做实：从场景定义，到对话生成，到 session / turn / task 状态，再到面向查看与演示的 UI。

## 当前状态

- 已有可运行半成品：[`06-session-runtime/README.md`](./06-session-runtime/README.md) 是当前最完整的演示入口，支持创建 session、提交 turn、推进 task，并在同端口查看 UI。
- 真实模型调用已接通：[`05-dialog-runtime/README.md`](./05-dialog-runtime/README.md) 已能对接 Ark 文本接口，返回结构化的 task-level 引导结果。
- 契约验证主链已建立：[`software-e2e/README.md`](./software-e2e/README.md) 是当前唯一推荐的软件 E2E runner，用于 fixture 回放、状态转换和 projection 断言。
- 产品与投影基线已成形：`00-governance/`、`01-product-spec/`、`02-projections/` 中的规格文档足以支撑当前实现继续推进。
- 当前仍是产品原型，不是 production service。持久化、可靠理解、多场景、多端接入和正式 projection 服务都还没有做完。

## 推荐入口

### 1. 预览当前最完整演示链路

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/06-session-runtime
python3 -m session_runtime.server --port 4183
```

打开 `http://127.0.0.1:4183/`。

说明：

- UI、Session API、Health check 由同一个进程提供
- 默认使用本地 JSON 文件保存 session
- 如果 Ark 配置缺失，服务仍可启动，但 turn 回复会明确返回 runtime error

### 2. 单独验证真实对话运行时

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/05-dialog-runtime
python3 -m runtime.fire_station_smokes --task-id fs_002
```

如果要给 `ui-mvp-mobile/` 提供最小 runtime API：

```bash
python3 -m runtime.ui_runtime_server --port 4173
```

### 3. 跑确定性软件验证

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/software-e2e
npm run check:built-in-fixtures
```

更多命令见 [`software-e2e/README.md`](./software-e2e/README.md)。

## 核心目录

目录编号沿用研发阶段命名，但当前应按模块职责理解，而不是把所有内容都当成“阶段日志”。

- `00-governance/`
  - 公共规则、session 对外基线、家长报告生成原则
- `01-product-spec/`
  - 产品主规格、状态机、schema、映射和界面结构说明
- `02-projections/`
  - home / live / timeline / report 等 projection 实现口径
- `software-e2e/`
  - 当前唯一推荐的软件 E2E 与契约回放入口
- `05-dialog-runtime/`
  - Fire Station 场景的真实文本对话运行时切片
- `06-session-runtime/`
  - 当前主演示链路，提供 stateful session runtime + API + demo UI
- `ui-mvp-mobile/`
  - 用于联调与演示的移动端 UI 原型
- `03-software-e2e-prep/`、`04-software-e2e-hardening/`
  - 历史阶段材料与准备文档，保留参考价值，但不作为默认入口
- `archive/`
  - 已替代或废弃的留档资料
- `docs/`
  - 项目总览与文档导航

## 当前能力边界

- 当前真实运行时只覆盖 `classic_world_fire_station` 场景，不是通用多场景引擎。
- `05-dialog-runtime` 仍是 request-scoped runtime，每次请求都会真实跑 prompt / LLM，没有结果缓存。
- `06-session-runtime` 的 session 持久化目前只到单个本地 JSON 文件，不含数据库、租户隔离或后台任务体系。
- `task_signal=auto` 只是最小 heuristic，不代表已经完成稳定的儿童输入理解或意图识别。
- `ui-mvp-mobile/` 是内部演示 UI，不是最终面向家长或孩子的正式产品界面。
- report / live / timeline projection 的规格已写清，但还没有收成独立、稳定的正式服务。
- 仓库里保留了不少阶段性文档和历史稿，阅读时应优先看当前入口文档，不要把旧计划稿当成当前实现说明。

## 文档导航

- 总览入口：[`docs/index.md`](./docs/index.md)
- 当前主运行模块：[`06-session-runtime/README.md`](./06-session-runtime/README.md)
- 真实文本对话切片：[`05-dialog-runtime/README.md`](./05-dialog-runtime/README.md)
- 确定性软件验证：[`software-e2e/README.md`](./software-e2e/README.md)
- 产品规格主入口：[`01-product-spec/ai-block-toy-master-outline-v1.md`](./01-product-spec/ai-block-toy-master-outline-v1.md)
- 状态机：[`01-product-spec/ai-block-toy-state-machine-mermaid-final-v1.md`](./01-product-spec/ai-block-toy-state-machine-mermaid-final-v1.md)

## 当前建议对外口径

可以这样介绍：

“AI Block Toy v1 是一个儿童引导式互动玩具的软件原型项目。当前版本已经打通了从场景定义、模型引导回复、会话状态管理到演示 UI 的主链路，能跑真实对话和最小 session；同时保留了完整的规格、projection 和软件验证基线，用于后续语音与硬件联调。”

## 下一步

- 提升 `task_signal=auto` 的完成判定和输入理解质量，减少手工 signal 依赖。
- 将 `02-projections/` 中的 live / report / timeline 规格逐步落成可调用服务。
- 在现有 Fire Station 场景跑稳之后，再扩展更多场景和设备联动。
