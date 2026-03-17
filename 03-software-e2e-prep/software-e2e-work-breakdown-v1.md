# Software E2E Work Breakdown v1

项目：AI积木玩具  
阶段：软件全链路跑通准备  
日期：2026-03-17  
状态：v1 拆工稿，作为实现排期与动态分配底稿

## 1. 本轮主要矛盾

当前不是继续堆文档，而是把已有规格变成**可执行的软件验证链路**。

说白了，下一阶段的核心矛盾有两个：
1. 怎么用 fixture 稳定驱动状态机 / 事件 / 物化 / projection
2. 怎么尽快拿到一个能反复跑的 mock runner 闭环，而不是继续空谈

所以本阶段 lead 应该是：**Codex 做实现主力**。  
Claude Code 做后置 review。  
主智能体继续当 PM / 验收 / 风险判断。  
Gemini 这轮不是主 lead，除非后面要补演示壳子。  
Qwen / Kimi 只在中文收口或命名上补位。

---

## 2. 本阶段交付标准

本轮完成，至少要看到这 5 个东西：

1. 6 个最小 fixture 有稳定定义
2. mock runner 能按 fixture 吐标准 event
3. entity materializer 能产出 `session / task / parent_report` 当前态
4. 四类 projection 至少能用固定 DTO 跑出结果
5. golden case 能自动校验关键字段

如果只做了 event feed，没有 projection 断言，不算跑通。  
如果只做了 DTO 假数据，没有状态机推进，也不算跑通。

---

## 3. 动态分配

### 3.1 当前角色

- **主智能体**：PM / 拆工 / 风险控制 / 验收 / 汇报
- **Codex**：实现 lead，负责 mock runner / materializer / projection pipeline
- **Claude Code**：实现后 review，查状态迁移、schema 一致性、边界漏洞
- **Gemini**：如需要 debug 页面、fixture 可视化面板、演示壳子，再接前端表达层
- **Qwen / Kimi**：中文命名、中文文档补丁、汇报收口
- **Oracle**：milestone 复盘，不常驻

### 3.2 串行顺序

这轮默认不能无脑并发。

正确顺序应该是：
1. fixture 冻结
2. mock runner 架构冻结
3. Codex 实现主链路
4. Claude Code review
5. 主智能体验收
6. 必要时 Oracle milestone 复盘

---

## 4. 模块拆解

### W1. Fixture spec 冻结

**目标**：把输入脚本和期望输出先钉住。  
**输入**：`software-e2e-fixtures-v1.md`  
**输出**：6 个 fixture 文件骨架 / schema / 命名规范

**验收点**：
- 每个 fixture 都有 terminal 断言
- 覆盖 happy / hint / timeout / parent / safety 主路径
- 关键 projection 断言已写死

### W2. Mock runner architecture

**目标**：定义 runner 怎么消费 fixture、怎么吐 event、怎么驱动物化与 projection。  
**建议输出文档**：`mock-runner-architecture-v1.md`

**至少说明**：
- fixture loader
- event feed loop
- state machine driver adapter
- materializer hook
- projection rebuild trigger
- golden assertion runner

**验收点**：
- 不依赖真 ASR / 真 TTS / 真 LLM
- 能稳定重放同一个 fixture
- seq_no / occurred_at / causation 链路有明确口径

### W3. Event contract adapter

**目标**：定义 mock 输入如何映射成标准 event envelope。  
**输出**：event mapping 表 / adapter stub

**验收点**：
- `event_type`
- `producer`
- `payload_public / payload_private`
- `parent_visible`
- `state_before / state_after`
- `correlation_id`

这些字段不能含糊。

### W4. Entity materializer

**目标**：让 session / task / parent_report 能从 event 重建当前态。  
**输出**：materializer 设计 + reducer 列表

**验收点**：
- session 聚合字段能被正确更新
- task 激活 / 完成 / 失败状态正确
- parent_report 能区分 `published / partial`

### W5. Projection builders

**目标**：四类 projection 都能从实体或 event 派生。  
**输出**：
- live builder
- timeline builder
- report detail builder
- home snapshot builder

**验收点**：
- DTO 字段口径对齐现有 spec
- 安全可读层不穿帮
- 失败 / 中止场景仍能产出降级结果

### W6. Golden assertion runner

**目标**：让 fixture 跑完后自动校验关键字段。  
**输出**：golden compare 方案

**验收点**：
- 支持按 fixture 执行
- 支持断言 terminal session/report 状态
- 支持 projection 关键字段校验
- 报错信息能告诉人到底哪块炸了

---

## 5. 推荐实现顺序

建议按下面顺序推进，别乱插：

1. W1 fixture spec 冻结
2. W2 mock runner architecture
3. W3 event contract adapter
4. W4 entity materializer
5. W5 live + timeline projection
6. W5 report detail + home snapshot
7. W6 golden assertion runner
8. Claude Code review + 主智能体验收

原因很简单：
- 先定输入
- 再定执行器
- 再做物化
- 最后做输出和校验

不然就会出现前后口径互相打架，改得像狗屁一样。

---

## 6. 风险清单

### R1. fixture 写得太虚

风险：只写故事，不写可执行字段。  
后果：Codex 开始实现时还得边猜边补。

**处理**：fixture 必须带 terminal 断言和 projection 断言。

### R2. event schema 和 projection spec 脱节

风险：event 吐出来了，但 live/report 字段拼不出来。  
后果：看似跑通，实际全是假闭环。

**处理**：在 W3 就把 projection 所需字段反推回 event 合同。

### R3. parent / safety 边界场景太晚处理

风险：正常链路能跑，异常链路一碰就炸。  
后果：后面返工最大。

**处理**：happy path 之后，优先补 parent path，不要把边界留到最后才看。

### R4. review 太早介入

风险：实现还没成型就让 reviewer 审空气。  
后果：时间白费。

**处理**：先让 Codex 把主链打通，再让 Claude Code 做后置 review。

---

## 7. 当前建议的下一手

现在最该补的不是再写 PRD，而是立刻补下面这个：

1. `mock-runner-architecture-v1.md`
2. 然后把 6 个 fixture 落成真实文件骨架
3. 再交给 Codex 开写 runner / reducers

也就是说，这轮我作为 PM 先把输入与架构底稿补齐，再把实现段交出去，这才像个正经工作室，不是瞎鸡巴堆字。