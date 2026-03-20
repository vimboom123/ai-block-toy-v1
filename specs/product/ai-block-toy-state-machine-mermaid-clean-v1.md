# AI积木玩具状态机 Mermaid（清晰版 v1）

## 版本 1：适合放 diagrams / PPT 的主流程图

```mermaid
flowchart TD
    A([开始]) --> B[待机 idle]
    B -->|唤醒词 / 按键| C[会话初始化 session_bootstrap]
    C --> D[开场 warming_up]
    D --> E[任务发布 task_dispatch]
    E --> F[等待孩子回应 await_answer]
    F -->|收到输入| G[输入理解 interpret_input]

    G -->|像是完成了| H[口头确认 self_report_confirm]
    H -->|确认完成| I[完成鼓励 celebrate_success]
    H -->|还没完成 / 不确定| J[提示 help]

    G -->|直接判定完成| I
    G -->|没答对 / 听不清| J
    G -->|跑题| K[拉回主线 off_topic_repair]

    J --> F
    K --> F

    I --> L[判断还有没有下一步 next_task_ready]
    L -->|有下一任务| E
    L -->|没有了| M[收尾 cooling_down]
    M --> N([结束 ended])
```

---

## 版本 2：把兜底逻辑也带上，但还能看懂

```mermaid
flowchart TD
    A([开始]) --> B[待机 idle]
    B -->|唤醒词 / 按键| C[会话初始化 session_bootstrap]
    C --> D[开场 warming_up]
    D --> E[任务发布 task_dispatch]
    E --> F[等待孩子回应 await_answer]

    F -->|收到输入| G[输入理解 interpret_input]
    F -->|沉默超时| R[重新唤回 reengagement]
    R --> F

    G -->|像是完成了| H[口头确认 self_report_confirm]
    H -->|确认完成| I[完成鼓励 celebrate_success]
    H -->|还没完成 / 不确定| J1[轻提示 give_hint]

    G -->|直接判定完成| I
    G -->|第一次失败| J1
    G -->|继续失败| J2[明确提示 guided_hint]
    G -->|还是不会| J3[分步提示 step_by_step_help]
    G -->|再不行| J4[示范 demo_mode]
    G -->|跑题| K[拉回主线 off_topic_repair]

    J1 --> F
    J2 --> F
    J3 --> F
    J4 --> F
    K --> F

    J4 -->|示范后仍不行| P[家长接管暂停 parent_interrupt_hold]
    P -->|恢复| F
    P -->|结束| X[异常收尾 abort_cleanup]

    D -->|安全命中| S[safety_hold]
    F -->|安全命中| S
    G -->|安全命中| S
    H -->|安全命中| S
    S --> X

    I --> L[判断还有没有下一步 next_task_ready]
    L -->|有下一任务| E
    L -->|没有了| M[收尾 cooling_down]
    M --> N([正常结束 ended])
    X --> Z([提前结束 aborted])
```

---

## 你现在最该放进 draw.io 的版本

如果你是给自己或团队看，先放 **版本 1**。

原因：
- 一眼能看懂主链
- 不会被一堆异常分支搞乱
- 适合先讲产品逻辑

如果你是给工程继续细化，再放 **版本 2**。

---

## 图里几个词，建议中文展示别太工程化

可以这样改：

- `session_bootstrap` → 会话初始化
- `warming_up` → 角色开场
- `task_dispatch` → 发布任务
- `await_answer` → 等孩子回应
- `interpret_input` → 理解孩子输入
- `self_report_confirm` → 口头确认是否完成
- `celebrate_success` → 完成鼓励
- `next_task_ready` → 判断下一步
- `cooling_down` → 收尾总结
- `reengagement` → 重新唤回
- `parent_interrupt_hold` → 家长接管 / 暂停
- `abort_cleanup` → 提前结束 / 异常收尾

---

## 一句话结论

上一版问题不是逻辑错，是**把工程细节、状态口径、异常流、实现备注全他妈堆进一张图里了**，所以没人看得懂。

这一版先把图分层：
- 一张讲主流程
- 一张讲完整兜底

这才像正常人会看的状态机图。
