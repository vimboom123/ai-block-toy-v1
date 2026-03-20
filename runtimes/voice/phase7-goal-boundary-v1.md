# AI Block Toy - Phase 7 Goal & Boundary v1

项目：AI Block Toy v1  
阶段：Phase 7 / 语音交互层 + 自然输入理解层 + 硬件预留层  
日期：2026-03-18

## 1. Phase 7 一句话定义

Phase 7 不是“加个语音 demo”，也不是另起一套新 runtime。

Phase 7 的正确定位是：

**在 Phase 6 已有 session runtime 之上，补上一层可接真实语音输入、可自动理解自然输入、可把结果映射回 session / turn / task、并为后续硬件接入保留稳定边界的 voice runtime。**

---

## 2. 为什么现在必须做 Phase 7

前六阶段已经证明：
- 产品规格、状态机、projection 基线已存在
- 软件 E2E 主链已跑通
- 真实文本对话 runtime 已接通
- session / turn / task 的最小闭环已成立

但当前还没完成的是：
- 用户自然输入理解
- task_signal 自动判定
- 真实语音输入/输出链路
- 面向硬件接入的音频与控制接口

所以 Phase 7 的任务不是重复前六阶段，而是把“工程态演示”往“自然交互演示”推进一层。

---

## 3. Phase 7 必须完成的目标

### 3.1 真实语音输入接入
系统需要能接收真实音频输入，而不只是 submit text。

最小要求：
- 支持麦克风输入
- 支持语音转文本（ASR）
- 支持把语音输入映射成 turn input
- 保留原始 transcript / final transcript / 置信或状态信息

### 3.2 自然输入理解层
系统不能再主要依赖人工显式给 `task_signal`。

最小要求：
- 对 child input 做自然语言理解
- 自动判断当前输入更接近：
  - keep_trying
  - task_completed
  - end_session
  - 其他系统态信号
- 形成可解释的 signal resolution 结果，而不是黑盒推进

### 3.3 Session bridge
语音层不能绕开 Phase 6，而必须接回现有 session runtime。

最小要求：
- voice input → turn request
- NLU result → signal resolution
- resolved input → submit_turn / session update
- assistant text reply → TTS output

### 3.4 语音输出层
系统需要把已有文本回复变成可播报输出。

最小要求：
- TTS 合成
- 音频播放
- 播报状态可观测
- 为后续“可打断、可中止、可恢复”预留状态位

### 3.5 硬件预留边界
Phase 7 先不深做板级集成，但必须留接口。

最小要求：
- 音频输入适配器接口
- 音频输出适配器接口
- 外部控制事件接口（唤醒/停止/静音/中断）
- 设备状态上报接口（在线、忙碌、错误、音量等）

---

## 4. Phase 7 明确不做什么

### 4.1 不重做产品状态机
Phase 1-6 已有 session / task / turn 主链，Phase 7 不应推翻它。

### 4.2 不先深做完整硬件驱动
当前阶段重点是语音 runtime 与 runtime bridge，不是板卡驱动、麦阵优化、底层固件。

### 4.3 不把 phase7 写成一次性 demo 脚本
不能只做：
- 本地 while loop 录音
- 阻塞式 TTS 播放
- 语音逻辑 / session 逻辑 / 硬件逻辑 混成一坨

### 4.4 不继续依赖人工 signal 作为主路径
可以保留 debug / fallback，但不能继续把“人工指定 task_signal”当正式演示主入口。

---

## 5. 推荐模块边界

### A. voice-runtime
负责：
- ASR
- transcript lifecycle
- TTS
- playback state
- interruption hooks

### B. input-understanding
负责：
- child utterance normalization
- task_signal auto resolution
- 输入语义解释
- fallback / uncertain handling

### C. phase6-bridge
负责：
- 把 transcript + resolved signal 提交给 Phase 6 session runtime
- 接收 session snapshot / assistant reply
- 保持 turn/task/session 口径一致

### D. device-adapter
负责：
- 麦克风输入设备
- 扬声器输出设备
- 后续硬件音频设备替换

### E. hardware-control-surface
负责：
- wake
- interrupt
- mute
- stop playback
- status report

---

## 6. 推荐数据流

```text
mic / hardware input
  -> ASR partial/final transcript
  -> input understanding / task_signal auto resolution
  -> Phase 6 submit_turn
  -> session runtime returns assistant reply + session snapshot
  -> TTS synth
  -> speaker / hardware output
  -> playback / interrupt / status feedback
```

---

## 7. 最小完成标准（MVP）

Phase 7 至少达到：

1. 用户可以真实说一句话
2. 系统能转成文本
3. 系统能自动判断当前 task 是否完成 / 继续 / 结束
4. 结果能通过现有 Phase 6 session runtime 推进
5. assistant 回复能真实播出来
6. 整条链路不依赖人工手填 `task_signal` 才能走通
7. 音频 I/O 入口不是写死脚本，而是有 adapter 边界

---

## 8. Phase 7 完成后，才有资格往后接硬件层

只有当下面这些成立，后续硬件接入才不是空接：
- voice turn 已能稳定映射到 session turn
- 自动 signal 判断已能承担主路径
- 语音输出已能对应 session reply
- 中断 / 状态 / 错误面已具备基础接口

否则硬件接入只会把当前的软件工程态问题放大。

---

## 9. PM 判断

Phase 7 的本质不是“声音功能”，而是：

**把 AI Block Toy 从“文本驱动的会话演示系统”推进成“能接真实自然输入、并且未来能挂到硬件上的交互 runtime”。**

如果这个阶段做歪，后面接硬件只会接上一层脆弱的 demo 壳。
如果这个阶段做对，后续硬件层就只是换 input/output adapter，而不是重做核心交互主链。
