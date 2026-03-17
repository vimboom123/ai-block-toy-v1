from __future__ import annotations

from pathlib import Path

from software_e2e.core import assert_golden, iter_fixture_paths, run_fixture


PREP_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PREP_DIR / "fixtures"


def test_all_materialized_fixtures_pass() -> None:
    failures = []
    for fixture_path in iter_fixture_paths(FIXTURES_DIR):
        artifacts = run_fixture(fixture_path)
        assertion = assert_golden(artifacts)
        if not assertion.ok:
            detail = "\n".join(
                f"{failure.path}: expected={failure.expected!r} actual={failure.actual!r}"
                for failure in assertion.failures
            )
            failures.append(f"{fixture_path.name}\n{detail}")
    assert not failures, "\n\n".join(failures)


def test_all_fixtures_emit_transition_events() -> None:
    for fixture_path in iter_fixture_paths(FIXTURES_DIR):
        artifacts = run_fixture(fixture_path)
        event_types = [event.event_type for event in artifacts.events]
        assert "state.transition_applied" in event_types, fixture_path.name
