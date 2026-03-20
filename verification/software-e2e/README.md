# software-e2e

Canonical software E2E runner for the AI block toy project.
This is now the only runnable chain to use for future UI/backend work in this project tree.

Purpose:
- replay frozen YAML fixtures without real ASR, TTS, LLM, or device IO
- drive the documented chain: fixture -> event envelope -> state transition -> reducers -> projections -> golden assert
- run both the built-in package fixtures and the archived prep fixtures under `../../archive/history/software-e2e-prep-phase/fixtures`
- keep `../../archive/history/software-e2e-prep-phase/` as fixture source bank only, not as a separate runnable runner

Execution flow:
1. `fixture-loader.ts` loads YAML and normalizes either fixture contract generation.
2. `runner-clock.ts` advances logical time and stamps `occurred_at`.
3. `event-adapter.ts` expands each fixture step into standard event envelopes.
4. `state-driver.ts` emits explicit transition metadata.
5. `reducers/` materialize `session`, `task`, and `parent_report`.
6. `projections/` build live, timeline, report, and home DTOs.
7. `assert/golden-assert.ts` checks terminal fields plus projection assertions.

Runner rules:
- use logical time, not real sleeps
- keep `seq_no` monotonic within one session
- reuse one `correlation_id` for all events expanded from the same fixture step
- maintained built-in fixtures now use canonical `nlu.interpreted`; fixture aliases `child.intent_recognized` / `child.answer_incorrect` remain only as compatibility shims for the archived phase-3 bank
- timeline source whitelist follows the v1 projection spec: `child.no_response_timeout` and `safety.checked` still drive reducers/reporting, but do not surface as direct timeline source items
- built-in coverage now includes the tricky timeline branches: parent-takeover hint hold and pause/resume debounce
- fixture assertions now verify selected event contracts, including `parent_report.generated` payload keys and producer alignment on safety/report events
- projections must not leak `payload_private`
- do not use `../../archive/history/software-e2e-prep-phase/run_e2e.py` for normal execution anymore

Canonical commands:

Run a single built-in fixture:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run run:fixture -- ./fixtures/fx_happy_path_basic.yaml
```

Run a single prep fixture:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run run:fixture -- ../../archive/history/software-e2e-prep-phase/fixtures/fx_parent_takeover_resume.yaml
```

Run all built-in fixtures:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run check:built-in-fixtures
```

Run the archived prep fixture set:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run check:history-fixtures
```

Run every built-in + archived prep fixture in one sweep:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run check:all-fixtures
```

Explicit directory batch run:

```bash
cd studio/projects/ai-block-toy-v1/verification/software-e2e
npm run run:fixture -- --all --fixtures-dir ../../archive/history/software-e2e-prep-phase/fixtures
```

Notes:
- `software-e2e-prep-phase/run_e2e.py` is archived and now only redirects you back here.
