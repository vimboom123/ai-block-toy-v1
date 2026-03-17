# AI Block Toy 文档总览

这份文档用于给项目建立一个稳定入口，帮助团队快速判断：

- 这个项目现在是什么
- 当前应从哪里开始读
- 哪些文档是主入口，哪些只是背景材料

## 项目是什么

AI Block Toy v1 是一个儿童引导式互动玩具的软件原型。当前仓库重点验证的是软件主链路，而不是硬件成品：

1. 用场景和 task 定义产品行为
2. 用 fixture runner 固定软件契约
3. 用真实模型调用生成 task-level 引导回复
4. 用 session runtime 管理 turn、task 和会话状态
5. 用 demo UI 对外展示当前运行结果

当前状态可以理解为：已经有可运行半成品，正在从“研发验证”往“可正式介绍、可持续迭代的产品项目”整理。

## 推荐阅读顺序

### 想快速了解项目

1. [`../README.md`](../README.md)
2. [`../06-session-runtime/README.md`](../06-session-runtime/README.md)
3. [`../05-dialog-runtime/README.md`](../05-dialog-runtime/README.md)
4. [`../software-e2e/README.md`](../software-e2e/README.md)

### 想看产品规格和数据口径

1. [`../01-product-spec/ai-block-toy-master-outline-v1.md`](../01-product-spec/ai-block-toy-master-outline-v1.md)
2. [`../01-product-spec/ai-block-toy-state-machine-v1.md`](../01-product-spec/ai-block-toy-state-machine-v1.md)
3. [`../01-product-spec/ai-block-toy-backend-schema-v1.md`](../01-product-spec/ai-block-toy-backend-schema-v1.md)
4. [`../02-projections/session-live-view-implementation-spec-v1.md`](../02-projections/session-live-view-implementation-spec-v1.md)
5. [`../02-projections/session-timeline-view-implementation-spec-v1.md`](../02-projections/session-timeline-view-implementation-spec-v1.md)
6. [`../02-projections/home-snapshot-view-implementation-spec-v1.md`](../02-projections/home-snapshot-view-implementation-spec-v1.md)
7. [`../02-projections/report-detail-view-implementation-spec-v1.md`](../02-projections/report-detail-view-implementation-spec-v1.md)

## 当前实现主链

- [`../software-e2e/`](../software-e2e/)
  - 确定性 fixture runner。用于验证状态转换、projection 和 golden 输出，是当前软件契约的稳定底盘。
- [`../05-dialog-runtime/`](../05-dialog-runtime/)
  - 真实文本对话切片。把 Fire Station 场景、prompt 构建和 Ark 模型调用接起来，输出结构化 guidance。
- [`../06-session-runtime/`](../06-session-runtime/)
  - 当前主演示模块。基于 05 的 responder 增加 session、turn、task 推进、最小持久化和 HTTP API。
- [`../ui-mvp-mobile/`](../ui-mvp-mobile/)
  - 当前演示 UI。优先连接 06-session-runtime；当后端不可用时，再退回 sample / fallback。

## 文档分层

### 1. 产品与公共基线

- [`../00-governance/`](../00-governance/)
- [`../01-product-spec/`](../01-product-spec/)
- [`../02-projections/`](../02-projections/)

这部分回答的是“产品应该长什么样、数据应该长什么样、投影应该怎么解释”。

### 2. 可运行实现

- [`../software-e2e/`](../software-e2e/)
- [`../05-dialog-runtime/`](../05-dialog-runtime/)
- [`../06-session-runtime/`](../06-session-runtime/)
- [`../ui-mvp-mobile/`](../ui-mvp-mobile/)

这部分回答的是“今天到底有什么能跑、怎么跑、边界在哪里”。

### 3. 背景资料和历史阶段材料

- [`../03-software-e2e-prep/`](../03-software-e2e-prep/)
- [`../04-software-e2e-hardening/`](../04-software-e2e-hardening/)
- [`../archive/`](../archive/)

这些目录保留了阶段计划、准备材料和历史稿，仍有参考价值，但不应作为当前默认入口。

## 当前不该怎么理解这个项目

- 它不是完整的硬件玩具交付包。
- 它不是正式上线的后端系统。
- 它不是已经解决了儿童理解、可靠 NLU 和报告投影的完整产品。
- 它也不是只剩概念稿的纸面方案；当前已经有可运行主链和可演示 UI。

## 当前建议对外口径

可以这样介绍：

“AI Block Toy v1 是一个儿童引导式互动玩具的软件原型项目。当前版本已经打通了从场景定义、模型引导回复、会话状态管理到演示 UI 的主链路，能跑真实对话和最小 session；同时保留了完整的规格、projection 和软件 E2E 验证体系。它还不是 production 产品，但已经不是纯概念稿或静态页面演示。”

## 下一步重点

- 把 session runtime 的理解与完成判定做得更可靠
- 把 projection 规格真正落成服务
- 把当前单场景原型逐步推广到更多场景
- 在保持口径稳定的前提下，再整理适合公开同步的材料
