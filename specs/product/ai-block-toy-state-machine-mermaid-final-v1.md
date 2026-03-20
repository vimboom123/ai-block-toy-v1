# AI积木玩具状态机 Mermaid 最终版 v1

这版目标只有 4 个：
- 不省略关键步骤
- 结构完整
- 尽量少交叉
- 适合直接贴进 Mermaid / draw.io Mermaid block

> 说明：为了压低交叉，这版采用 `LR` + `subgraph` 分区。
> 主链、帮助链、兜底链分开摆，不把所有箭头搅成一锅。

```mermaid
---
config:
  theme: base
  layout: dagre
---
flowchart LR

    %% =========================
    %% 主链
    %% =========================
    subgraph MAIN[主流程]
        direction LR
        A([开始]) --> B[待机\nidle]
        B -->|唤醒词 / 按键| C[会话初始化\nsession_bootstrap]
        C -->|theme.bound| D[角色开场\nwarming_up]
        D -->|开场播报完成| E[任务发布\ntask_dispatch]
        E -->|任务指令播报完成| F[等待孩子回应\nawait_answer]
        F -->|收到孩子输入| G[输入理解\ninterpret_input]
        G -->|命中成功且需口头确认| H[口头确认是否完成\nself_report_confirm]
        G -->|命中成功且无需确认| M[完成鼓励\ncelebrate_success]
        H -->|confirm_done| M
        M -->|鼓励播报完成| N[判断下一步\nnext_task_ready]
        N -->|还有下一任务| E
        N -->|没有下一任务| O[收尾总结\ncooling_down]
        O -->|session.status=ended\nsession.ended completed| P([正常结束\nended])
    end

    %% =========================
    %% 帮助链
    %% =========================
    subgraph HELP[帮助升级链]
        direction TB
        I[轻提示\ngive_hint]
        J[明确提示\nguided_hint]
        K[分步提示\nstep_by_step_help]
        L[示范\ndemo_mode]
    end

    %% =========================
    %% 兜底链
    %% =========================
    subgraph FALLBACK[兜底 / 横切]
        direction TB
        Q[跑题拉回\noff_topic_repair]
        R[重新唤回\nreengagement]
        S[家长接管暂停\nparent_interrupt_hold]
        T[安全停止\nsafety_hold]
        U[异常收尾\nabort_cleanup]
        V([提前结束\naborted])
    end

    %% =========================
    %% 输入理解 -> 帮助链
    %% =========================
    G -->|第一次低置信 / 错答| I
    G -->|第二次低置信 / 错答| J
    G -->|继续失败| K
    G -->|仍失败| L

    %% 口头确认失败也回帮助链
    H -->|not_done| J
    H -->|unsure| J
    H -->|确认超时| J

    %% 帮助链统一回等待
    I -->|提示播报完成| F
    J -->|提示播报完成| F
    K -->|子步骤播报完成| F
    L -->|示范播报完成| F

    %% =========================
    %% 跑题 / 沉默
    %% =========================
    G -->|intent=off_topic_chat| Q
    Q -->|拉回后继续当前任务| F

    F -->|沉默超时| R
    R -->|重新响应| F
    R -->|再次沉默| J

    %% =========================
    %% 家长接管
    %% =========================
    L -->|示范后仍失败 / 需要成人帮助| S
    D -->|parent.interrupt_requested| S
    E -->|parent.interrupt_requested| S
    F -->|parent.interrupt_requested| S
    H -->|parent.interrupt_requested| S

    S -->|resume_requested\nsession.status paused->active| F
    S -->|end_session_requested| U

    %% =========================
    %% 安全停止
    %% =========================
    D -->|safety.checked| T
    F -->|safety.checked| T
    G -->|safety.checked| T
    H -->|safety.checked| T
    T -->|end_reason=safety_stop| U

    %% =========================
    %% 异常收尾
    %% =========================
    U -->|system.cleanup_finished\nsession.status=aborted| V

    %% =========================
    %% 样式
    %% =========================
    classDef main fill:#e8f1ff,stroke:#4a76a8,stroke-width:1.5px,color:#111;
    classDef help fill:#fff3cd,stroke:#c99700,stroke-width:1.5px,color:#111;
    classDef fallback fill:#fdeaea,stroke:#c94f4f,stroke-width:1.5px,color:#111;
    classDef end fill:#e7f7e7,stroke:#5c9e5c,stroke-width:1.5px,color:#111;

    class B,C,D,E,F,G,H,M,N,O main;
    class I,J,K,L help;
    class Q,R,S,T,U fallback;
    class A,P,V end;
```

---

## 这版为什么比前面稳

### 1. 主链不拆烂
主链固定就是：
- 待机
- 初始化
- 开场
- 发布任务
- 等回应
- 理解输入
- 口头确认 / 完成鼓励
- 判断下一步
- 收尾
- 正常结束

### 2. 帮助链独立出来
不把 `give_hint / guided_hint / step_by_step_help / demo_mode` 硬塞回主链同一行。

### 3. 兜底链单独放一列
把：
- 跑题
- 沉默
- 家长接管
- 安全停止
- 异常收尾

都单独放到 `FALLBACK` 分区，减少主链交叉。

### 4. 保留了关键口径
这版没有偷懒省掉：
- `self_report_confirm`
- `parent_interrupt_hold`
- `safety_hold`
- `abort_cleanup`
- `session.status=ended / aborted`

---

## 如果 Mermaid 渲染后还是有交叉
那不是逻辑问题，是 Mermaid 自动布局的上限问题。

此时有两个办法：
1. 继续拆成两张图：
   - 主链图
   - 兜底图
2. 用 draw.io 手工排版，但内容还是用这版当源

---

## 当前建议

- **对外讲方案 / 给人看**：先用这版
- **真要做工程实现**：同时参考 `specs/product/ai-block-toy-state-machine-v1.md`
