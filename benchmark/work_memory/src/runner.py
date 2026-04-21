from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .assertions import (
    evaluate_artifact_assertions,
    evaluate_budget_assertions,
    inspect_text_expectations,
)
from .models import CaseRunResult, CaseSpec, CheckpointRunResult
from .probes import collect_context_snapshot


class BackgroundTaskFailure(RuntimeError):
    pass


@dataclass(slots=True)
class BenchmarkRunner:
    client: Any
    context_probe: Callable[[Any], Any] | None = None
    poll_interval_s: float = 0.5
    commit_poll_timeout_s: float = 300.0
    transient_poll_error_tolerance: int = 3

    def run_case_once(self, case_spec: CaseSpec) -> CaseRunResult:
        for turn in case_spec.turns:
            self.client.apply_turn(turn)

        checkpoint_results: list[CheckpointRunResult] = []
        latest_snapshot = None
        for checkpoint in case_spec.checkpoints:
            try:
                if checkpoint.trigger == "after_async_commit":
                    self._wait_for_background_task(self.client.commit_session())
                elif checkpoint.trigger == "after_compact":
                    self._wait_for_background_task(self.client.compact_session())
            except BackgroundTaskFailure as exc:
                checkpoint_results.append(
                    CheckpointRunResult(
                        id=checkpoint.id,
                        trigger=checkpoint.trigger,
                        passed=False,
                        failures=[str(exc)],
                        infra_failure=True,
                        source_expectation=checkpoint.source_expectation,
                    )
                )
                continue

            snapshot = (
                self.context_probe(self.client)
                if self.context_probe is not None
                else collect_context_snapshot(self.client)
            )
            latest_snapshot = snapshot
            answer = self.client.ask(checkpoint.query)
            failures, matched_expected, matched_forbidden = inspect_text_expectations(
                answer,
                expected_any=checkpoint.expected_any,
                forbidden_any=checkpoint.forbidden_any,
            )
            checkpoint_results.append(
                CheckpointRunResult(
                    id=checkpoint.id,
                    trigger=checkpoint.trigger,
                    passed=not failures,
                    answer=answer,
                    failures=failures,
                    source_expectation=checkpoint.source_expectation,
                    matched_expected=matched_expected,
                    matched_forbidden=matched_forbidden,
                    wm_chars=snapshot.wm_chars,
                    wm_token_estimate=snapshot.wm_token_estimate,
                    message_count=snapshot.message_count,
                    section_token_estimates=dict(snapshot.section_token_estimates),
                    section_item_counts=dict(snapshot.section_item_counts),
                    tool_pair_count=snapshot.tool_pair_count,
                )
            )

        if _case_has_artifact_assertions(case_spec):
            snapshot = latest_snapshot or (
                self.context_probe(self.client)
                if self.context_probe is not None
                else collect_context_snapshot(self.client)
            )
            artifact_failures = evaluate_artifact_assertions(
                wm_text=snapshot.wm_text,
                artifact_assertions=case_spec.artifact_assertions,
                message_count=snapshot.message_count,
                tool_pair_count=snapshot.tool_pair_count,
            )
            checkpoint_results.append(
                CheckpointRunResult(
                    id="artifact_assertions",
                    trigger="artifact_assertions",
                    passed=not artifact_failures,
                    failures=artifact_failures,
                    wm_chars=snapshot.wm_chars,
                    wm_token_estimate=snapshot.wm_token_estimate,
                    message_count=snapshot.message_count,
                    section_token_estimates=dict(snapshot.section_token_estimates),
                    section_item_counts=dict(snapshot.section_item_counts),
                    tool_pair_count=snapshot.tool_pair_count,
                )
            )

        if _case_has_budget_assertions(case_spec):
            snapshot = latest_snapshot or (
                self.context_probe(self.client)
                if self.context_probe is not None
                else collect_context_snapshot(self.client)
            )
            budget_failures = evaluate_budget_assertions(
                wm_token_estimate=snapshot.wm_token_estimate,
                section_token_estimates=snapshot.section_token_estimates,
                section_item_counts=snapshot.section_item_counts,
                budget_assertions=case_spec.budget_assertions,
            )
            checkpoint_results.append(
                CheckpointRunResult(
                    id="budget_assertions",
                    trigger="budget_assertions",
                    passed=not budget_failures,
                    failures=budget_failures,
                    wm_chars=snapshot.wm_chars,
                    wm_token_estimate=snapshot.wm_token_estimate,
                    message_count=snapshot.message_count,
                    section_token_estimates=dict(snapshot.section_token_estimates),
                    section_item_counts=dict(snapshot.section_item_counts),
                    tool_pair_count=snapshot.tool_pair_count,
                )
            )

        return CaseRunResult(
            case_id=case_spec.case_id,
            mode=case_spec.mode,
            passed=all(result.passed for result in checkpoint_results),
            checkpoint_results=checkpoint_results,
            pass_count=None,
            repeat_count=1,
        )

    def _wait_for_background_task(self, initial_result: dict[str, Any] | None) -> dict[str, Any]:
        if not initial_result:
            return {}

        task_id = initial_result.get("task_id")
        if not task_id:
            return initial_result

        deadline = time.monotonic() + self.commit_poll_timeout_s
        consecutive_errors = 0
        while time.monotonic() < deadline:
            try:
                task = self.client.get_task(task_id)
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= self.transient_poll_error_tolerance:
                    raise BackgroundTaskFailure(
                        f"Commit task polling failed: {task_id}"
                    ) from None
                if self.poll_interval_s > 0:
                    time.sleep(self.poll_interval_s)
                continue

            consecutive_errors = 0
            status = task.get("status")
            if status == "completed":
                return task
            if status == "failed":
                raise BackgroundTaskFailure(f"Commit task failed: {task_id}")
            if self.poll_interval_s > 0:
                time.sleep(self.poll_interval_s)

        raise BackgroundTaskFailure(f"Commit polling timed out for task: {task_id}")


def aggregate_repeat_outcomes(outcomes: list[bool], *, mode: str) -> bool:
    if not outcomes:
        return False
    if mode == "artifact_whitebox":
        return all(outcomes)
    passed_count = sum(1 for outcome in outcomes if outcome)
    return passed_count >= (len(outcomes) // 2 + 1)


def _case_has_artifact_assertions(case_spec: CaseSpec) -> bool:
    assertions = case_spec.artifact_assertions
    return any(
        [
            assertions.wm_must_contain,
            assertions.wm_must_not_contain,
            assertions.wm_may_contain_only_as_historical,
            assertions.section_contains,
            assertions.section_not_contains,
            assertions.tail_min_messages is not None,
            assertions.tool_pair_count is not None,
            assertions.key_file_paths_present,
        ]
    )


def _case_has_budget_assertions(case_spec: CaseSpec) -> bool:
    assertions = case_spec.budget_assertions
    return any(
        [
            assertions.max_total_wm_tokens is not None,
            assertions.max_section_tokens,
            assertions.max_section_items,
        ]
    )
