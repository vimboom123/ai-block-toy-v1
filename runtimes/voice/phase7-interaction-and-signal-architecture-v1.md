# Phase 7 - Interaction + Signal Architecture v1

项目：AI Block Toy v1  
阶段：Phase 7 / 自然输入理解 + 互动生成  
日期：2026-03-18

## 1. 关键修正

Phase 7 不能只做 `task_signal` 自动判定。

如果系统只会把孩子的话分类成：
- keep_trying
- task_completed
- end_session

那它只是一个推进器，不是一个有互动感的 AI 积木。

所以必须拆成两条并行但耦合的链：

1. **Signal Resolver（内部控制链）**
2. **Interaction Generator（对孩子的互动链）**

---

## 2. 双通道架构

### A. Signal Resolver
给 runtime 用。

职责：
- 判断当前输入对 task 推进意味着什么
- 输出稳定、保守、可解释的推进信号

输出重点：
- `task_signal`
- `confidence`
- `reason`
- `fallback_needed`

### B. Interaction Generator
给孩子用。

职责：
- 接住孩子原话
- 维持陪伴感、趣味感、鼓励感
- 在不生硬的前提下把孩子拉回当前任务

输出重点：
- `reply_text`
- `interaction_mode`
- `emotion_tone`
- `redirect_strength`

---

## 3. 为什么必须拆开

因为“系统怎么推进”与“机器人怎么说话”不是同一个问题。

例如：

孩子说：`消防车真帅`

### 内部控制判断
- 没完成当前任务目标
- 所以 `task_signal = keep_trying`

### 外部互动回复
- 不该冷冰冰地只说“请继续回答问题”
- 应该先接兴趣点，再轻柔回引

例如：
- “对呀，消防车真的很帅。那这辆这么厉害的消防车，现在是不是要赶去帮忙呀？你觉得它要去做什么呢？”

所以：
- **内部信号可以保守**
- **外部互动必须自然**

---

## 4. 推荐处理流程

```text
child speech/text
  -> normalize text
  -> signal resolver
  -> interaction generator
  -> phase6 bridge submit_turn
  -> assistant reply package
  -> TTS/output
```

更细一点：

```text
孩子原话
  -> 文本规范化
  -> 判断 task_signal
  -> 判断这句话的互动价值（兴趣点/情绪点/偏题程度）
  -> 生成自然回复
  -> 把 signal 提交给 phase6
  -> 将回复文本作为本轮 assistant output
```

---

## 5. Signal Resolver 设计

### 输入
- `child_input_text`
- `current_task`
- `task_completion_criteria`
- `scene_context`
- `session_snapshot`
- `allowed_signals`

### 输出
```json
{
  "task_signal": "keep_trying",
  "confidence": 0.73,
  "reason": "孩子表达了兴趣，但没有回答当前任务目标",
  "fallback_needed": false,
  "normalized_child_text": "消防车真帅"
}
```

### 原则
1. 只做当前 task 范围内判断
2. 判不准时宁可保守
3. 不直接负责说话风格
4. 输出必须结构化、稳定、可测试

---

## 6. Interaction Generator 设计

### 输入
- `child_input_text`
- `normalized_child_text`
- `current_task`
- `task_signal`
- `signal_reason`
- `scene_style`
- `child_engagement_state`

### 输出
```json
{
  "reply_text": "对呀，消防车真的很帅。那这么帅的消防车，现在是不是要赶去帮忙呀？你觉得它要去做什么呢？",
  "interaction_mode": "warm_redirect",
  "emotion_tone": "playful",
  "redirect_strength": "soft"
}
```

### interaction_mode 建议枚举
- `acknowledge_and_redirect`
- `warm_redirect`
- `celebrate_completion`
- `gentle_retry`
- `graceful_end`
- `emotional_soothing`

### 原则
1. 先接住孩子，再拉回任务
2. 不直接暴露工程态词汇（如 task_signal）
3. 不要像考试系统
4. 回引强度可调，避免每次都像审问

---

## 7. phase6 bridge 怎么接

当前 phase6 接口还是：
```json
{
  "child_input_text": "...",
  "task_signal": "..."
}
```

所以 phase7 第一版不强改 phase6，而是内部保留 richer package：

```json
{
  "child_input_text": "消防车真帅",
  "signal_resolution": {
    "task_signal": "keep_trying",
    "confidence": 0.73,
    "reason": "孩子表达兴趣但没完成任务"
  },
  "interaction_generation": {
    "reply_text": "对呀，消防车真的很帅。那它现在要去做什么呢？",
    "interaction_mode": "warm_redirect"
  }
}
```

再桥接成对 phase6 的提交：

```json
{
  "child_input_text": "消防车真帅",
  "task_signal": "keep_trying"
}
```

也就是说：
- **phase6 继续管状态推进**
- **phase7 负责理解 + 互动包装**

---

## 8. 最小例子

### 情况 A：偏兴趣，但未完成任务
孩子：`消防车真帅`

内部：
- `task_signal = keep_trying`

外部：
- `reply_text = 对呀，消防车真的很帅。那这辆消防车现在要去做什么呀？`

### 情况 B：明确完成任务
孩子：`我要开消防车去救火`

内部：
- `task_signal = task_completed`

外部：
- `reply_text = 对，就是去救火。你说对啦，我们快让消防车出发！`

### 情况 C：退出
孩子：`我不想玩了`

内部：
- `task_signal = end_session`

外部：
- `reply_text = 好呀，那我们先休息一下。你想再玩的时候我还在。`

---

## 9. 对大模型真正有意义的部分

大模型在这个系统里最有价值的，不是单做 signal 分类器，而是：

1. 接住孩子的兴趣点
2. 识别情绪和参与状态
3. 生成自然的回引话术
4. 降低“系统像考试机”的感觉
5. 让互动保留陪伴感

所以：
- `Signal Resolver` 可以偏保守、可测
- `Interaction Generator` 应该承担更多“像人”的部分

---

## 10. 下一步实现顺序

### Step 1
先落 schema：
- `signal_resolution.schema.json`
- `interaction_generation.schema.json`

### Step 2
先做文本版原型：
- 输入一段 text
- 同时产出 signal + reply_text

### Step 3
接到 phase6 submit_turn
- signal 用于推进
- reply_text 用于 assistant 输出

### Step 4
再接 ASR / TTS
- transcript 只作为输入来源
- 核心逻辑仍是 signal + interaction 双通道

---

## 11. PM 判断

Phase 7 的关键不是“识别一句话后推进流程”，而是：

**既能让系统知道该不该推进，又能让孩子觉得自己被接住、被回应、被带着玩。**

如果只有前者，没有后者，AI 积木就会退化成一个会分类的流程机。
