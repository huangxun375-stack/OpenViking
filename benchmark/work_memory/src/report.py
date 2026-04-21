from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .models import CaseRunResult, CaseSpec


def build_summary(cases: list[CaseSpec], case_results: list[CaseRunResult]) -> dict:
    cases_by_id = {case.case_id: case for case in cases}
    passed_cases = sum(1 for result in case_results if result.passed)
    total_cases = len(case_results)
    failed_cases = total_cases - passed_cases

    capability_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "failed": 0}
    )
    total_checkpoints = 0
    matched_expected_count = 0
    expected_checkpoint_count = 0
    forbidden_leaks = 0
    handoff_successes = 0
    handoff_total = 0
    tool_grounded_passes = 0
    tool_grounded_total = 0
    growth_slopes: list[float] = []

    for result in case_results:
        case = cases_by_id.get(result.case_id)
        capability = case.capability if case else "unknown"
        capability_counts[capability]["total"] += 1
        capability_counts[capability]["passed" if result.passed else "failed"] += 1

        if capability == "tool_grounded_memory":
            tool_grounded_total += 1
            if result.passed:
                tool_grounded_passes += 1

        if capability == "growth_control":
            growth_slope = _compute_case_growth_slope(result)
            if growth_slope is not None:
                growth_slopes.append(growth_slope)

        for checkpoint in result.checkpoint_results:
            total_checkpoints += 1
            if checkpoint.trigger in {"after_async_commit", "after_compact"}:
                handoff_total += 1
                if checkpoint.passed:
                    handoff_successes += 1

            if checkpoint.matched_forbidden:
                forbidden_leaks += 1

            if checkpoint.matched_expected is not None:
                expected_checkpoint_count += 1
                if checkpoint.matched_expected:
                    matched_expected_count += 1
    correction = capability_counts.get("correction", {"total": 0, "passed": 0, "failed": 0})
    overwrite_correctness = (
        correction["passed"] / correction["total"] if correction["total"] else None
    )

    return {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "capabilities": dict(sorted(capability_counts.items())),
        "case_pass_rate": (passed_cases / total_cases) if total_cases else None,
        "checkpoint_pass_rate": _compute_checkpoint_pass_rate(case_results),
        "answer_match_rate": (
            matched_expected_count / expected_checkpoint_count if expected_checkpoint_count else None
        ),
        "handoff_success_rate": (
            handoff_successes / handoff_total if handoff_total else None
        ),
        "overwrite_correctness": overwrite_correctness,
        "correction_accuracy": overwrite_correctness,
        "tool_grounded_accuracy": (
            tool_grounded_passes / tool_grounded_total if tool_grounded_total else None
        ),
        "forbidden_fact_leak_rate": (
            forbidden_leaks / total_checkpoints if total_checkpoints else None
        ),
        "wm_growth_slope": _average_growth_slope(growth_slopes),
        "section_budget_violation_count": _count_budget_violations(case_results),
    }


def build_capability_scores(cases: list[CaseSpec], case_results: list[CaseRunResult]) -> dict[str, dict]:
    cases_by_id = {case.case_id: case for case in cases}
    grouped: dict[str, list[CaseRunResult]] = defaultdict(list)
    for result in case_results:
        case = cases_by_id.get(result.case_id)
        capability = case.capability if case else "unknown"
        grouped[capability].append(result)

    scores: dict[str, dict] = {}
    for capability, results in sorted(grouped.items()):
        total = len(results)
        passed = sum(1 for result in results if result.passed)
        scores[capability] = {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": total - passed,
            "case_pass_rate": (passed / total) if total else None,
        }
    return scores


