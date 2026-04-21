import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


def test_normalize_text_collapses_whitespace_case_and_punctuation():
    _add_benchmark_package_to_path()

    from src.normalization import normalize_text

    assert normalize_text('  "Bob   Smith,"  ') == "bob smith"


def test_evaluate_text_expectations_passes_when_expected_is_present():
    _add_benchmark_package_to_path()

    from src.assertions import evaluate_text_expectations

    failures = evaluate_text_expectations(
        "The current owner is Bob.",
        expected_any=["Bob"],
        forbidden_any=["Alice"],
    )

    assert failures == []


def test_evaluate_text_expectations_reports_forbidden_fact_leak():
    _add_benchmark_package_to_path()

    from src.assertions import evaluate_text_expectations

    failures = evaluate_text_expectations(
        "The current owner is Alice.",
        expected_any=["Bob"],
        forbidden_any=["Alice"],
    )

    assert any("forbidden_any" in failure for failure in failures)


def test_forbidden_any_ignores_historical_or_superseded_mentions():
    _add_benchmark_package_to_path()

    from src.assertions import inspect_text_expectations

    failures, matched_expected, matched_forbidden = inspect_text_expectations(
        "Previously got ImportError, now the final result is 21 passed.",
        expected_any=["21 passed"],
        forbidden_any=["ImportError"],
    )

    assert failures == []
    assert matched_expected is True
    assert matched_forbidden is False


def test_forbidden_any_uses_word_boundaries_for_single_word_terms():
    _add_benchmark_package_to_path()

    from src.assertions import inspect_text_expectations

    failures, matched_expected, matched_forbidden = inspect_text_expectations(
        "The step is unblocked now.",
        expected_any=["unblocked"],
        forbidden_any=["blocked"],
    )

    assert failures == []
    assert matched_expected is True
    assert matched_forbidden is False


def test_historical_only_assertion_rejects_stale_current_value():
    _add_benchmark_package_to_path()

    from src.assertions import evaluate_artifact_assertions
    from src.models import ArtifactAssertions

    failures = evaluate_artifact_assertions(
        wm_text="# Working Memory\n\n## Key Facts & Decisions\n- Current owner: Alice\n",
        artifact_assertions=ArtifactAssertions(
            wm_may_contain_only_as_historical=["Alice"],
        ),
    )

    assert any("historical" in failure for failure in failures)


def test_historical_only_does_not_treat_golden_as_old_marker():
    _add_benchmark_package_to_path()

    from src.assertions import evaluate_artifact_assertions
    from src.models import ArtifactAssertions

    failures = evaluate_artifact_assertions(
        wm_text="# Working Memory\n\n## Key Facts & Decisions\n- Golden dataset owner: Alice\n",
        artifact_assertions=ArtifactAssertions(
            wm_may_contain_only_as_historical=["Alice"],
        ),
    )

    assert any("historical" in failure for failure in failures)


def test_section_assertions_check_expected_and_forbidden_section_content():
    _add_benchmark_package_to_path()

    from src.assertions import evaluate_artifact_assertions
    from src.models import ArtifactAssertions

    failures = evaluate_artifact_assertions(
        wm_text=(
            "# Working Memory\n\n"
            "## Key Facts & Decisions\n"
            "- Current owner: Bob\n\n"
            "## Errors & Corrections\n"
            "- Old owner: Alice\n"
        ),
        artifact_assertions=ArtifactAssertions(
            section_contains={"Key Facts & Decisions": ["Carol"]},
            section_not_contains={"Errors & Corrections": ["Alice"]},
        ),
    )

    assert any("section_contains" in failure for failure in failures)
    assert any("section_not_contains" in failure for failure in failures)
