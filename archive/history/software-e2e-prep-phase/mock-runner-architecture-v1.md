# Mock Runner Architecture v1

项目：AI积木玩具  
阶段：软件全链路跑通准备  
日期：2026-03-17  
状态：v1 架构底稿，供 Codex 进入实现

## 1. 这玩意要解决什么

目标不是做一个花里胡哨的模拟器，而是做一个**稳定、可重放、可断言**的 software E2E runner。

它要在**没有真实硬件、没有真语音链路、没有真 LLM 输出**的前提下，把下面这条链打通：

fixture input  
→ event envelope  
→ state transition  
→ entity materialize  
→ projection build  
→ golden assert

说白了，mock runner 就是这条软件闭环的假发动机。

---

## 2. 非目标

这轮先明确不做这些：
- 不做真实 ASR / TTS
- 不做真实语音流
- 不做真实设备驱动
- 不做复杂可视化控制台
- 不把它做成通用 workflow 引擎

只做 AI积木玩具这条主链路够用的 runner。

---

## 3. 总体结构

建议拆成 6 个模块：

1. `fixture_loader`
2. `runner_clock`
3. `event_adapter`
4. `state_driver`
5. `materializer_pipeline`
6. `projection_assertion_runner`

### 3.1 数据流

```text
fixture yaml/json
  -> fixture_loader
  -> normalized steps
  -> event_adapter
  -> standard event envelope
  -> append event store
  -> state_driver / reducers
  -> materialized session/task/report
  -> projection builders
  -> golden assertion runner
```

---

## 4. 模块定义

### 4.1 fixture_loader

**职责**：
- 读 fixture 文件
- 做 schema 校验
- 把 `steps` 正规化
- 补默认值

**输入**：
- `fixtures/*.yaml`

**输出**：

```ts
interface NormalizedFixture {
  id: string
  category: string
  themeCode: string
  bootstrap: SessionBootstrap
  steps: FixtureStep[]
  expected: FixtureExpected
}
```

**硬规则**：
- fixture 加载阶段就报 schema 错，不要把烂数据放进主流程
- 所有 step 必须可排序
- 所有 actor/type 都要在白名单里

### 4.2 runner_clock

**职责**：
- 提供伪时间推进
- 决定 `occurred_at`
- 让超时 / reengagement / pause 这种场景可重复回放

建议模式：
- 默认用逻辑时钟，不用真实 sleep
- `at: 8s / 15s / 24s` 这类字段只映射成逻辑 timestamp

**原因**：
- 真 sleep 太慢，回归测试像傻逼
- 逻辑时钟更稳定，可重复跑

### 4.3 event_adapter

**职责**：
- 把 fixture 的外部动作翻译成标准 event envelope
- 统一补上 `seq_no / producer / correlation_id / payload_public/private`

**示例**：

fixture step：
```yaml
- at: 12s
  actor: child
  type: answer_incorrect
  payload:
    confidence_score: 0.38
```

adapter 之后：
```json
{
  "event_type": "child.answer_evaluated",
  "producer": "system",
  "confidence_score": 0.38,
  "confidence_level": "low",
  "payload_private": {
    "result": "incorrect"
  },
  "payload_public": {
    "display_text": "孩子这一步还没答对"
  }
}
```

**关键点**：
- fixture step 不一定等于最终 event_type
- 真正入库前必须过 adapter
- adapter 是 runner 和正式 schema 之间的防抖层

### 4.4 state_driver

**职责**：
- 读取当前 `session.current_state`
- 根据 event + rule 计算迁移
- 产出 `state.transition_applied`

**建议接口**：

```ts
nextState = stateDriver.apply({
  currentState,
  event,
  sessionSnapshot,
  taskSnapshot
})
```

**输出**：
- next state
- side effects（如激活 task、升级 hint、进入 parent takeover）
- transition metadata（rule_id / reason）

**硬规则**：
- 状态迁移必须显式落 event
- 不能偷偷改 session/task，不留痕

### 4.5 materializer_pipeline

**职责**：
- event append 后触发 reducers
- 更新 `session / task / parent_report`
- 保证 projection builder 拿到的是稳定当前态

