# Phase 7 - Natural Language Understanding / Signal Resolution Design v1

## 一句话
不是让系统“真正理解世界”，而是让它在**当前 task 上下文里**，判断孩子这句话更像：
- keep_trying
- task_completed
- end_session

## 输入
- child_input_text
- current_task
- scene context
- session snapshot
- allowed_signals

## 输出
- task_signal
- confidence
- reason
- normalized_child_text
- fallback_needed

## 设计原则
1. 只做**任务内理解**，不做无限开放聊天理解
2. 先看当前 task 目标，再看孩子这句话是不是完成了目标
3. 判不准时宁可 `keep_trying`，不要乱推进
4. 第一版走“规则 + LLM 二段式”

## 第一版流程
### Step 1: 规范化
把孩子原话做轻量清洗：
- 去口头噪音词
- 统一同义表达
- 保留核心动作词 / 对象词

### Step 2: 规则层
先跑简单规则：
- 明确退出词 -> end_session
- 明确完成表达且命中当前 task 目标词 -> task_completed
- 明确跑偏 / 含糊 / 只有情绪词 -> keep_trying

### Step 3: LLM 判定层
规则层不够确定时，给模型最小上下文：
- 当前 task 是什么
- 目标完成条件是什么
- 孩子说了什么
- 可选 signal 只有 3 个
要求模型只返回结构化 JSON：
- task_signal
- confidence
- reason

### Step 4: 安全兜底
以下情况直接降级为 `keep_trying`：
- 模型输出非法
- 置信过低
- 与规则层强冲突
- 明显缺少 task 所需关键信息

## 为什么这样做
因为这个项目不需要先解决“通用自然语言理解”，
它只需要解决：
**在当前 task 里，这句话算不算完成、继续、还是结束。**

这会比做一个大而空的 NLU 稳很多。

## 例子
### 当前 task
“让孩子说出消防车要去做什么”

### 孩子输入
“我要开消防车去救火”

### resolver 输出
- task_signal: task_completed
- confidence: 0.88
- reason: 已明确说出消防车行动目标，符合当前 task 完成条件

### 孩子输入
“消防车好快啊”

### resolver 输出
- task_signal: keep_trying
- confidence: 0.73
- reason: 表达了兴趣，但没回答当前 task 目标

### 孩子输入
“我不想玩了”

### resolver 输出
- task_signal: end_session
- confidence: 0.96
- reason: 明确退出意图

## 实现优先级
1. 先做文本输入版 resolver
2. 跑通 phase6 submit_turn
3. 再把 ASR transcript 接进来
4. 最后再升级全双工/打断/硬件
