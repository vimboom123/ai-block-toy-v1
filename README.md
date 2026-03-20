# AI Block Toy v1

`AI Block Toy v1` 是一个面向儿童互动积木玩具的软件原型仓库。  
这次整理后的目标很简单：仓库按职责读，不再按阶段编号读。

## 先看什么

如果你第一次进这个仓库，按这个顺序看：

1. [`README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/README.md)
2. [`docs/index.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/docs/index.md)
3. [`runtimes/session/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session/README.md)
4. [`runtimes/voice/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice/README.md)
5. [`apps/parent-view/README.md`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/apps/parent-view/README.md)

## 现在的仓库结构

- `runtimes/`
  - 可运行主链。这里是项目今天真正能跑的部分。
- `apps/`
  - UI 原型和演示端。
- `specs/`
  - 产品规格和 projection 口径。
- `governance/`
  - 公共规则、session 外显口径、家长报告规则。
- `verification/`
  - fixture、contract、golden 等验证链。
- `docs/`
  - 导航、总览、阅读顺序。
- `archive/`
  - 历史阶段材料和旧草稿，不作为默认入口。

## 活跃模块

- [`runtimes/dialog`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/dialog)
  - 最小真实文本对话运行时。
- [`runtimes/session`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session)
  - 当前项目主运行链，负责 session / turn / task / UI API。
- [`runtimes/voice`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice)
  - 语音链路、设备桥接、Phase 7。
- [`apps/parent-view`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/apps/parent-view)
  - 家长查看页 / 演示 UI。
- [`verification/software-e2e`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/verification/software-e2e)
  - 软件 E2E 与 fixture 回放。

## 最快启动

当前默认先看 `session runtime + parent view`：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session
python3 -m session_runtime.server --port 4183
```

打开：

- [http://127.0.0.1:4183/](http://127.0.0.1:4183/)

如果你要跑语音主链：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_fast.py
```

如果你要跑软件验证：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/verification/software-e2e
npm run check:built-in-fixtures
```

## 这个仓库不再怎么读

这次重组后，根目录不再推荐按 `00 / 01 / 05 / 06 / 07` 这种阶段编号理解。  
那些编号目录已经拆成职责目录，历史阶段材料全部放进了 [`archive/history`](/Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/archive/history)。

## 当前边界

- 仍然是原型，不是 production service。
- 当前主场景仍然是 `classic_world_fire_station`。
- 设备桥接可用，但不是正式硬件平台。
- 历史文档很多，默认只看 `README -> docs -> runtimes -> apps` 这条链。

## 下一步

- 把 active docs 全部按新目录继续收口。
- 把 `archive/` 里的历史材料继续去噪。
- 把运行脚本、测试和外部桥接统一到新路径口径。
