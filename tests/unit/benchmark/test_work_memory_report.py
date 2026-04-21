import csv
import json
import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


def test_write_result_bundle_creates_summary_csv_and_markdown(tmp_path):
    _add_benchmark_package_to_path()

    from src.models import CaseRunResult, CheckpointRunResult, CaseSpec
    from src.report import write_result_bundle

    case = CaseSpec(
        case_id="correction_current_owner",
        title="Correction current owner",
        mode="chat_e2e",
        capability="correction",
        priority="critical",
        expected_failure=False,
    )
    result = CaseRunResult(
        case_id="correction_current_owner",
        mode="chat_e2e",
        passed=False,
        checkpoint_results=[
            CheckpointRunResult(
                id="cp_after_turn",
                trigger="after_turn",
                passed=False,
                answer="Alice",
                failures=["forbidden phrase leaked: Alice"],
                matched_forbidden=True,
                wm_token_estimate=12,
            )
        ],
    )

    outputs = write_result_bundle(
        output_dir=tmp_path,
        cases=[case],
        case_results=[result],
    )

    summary_path = outputs["summary_json"]
    checkpoints_path = outputs["checkpoints_csv"]
    capability_scores_path = outputs["capability_scores_json"]
    growth_path = outputs["growth_csv"]
    report_path = outputs["report_md"]

    assert summary_path.exists()
    assert checkpoints_path.exists()
    assert capability_scores_path.exists()
    assert growth_path.exists()
    assert report_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total_cases"] == 1
    assert summary["passed_cases"] == 0
    assert summary["failed_cases"] == 1
    assert summary["capabilities"]["correction"]["failed"] == 1
    assert summary["forbidden_fact_leak_rate"] == 1.0
    assert summary["wm_growth_slope"] is None

    capability_scores = json.loads(capability_scores_path.read_text(encoding="utf-8"))
    assert capability_scores["correction"]["case_pass_rate"] == 0.0

    with checkpoints_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["case_id"] == "correction_current_owner"
    assert rows[0]["checkpoint_id"] == "cp_after_turn"
    assert rows[0]["passed"] == "false"
    assert rows[0]["wm_token_estimate"] == "12"

    with growth_path.open("r", encoding="utf-8", newline="") as handle:
        growth_rows = list(csv.DictReader(handle))
    assert len(growth_rows) == 1
    assert growth_rows[0]["case_id"] == "correction_current_owner"
    assert growth_rows[0]["wm_token_estimate"] == "12"

    report_text = report_path.read_text(encoding="utf-8")
    assert "Work Memory Benchmark Report" in report_text
    assert "correction_current_owner" in report_text
    assert "WM growth slope" in report_text


def test_build_summary_computes_growth_slope_only_from_growth_control_cases():
    _add_benchmark_package_to_path()

    from src.models import CaseRunResult, CaseSpec, CheckpointRunResult
    from src.report import build_summary

    growth_case = CaseSpec(
        case_id="growth_append_only_key_facts",
        title="Growth append only key facts",
        mode="artifact_whitebox",
        capability="growth_control",
        priority="normal",
    )
    correction_case = CaseSpec(
        case_id="correction_current_owner",
        title="Correction current owner",
        mode="chat_e2e",
        capability="correction",
        priority="critical",
    )
    growth_result = CaseRunResult(
        case_id="growth_append_only_key_facts",
        mode="artifact_whitebox",
        passed=True,
        checkpoint_results=[
            CheckpointRunResult(
                id="cp_after_turn",
                trigger="after_turn",
                passed=True,
                wm_token_estimate=10,
            ),
            CheckpointRunResult(
                id="cp_after_compact",
                trigger="after_compact",
                passed=True,
                wm_token_estimate=30,
            ),
        ],
    )
    correction_result = CaseRunResult(
        case_id="correction_current_owner",
        mode="chat_e2e",
        passed=True,
        checkpoint_results=[
            CheckpointRunResult(
                id="cp_after_turn",
                trigger="after_turn",
                passed=True,
                wm_token_estimate=999,
            )
        ],
    )

    summary = build_summary(
        [growth_case, correction_case],
        [growth_result, correction_result],
    )

    assert summary["wm_growth_slope"] == 20.0
