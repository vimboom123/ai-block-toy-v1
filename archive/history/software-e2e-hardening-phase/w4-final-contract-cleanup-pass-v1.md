# W4 Final Contract Cleanup Pass v1

项目：AI积木玩具  
阶段：Phase 4 / 最终 reviewer pass 前收口  
日期：2026-03-17  
范围：`software-e2e/`、built-in fixtures、phase4 hardening docs

## 1. 本轮已修完的问题

1. canonical child input 命名收口：
   - 维护中的 built-in fixtures 已从 `child.intent_recognized` / `child.answer_incorrect` 切到 canonical `nlu.interpreted`
   - 旧别名只保留给 `03-software-e2e-prep/fixtures/` 兼容
   - fixture assert 新增 `events.must_not_contain`，避免维护链再偷偷回流 legacy 名字

2. `parent_report.generated` payload 合同补强：
   - fixture assert 新增 event-level contract 检查
   - 已显式校验 `producer`
   - 已显式校验 `report_id / report_version / summary_version / publish_status / source_event_range` 这组关键 key
   - report reducer 现会真正吃 `payload.report_id`，不再无脑沿用本地 fallback id

3. `confidence_overall` 收口：
   - report 最终合同改成 non-null
   - reducer 现在会优先吃 event 自带 `confidence_level`，不再只靠 `confidence_score` 推断
   - published/partial 最终 report DTO 不再出现 `confidence_overall=null`

4. report 文案终态修正：
   - `parent_summary` / `follow_up_suggestion` 改为在 `parent_report.generated` 时按最终 session/task 聚合结果收口
   - 修掉多任务 happy path 里“完成了 2 个任务，但总结还写 1 个任务”的陈旧文案问题
   - 修掉 follow-up 还停留在旧 task 的问题

5. `self_report_confirm` 覆盖补强：
   - fixture 现在同时证明进入 `awaiting_child_confirmation=true`
   - 也证明离开确认态后会回到 `false`

6. timeline Rule B / Rule C 真正补齐：
   - 新增 `fx_timeline_parent_takeover_hold.yaml`
   - 新增 `fx_timeline_pause_resume_debounce.yaml`
   - runner 现在会在 `parent_takeover` 后抑制后续普通 hint，直到明确 `parent.resume_requested`
   - quick pause/resume 仍按 15 秒去抖，不额外冒出 `paused_for_parent` / `session_resumed`

7. `parent.interrupt_requested` 直达路径补齐：
   - `session.help_level_peak` 现在会被正确抬到 `parent_takeover`
   - 不再出现 task 层是 `parent_takeover`，session/report 层却还停在低一级的断层

8. timeline spec 邻接口径补齐：
   - `session_timeline_view` 现在按 spec 做最近 100 条裁剪
   - 超过 100 条时会保留起始 `session_started`，不再无上限返回全量

## 2. 剩余 true blockers

- 无。  
  就当前 phase4 指定的 contract/blocker surface 来看，已经没有还会卡最终 reviewer pass 的 must-fix 未收口项。

## 3. 剩余 non-blocking risks

1. `03-software-e2e-prep/fixtures/` 仍保留 legacy 输入别名与 `public_stage=active` 兼容素材。
   - 这不再影响 canonical runnable path，但 reviewer 读历史素材时还是会看到旧口径残影。

2. report 文本/亮点仍是 deterministic template 级实现。
   - `achievement_tags / notable_moments / parent_summary / follow_up_suggestion` 已不再错合同，但还没做到治理文档里更完整的 worker 聚合质量。

3. canonical child-input 全链还没在 runnable fixtures 里把 `device.signal_received / child.audio_captured / asr.transcribed` 跑成显式样例。
   - 当前维护链的 canonical 命名已统一到 `nlu.interpreted` 入口，但更上游的输入链覆盖仍偏薄。

4. timeline 100-item clipping 已落代码，但当前 fixture bank 还没有专门的 `>100 items` 压力样例。
   - 这属于覆盖密度风险，不是当前合同 blocker。

## 4. 验证

已执行：

```bash
cd studio/projects/ai-block-toy-v1/software-e2e
npm run check:all-fixtures
```

结果：
- built-in fixtures：9 / 9 PASS
- phase3 fixtures：11 / 11 PASS
- 总计：20 / 20 PASS