def write_result_bundle(
    *,
    output_dir: str | Path,
    cases: list[CaseSpec],
    case_results: list[CaseRunResult],
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    cases_by_id = {case.case_id: case for case in cases}
    summary = build_summary(cases, case_results)
    capability_scores = build_capability_scores(cases, case_results)

    summary_json = output_path / "summary.json"
    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    checkpoints_csv = output_path / "checkpoints.csv"
    with checkpoints_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "checkpoint_id",
                "trigger",
                "mode",
                "capability",
                "passed",
                "infra_failure",
                "expected_failure",
                "source_expectation",
                "matched_expected",
                "matched_forbidden",
                "wm_chars",
                "wm_token_estimate",
                "message_count",
                "tool_pair_count",
                "answer",
                "failures",
            ],
        )
        writer.writeheader()
        for result in case_results:
            case = cases_by_id.get(result.case_id)
            for checkpoint in result.checkpoint_results:
                writer.writerow(
                    {
                        "case_id": result.case_id,
                        "checkpoint_id": checkpoint.id,
                        "trigger": checkpoint.trigger,
                        "mode": result.mode,
                        "capability": case.capability if case else "unknown",
                        "passed": str(checkpoint.passed).lower(),
                        "infra_failure": str(checkpoint.infra_failure).lower(),
                        "expected_failure": str(case.expected_failure if case else False).lower(),
                        "source_expectation": checkpoint.source_expectation,
                        "matched_expected": _format_optional_bool(checkpoint.matched_expected),
                        "matched_forbidden": str(checkpoint.matched_forbidden).lower(),
                        "wm_chars": _format_optional_int(checkpoint.wm_chars),
                        "wm_token_estimate": _format_optional_int(checkpoint.wm_token_estimate),
                        "message_count": _format_optional_int(checkpoint.message_count),
                        "tool_pair_count": _format_optional_int(checkpoint.tool_pair_count),
                        "answer": checkpoint.answer,
                        "failures": " | ".join(checkpoint.failures),
                    }
                )

    capability_scores_json = output_path / "capability_scores.json"
    capability_scores_json.write_text(
        json.dumps(capability_scores, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    growth_csv = output_path / "growth.csv"
    with growth_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "checkpoint_id",
                "trigger",
                "mode",
                "capability",
                "wm_chars",
                "wm_token_estimate",
                "message_count",
                "tool_pair_count",
            ],
        )
        writer.writeheader()
        for result in case_results:
            case = cases_by_id.get(result.case_id)
            for checkpoint in result.checkpoint_results:
                if checkpoint.wm_token_estimate is None and checkpoint.wm_chars is None:
                    continue
                writer.writerow(
                    {
                        "case_id": result.case_id,
                        "checkpoint_id": checkpoint.id,
                        "trigger": checkpoint.trigger,
                        "mode": result.mode,
                        "capability": case.capability if case else "unknown",
                        "wm_chars": _format_optional_int(checkpoint.wm_chars),
                        "wm_token_estimate": _format_optional_int(checkpoint.wm_token_estimate),
                        "message_count": _format_optional_int(checkpoint.message_count),
                        "tool_pair_count": _format_optional_int(checkpoint.tool_pair_count),
                    }
                )

    artifacts_dir = output_path / "artifacts"
    for result in case_results:
        case_dir = artifacts_dir / result.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        for checkpoint in result.checkpoint_results:
            artifact_path = case_dir / f"{checkpoint.id}.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "case_id": result.case_id,
                        "mode": result.mode,
                        "checkpoint_id": checkpoint.id,
                        "trigger": checkpoint.trigger,
                        "passed": checkpoint.passed,
                        "infra_failure": checkpoint.infra_failure,
                        "source_expectation": checkpoint.source_expectation,
                        "matched_expected": checkpoint.matched_expected,
                        "matched_forbidden": checkpoint.matched_forbidden,
                        "answer": checkpoint.answer,
                        "failures": checkpoint.failures,
                        "wm_chars": checkpoint.wm_chars,
                        "wm_token_estimate": checkpoint.wm_token_estimate,
                        "message_count": checkpoint.message_count,
                        "tool_pair_count": checkpoint.tool_pair_count,
                        "section_token_estimates": checkpoint.section_token_estimates,
                        "section_item_counts": checkpoint.section_item_counts,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    report_md = output_path / "report.md"
    report_lines = [
        "# Work Memory Benchmark Report",
        "",
        f"- Total cases: {summary['total_cases']}",
        f"- Passed cases: {summary['passed_cases']}",
        f"- Failed cases: {summary['failed_cases']}",
        f"- Case pass rate: {_format_ratio(summary['case_pass_rate'])}",
        f"- Checkpoint pass rate: {_format_ratio(summary['checkpoint_pass_rate'])}",
        f"- Answer match rate: {_format_ratio(summary['answer_match_rate'])}",
        f"- Handoff success rate: {_format_ratio(summary['handoff_success_rate'])}",
        f"- Overwrite correctness: {_format_ratio(summary['overwrite_correctness'])}",
        f"- Tool grounded accuracy: {_format_ratio(summary['tool_grounded_accuracy'])}",
        f"- Forbidden fact leak rate: {_format_ratio(summary['forbidden_fact_leak_rate'])}",
        f"- WM growth slope: {_format_float(summary['wm_growth_slope'])}",
        f"- Section budget violations: {summary['section_budget_violation_count']}",
        "",
        "## Capabilities",
        "",
    ]
    for capability, stats in capability_scores.items():
        report_lines.append(
            f"- `{capability}`: {stats['passed_cases']}/{stats['total_cases']} "
            f"({_format_ratio(stats['case_pass_rate'])})"
        )

    report_lines.extend(["", "## Cases", ""])
    for result in case_results:
        case = cases_by_id.get(result.case_id)
        capability = case.capability if case else "unknown"
        repeat_total = result.repeat_count or 1
        repeat_pass_count = result.pass_count if result.pass_count is not None else int(result.passed)
        report_lines.append(
            f"- `{result.case_id}` ({capability}, {result.mode}): "
            f"{'PASS' if result.passed else 'FAIL'} "
            f"[repeat {repeat_pass_count}/{repeat_total}]"
        )

    report_md.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return {
        "summary_json": summary_json,
        "checkpoints_csv": checkpoints_csv,
        "capability_scores_json": capability_scores_json,
        "growth_csv": growth_csv,
        "report_md": report_md,
    }


def _compute_checkpoint_pass_rate(case_results: list[CaseRunResult]) -> float | None:
    total = 0
    passed = 0
    for result in case_results:
        for checkpoint in result.checkpoint_results:
            total += 1
            if checkpoint.passed:
                passed += 1
    return (passed / total) if total else None


def _count_budget_violations(case_results: list[CaseRunResult]) -> int:
    return sum(
        1
        for result in case_results
        for checkpoint in result.checkpoint_results
        if checkpoint.trigger == "budget_assertions" and not checkpoint.passed
    )


def _compute_case_growth_slope(case_result: CaseRunResult) -> float | None:
    points = [
        float(checkpoint.wm_token_estimate)
        for checkpoint in case_result.checkpoint_results
        if checkpoint.wm_token_estimate is not None
        and checkpoint.trigger not in {"artifact_assertions", "budget_assertions"}
    ]
    return _compute_growth_slope(points)


def _average_growth_slope(points: list[float]) -> float | None:
    if not points:
        return None
    return sum(points) / len(points)


def _compute_growth_slope(points: list[float]) -> float | None:
    if not points:
        return None
    if len(points) == 1:
        return None

    xs = [float(index + 1) for index in range(len(points))]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(points) / len(points)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, points, strict=False))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return float(points[-1])
    return numerator / denominator


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return ""
    return str(value).lower()


def _format_optional_int(value: int | None) -> str:
    return "" if value is None else str(value)


def _format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _format_float(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
