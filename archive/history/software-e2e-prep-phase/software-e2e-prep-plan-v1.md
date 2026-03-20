# Software E2E 跑通准备方案 v1

项目：AI积木玩具  
阶段：软件全链路跑通准备  
日期：2026-03-16  
状态：v1 草案，作为工作室模式下的软件主线执行底稿

## 1. 当前目标

在不依赖真实硬件的前提下，先把软件主链路跑通。

要跑通的不是单个接口，而是完整闭环：
- session 创建
- 状态机推进
- event 写入
- live projection 生成
- timeline projection 生成
- report 生成
- 首页 / 会话页 / 报告页能读到稳定 DTO

## 2. 当前阶段交付物

这一阶段先输出 3 件核心东西：
1. fixture / golden cases
2. mock runner 方案
3. work breakdown / 实现顺序

## 3. fixture 分类

### 3.1 Happy path
- 正常开始
- 正常完成 1~2 个 task
- 正常结束
- 生成完整 report

### 3.2 Hint escalation path
- 孩子低置信 / 错答
- `none -> light_nudge -> guided_hint`
- 最终完成任务

### 3.3 Parent takeover path
- 连续失败
- 进入 `parent_takeover`
- 触发 `parent_interrupt_hold`
- 家长恢复或终止

### 3.4 Safety stop path
- 中途命中安全停止
- 进入 `abort_cleanup`
- 生成 partial report

### 3.5 Timeout / no response path
- 等待超时
- reengagement
- hint 或 demo
- 最终继续或中止

## 4. mock runner 目标

mock runner 不做真正语音能力，只负责：
- 按 fixture 吐 event
- 驱动 session / task / report 物化态
- 产出 live / timeline / report 三类 projection
- 让前端或调试页能验证整条链路

## 5. 最小模块拆解

### M1. Session bootstrap
- 创建 session
- 绑定 theme
- 进入 warming_up

### M2. State machine driver
- 消费 fixture 输入
- 推动状态迁移
- 写 `state.transition_applied`

### M3. Event store
- 记录标准 event
- 保证排序与 seq_no

### M4. Entity materializer
- 把 event 物化成 `session / task / parent_report`

### M5. Projection builders
- `session_live_view`
- `session_timeline_view`
- `home_snapshot_view`
- `report_detail_view`

### M6. Debug / validation surface
- 输出 DTO JSON
- 跑 golden case 对比

## 6. 实现顺序

1. 先做 fixture 定义
2. 再做 mock runner 的 event feed
3. 再做 session/task 物化
4. 再做 live view
5. 再做 timeline view
6. 再做 report generation + report detail
7. 最后补 home snapshot

## 7. 当前工作室分工

- 主智能体：PM / 拆工 / 验收 / 汇报
- Codex：后续实现主力（mock runner / 物化 / projection builder）
- Claude Code：逻辑复核 / 风险审查 / 实现后复审
- Gemini：如需要前端 mock 页面或演示壳子，再介入
- Qwen/Kimi：中文汇报、命名与文档收口按需介入

## 8. 当前风险

- 文档虽已过主审，但 fixture 还没真正定稿
- mock runner 一旦开写，可能会暴露新的 schema / event 缺口
- 报告生成链路和 live/timeline 的时序一致性要重点盯

## 9. 下一步

紧接着要做：
1. `software-e2e-fixtures-v1.md`
2. `mock-runner-architecture-v1.md`
3. 软件主线 work breakdown
