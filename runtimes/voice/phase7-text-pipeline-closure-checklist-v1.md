# Phase 7 Text Pipeline Closure Checklist v1

项目：AI Block Toy v1  
阶段：Phase 7A / 文本链收口  
日期：2026-03-18

## 目标
先把文本链收成一个本机可验收模块，再进入语音链挂接。

当前主链：
`child text -> signal_resolution -> interaction_generation -> interaction_context -> phase6_turn_payload`

---

## 一、必须补（P0）

### A. 正常输入主路径
- [ ] 兴趣型跑题：如“消防车真帅”
- [ ] 半完成输入：如“去帮忙”
- [ ] 明确完成输入：如“去灭火”
- [ ] 明确退出输入：如“我不想玩了”
- [ ] 卡住/不知道：如“不知道”

### B. provider 异常路径
- [ ] qwen timeout -> retry -> fallback
- [ ] qwen key missing -> immediate fallback
- [ ] provider bad json -> fallback
- [ ] provider missing `reply_text` -> fallback
- [ ] provider mechanical reply -> fallback
- [ ] retry 仍失败 -> fallback but no raise

### C. phase6 bridge 异常
- [ ] session 不存在
- [ ] submit_turn 返回 4xx/5xx
- [ ] bridge client timeout / connection failure
- [ ] bridge 错误时文本链仍返回本轮 signal/interaction，不整条炸掉

---

## 二、应该补（P1）

### D. 语义细分
- [ ] partial / partial-credit 语义：`去帮忙` 不应和纯跑题完全同口径
- [ ] acknowledgment 不要复读抽取结果
- [ ] task_completed 收尾文案更产品化，不要“下一步再看看”这种虚句
- [ ] end_session 文案保持安抚，不带多余任务推进

### E. provider 对比与切换
- [ ] qwen / minimax / ark_doubao / template 四路 provider 可切换 smoke
- [ ] 默认 provider=qwen 的 smoke 覆盖
- [ ] provider_name / generation_source / fallback_reason 输出稳定

---

## 三、可后补（P2）

### F. 多轮文本 session
- [ ] 连续两轮/三轮输入时 recent context 是否更顺
- [ ] phase6 真 session 下的文本连续互动 smoke
- [ ] 多 task 场景下 interaction_context 是否仍够轻

---

## 四、结束标准
当下面同时成立，文本链才算收口：

1. P0 全补齐
2. 关键 P1（partial-credit + 默认 qwen + provider 切换 smoke）补齐
3. 本机回归通过
4. 手工 smoke 至少跑一轮典型样本集
5. 当前已知错误路径都有明确 fallback 行为，不再靠人工盯守

到这一步，才进入 Phase 7B：语音链挂接。