建议 reducer 至少拆成：
- `sessionReducer`
- `taskReducer`
- `reportReducer`

**sessionReducer** 负责：
- `status`
- `public_stage`
- `current_state`
- `current_task_id`
- `turn_count`
- `retry_count`
- `completed_task_count`
- `help_level_peak`

**taskReducer** 负责：
- 激活 / 完成 / 失败 / 跳过
- `help_level_current / peak`
- `attempt_count`
- `parent_note`

**reportReducer** 负责：
- partial / published 判定
- notable moments 聚合
- parent_summary / suggestion

### 4.6 projection_assertion_runner

**职责**：
- 每个 fixture 执行结束后构建四类 projection
- 按 golden 断言关键字段
- 输出失败 diff

至少构建：
- `session_live_view`
- `session_timeline_view`
- `report_detail_view`
- `home_snapshot_view`

断言策略：
- 关键字段精确匹配
- 非关键易抖字段允许忽略或白名单过滤

---

## 5. 推荐执行循环

建议 runner 主循环：

```text
1. load fixture
2. bootstrap session/theme/task seed
3. for each step:
   a. advance logical clock
   b. adapter -> 1..n domain events
   c. append events with seq_no
   d. state_driver apply transition
   e. append transition / side-effect events
   f. run reducers
4. build projections
5. compare with goldens
6. print summary
```

注意：
- 一个 fixture step 允许展开成多个 event
- 一个 event 也可能触发多个 reducer 更新
- 但 seq_no 必须全局单调递增

---

## 6. 建议文件结构

```text
software-e2e/
  fixtures/
  goldens/
    live/
    timeline/
    report/
    home/
  src/
    fixture-loader.ts
    runner-clock.ts
    event-adapter.ts
    state-driver.ts
    reducers/
      session-reducer.ts
      task-reducer.ts
      report-reducer.ts
    projections/
      build-live.ts
      build-timeline.ts
      build-report.ts
      build-home.ts
    assert/
      golden-assert.ts
    run-fixture.ts
```

如果仓库语言不是 TS，这个结构名也可以平移，但模块边界别乱。

---

## 7. 关键实现口径

### 7.1 seq_no

- 以 session 为粒度递增
- 每写入一个 event 就 +1
- bootstrap 事件也算 seq

### 7.2 occurred_at

- 来自逻辑时钟
- 不取真实系统时间
- 同一 step 展开的多个 event，可以在同一时刻，但 seq_no 不同

### 7.3 correlation_id

- 同一 fixture step 展开的 event 共享同一个 correlation_id
- 方便串回一条业务链

### 7.4 payload_public / payload_private

- projection 只允许读安全可读层
- runner 必须主动测这个边界，不能偷懒直接把 private 往外吐

### 7.5 report generation

- 正常结束 -> `published`
- 中断 / safety stop -> `partial`
- 无法生成完整报告时，也必须有降级结果，不允许直接没 report

---

## 8. 首轮实现优先级

### P1
- fixture_loader
- runner_clock
- event_adapter
- sessionReducer
- taskReducer
- build-live
- build-timeline
- run-fixture

### P2
- reportReducer
- build-report
- build-home
- golden-assert

### P3
- batch run
- fixture diff 报表
- debug CLI 输出美化

原因：
- 先让主链跑起来
- 再补报告和首页
- 最后再管批量体验

---

## 9. 风险与建议

### 风险 1：把 runner 写成半套业务系统

这很容易失控。mock runner 应该只验证链路，不该变成另一套正式后端。

**建议**：
- 只保留跑 fixture 必需能力
- 别先做复杂插件系统

### 风险 2：state_driver 和 reducer 职责打架

如果状态迁移和实体更新搅一起，后面肯定改崩。

**建议**：
- state_driver 只管判迁移和 side effects
- reducer 只管把 event 归并成当前态

### 风险 3：projection builder 偷读 private 字段

这会把家长端安全边界搞烂。

**建议**：
- 测试里加一条硬规则：projection 不允许依赖 `payload_private`

---

## 10. 当前结论

这轮可以直接按这个架构开干。  
下一手最合理的是：**Codex 按这个底稿去落 runner 骨架**，别再继续空转文档。