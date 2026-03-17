# Software E2E Hardening Plan v1

项目：AI积木玩具  
阶段：Phase 4 / 软件 E2E 加固与主实现对齐  
日期：2026-03-17  
状态：W1-W4 已完成，进入最终 reviewer pass 准备

## 1. 当前阶段目标

第三阶段已经把 mock fixture → runner → reducer → projection → assert 这条最小闭环打通。
下一阶段的目标不再是“证明它能跑”，而是：

1. 把 `software-e2e/` 正式收成项目内唯一可执行 E2E 主链
2. 清理 phase 3 期间遗留的双轨口径（prep 文档 vs software-e2e 实际实现）
3. 增加更可维护的批量命令、fixture 分组和结果汇总
4. 做一次与主实现/真实 DTO/真实事件命名的逐项核对
5. 把当前 inline expected 断言逐步升级为可维护的 golden / snapshot 策略（如需要）

## 1.1 当前进展（2026-03-17 hardening pass）

已完成：
- 项目根 README、`software-e2e/README.md`、`03-software-e2e-prep/README.md` 已统一到同一套运行口径
- `software-e2e/` 已被明确成项目内唯一 runnable path
- 历史 Python 入口 `03-software-e2e-prep/run_e2e.py` 已降级为显式 redirect shim
- 批量脚本已具备 `check:built-in-fixtures`、`check:phase3`、`check:all-fixtures`
- W3/W4 契约清理已补齐：
  - 维护中的 built-in fixture 已切到 canonical `nlu.interpreted`
  - `parent_report.generated` payload 合同已补充 fixture 级断言
  - `confidence_overall` 已收成 non-null 最终字段，不再允许最终 report DTO 漏空
  - `self_report_confirm` fixture 已补足进入/退出 checkpoint
  - timeline 的 parent-takeover hold 与 pause/resume debounce 已补成显式 fixture
  - `parent.interrupt_requested` 直达路径也会计入 `session.help_level_peak=parent_takeover`

仍保留但不再作为主链：
- `03-software-e2e-prep/software_e2e/` 与 `03-software-e2e-prep/tests/` 作为 phase 3 历史原型留档继续存在

## 2. 当前主要矛盾

现在最大的矛盾不是“能不能跑”，而是：
- 这条链能不能稳定成为后续阶段唯一基线
- 以后别的阶段接进来时，是否还会重复撞依赖 / schema 漂移 / runner 入口分叉这类问题

所以这一阶段更像“加固 + 统一口径”，而不是重写。

## 3. 建议工作包

### W1. Canonical path 收口
- 把 `software-e2e/README.md`、项目根 README、prep README 统一成同一套运行说明
- 删除或标记废弃的 Python 跑法描述
- 明确从现在开始：`software-e2e/` 是唯一可执行 E2E 基线
当前状态：已完成。

### W2. Batch 命令与汇总
- 增加 `check:phase3` / `check:built-in-fixtures` / `check:all-fixtures` 等脚本
- 增加按 fixture 分类聚合的 summary 输出
- 让失败结果能直接定位 fixture / projection / path
当前状态：基础脚本已完成；更细的聚合 summary 仍可后续补。

### W3. 契约核对
- 对照 01/02 文档，逐项核查：
  - event type 命名
  - DTO 字段名
  - display_status 口径
  - report.publish_status 口径
  - retry_count / help_level_peak 等核心字段
当前状态：已完成；详见 `w3-contract-alignment-pass-v1.md` 与 `w4-final-contract-cleanup-pass-v1.md`。

### W4. Golden 策略升级
- 评估是否要把当前 inline expected 的部分断言迁到独立 golden 文件
- 若迁移，需保证：
  - 不引入大规模噪音 diff
  - 不让维护成本高于收益
当前状态：本轮未外置 snapshot，但已把事件 payload / timeline edge / checkpoint 断言加进 fixture golden。

### W5. 扩展覆盖
- 只在有明确产品/后端新约束时新增 fixture
- 不为了“看起来更全”而无脑堆样本

## 4. 交付标准

本阶段完成至少应满足：
- `software-e2e/` 成为唯一明示主链
- 文档口径不再互相打架
- 批量运行命令稳定
- 对齐核查结果形成文档或 checklist
- 新人接手时，不需要再猜“到底该跑哪套链”

## 5. 当前建议

下一步默认动作：
1. 让最终 reviewer 直接按 `software-e2e/` + `04-software-e2e-hardening/w4-final-contract-cleanup-pass-v1.md` 过一遍
2. 若 reviewer 无新增 must-fix，再决定是否值得做 golden 外置
3. 只有出现新的后端 / projection 合同，才继续扩 fixture
