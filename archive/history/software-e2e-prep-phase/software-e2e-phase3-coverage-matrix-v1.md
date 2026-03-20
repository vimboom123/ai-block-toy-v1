# Software E2E Phase 3 Coverage Matrix v1

## 已支持事件 -> runner / reducer / projection / fixture

| event type | adapter | state | reducer | projection/assert | fixture |
|---|---|---|---|---|---|
| `session.started` | yes | yes | yes | live/timeline/report/home | happy, hint, parent, timeout, safety |
| `task.activated` | yes | yes | yes | live/timeline/report | happy, hint, parent, timeout, safety |
| `task.failed` | yes | yes | yes | live/timeline/report | parent resume/terminate |
| `help.level_changed` | yes | yes | yes | live/timeline/report | hint, parent resume/terminate, timeout |
| `parent.interrupt_requested` | yes | yes | yes | live/timeline | parent resume/terminate |
| `parent.resume_requested` | yes | yes | yes | live/timeline | parent resume |
| `parent.end_session_requested` | yes | yes | partial | timeline/live/home/report partial path | parent terminate |
| `child.no_response_timeout` | yes | yes | partial | timeline/report/assert | timeout |
| `task.completed` | yes | yes | yes | live/timeline/report/home | happy, hint, parent resume, timeout |
| `safety.checked` | yes | yes | partial | timeline/live/report/home | safety |
| `session.ended` | yes | yes | yes | live/timeline/report/home | all terminal fixtures |
| `parent_report.generated` | yes | no-op | yes | report/home | all terminal fixtures |

## 当前还没真正覆盖的风险

1. `safety.checked != stop`
2. timeout 连续升级链（多次 timeout -> guided_hint/demo/abort）
3. parent takeover 后再次失败 / 再次接管
4. 同 task id reopen / retry attempt 精确口径
5. timeline 合并规则（Rule A / Rule B）更完整实现
6. severity / alert / continue_entry 在更多结束原因下的组合
7. 主实现真实事件命名与 e2e runner 命名逐项核对

## 最小下一批 fixture

1. `fx_timeout_escalation_abort`
2. `fx_safety_warn_continue`
3. `fx_parent_takeover_reenter`
4. `fx_network_error_partial_report`
5. `fx_system_abort_partial_report`
