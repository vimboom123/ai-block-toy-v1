# AI Block Toy 文档导航

这份索引只管一件事：把“现在该看什么”讲清楚。  
默认只导航活跃模块，历史阶段资料统一放到后面。

## 推荐阅读顺序

1. [`README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/README.md)
2. [`runtimes/session/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session/README.md)
3. [`runtimes/voice/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice/README.md)
4. [`apps/parent-view/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/apps/parent-view/README.md)
5. [`verification/software-e2e/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/verification/software-e2e/README.md)

## 活跃实现

- [`runtimes/dialog`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/dialog)
  - 文本对话运行时。
- [`runtimes/session`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session)
  - 当前主运行链。
- [`runtimes/voice`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice)
  - 语音与设备接入链。
- [`apps/parent-view`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/apps/parent-view)
  - 家长 UI / 演示前端。

## 规格与口径

- [`specs/product`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/specs/product)
  - 产品规格、状态机、后端 schema。
- [`specs/projections`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/specs/projections)
  - live / timeline / report / home 的 projection 口径。
- [`governance`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/governance)
  - 对外可见 session 规则和家长报告规则。

## 验证

- [`verification/software-e2e`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/verification/software-e2e)
  - fixture runner、golden、contract 验证。

## 历史资料

- [`archive/history`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/archive/history)
  - 以前的阶段性材料，不再作为默认入口。
- [`archive/root-drafts`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/archive/root-drafts)
  - 根目录旧草稿，已移出主入口。

## 现在不建议怎么读

- 不建议从 `archive/` 开始读。
- 不建议先看零散草稿再猜当前实现。
- 不建议再按“Phase 5 / 6 / 7 目录号”找东西；请按 `runtimes / apps / specs / verification` 找。
