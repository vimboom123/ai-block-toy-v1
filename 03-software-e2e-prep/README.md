# Software E2E Prep Fixtures

The canonical runnable phase-3 path is now the TypeScript package at [`../software-e2e`](../software-e2e).

This folder now holds:
- the materialized prep fixtures that the canonical runner still consumes
- the archived Python prototype kept only as historical reference

The runner you should actually use going forward is the existing `software-e2e` chain.

## Run via the canonical TypeScript runner

Single prep fixture:

```bash
cd ../software-e2e
npm run run:fixture -- ../03-software-e2e-prep/fixtures/fx_parent_takeover_resume.yaml
```

Whole prep fixture set:

```bash
cd ../software-e2e
npm run check:phase3
```

Whole built-in + prep sweep:

```bash
cd ../software-e2e
npm run check:all-fixtures
```

Explicit directory batch run:

```bash
cd ../software-e2e
npm run run:fixture -- --all --fixtures-dir ../03-software-e2e-prep/fixtures
```

## Notes

- the prep fixture YAMLs are the schema-compat target for phase 3
- `run_e2e.py`, `software_e2e/`, and `tests/` under this folder are archived phase-3 prototype material, not the maintained runnable chain
- no network, no ASR/TTS/LLM, no real device IO
