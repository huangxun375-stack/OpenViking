import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


def _build_case(*, trigger: str):
    _add_benchmark_package_to_path()

    from src.models import CaseSpec, CheckpointSpec

    return CaseSpec(
        case_id=f"case_{trigger}",
        title=f"Case {trigger}",
        mode="chat_e2e",
        capability="tail_recall",
        priority="critical",
        turns=[{"role": "user", "content": "记住：当前负责人是 Bob。"}],
        checkpoints=[
            CheckpointSpec(
                id=f"cp_{trigger}",
                trigger=trigger,
                query="当前负责人是谁？",
                expected_any=["Bob"],
            )
        ],
    )


class _FakeClient:
    def __init__(self, *, answer: str = "Bob", task_statuses: list[str] | None = None):
        self.answer = answer
        self.task_statuses = list(task_statuses or ["completed"])
        self.applied_turns: list[dict] = []
        self.commit_calls = 0
        self.get_task_calls: list[str] = []
        self.compact_calls = 0

    def apply_turn(self, turn: dict) -> None:
        self.applied_turns.append(turn)

    def ask(self, query: str) -> str:
        return self.answer

    def commit_session(self) -> dict:
        self.commit_calls += 1
        return {"task_id": "task-1", "status": "accepted"}

    def get_task(self, task_id: str) -> dict:
        self.get_task_calls.append(task_id)
        status = self.task_statuses.pop(0) if self.task_statuses else "completed"
        return {"task_id": task_id, "status": status}

    def compact_session(self) -> None:
        self.compact_calls += 1

    def get_session_context(self) -> dict:
        return {
            "latest_archive_overview": "# Working Memory\n\n## Key Facts & Decisions\n- Current owner: Bob\n",
            "messages": [{"role": "user"}, {"role": "assistant"}],
            "estimatedTokens": 15,
        }


def test_after_turn_checkpoint_does_not_trigger_commit():
    _add_benchmark_package_to_path()

    from src.runner import BenchmarkRunner

    runner = BenchmarkRunner(client=_FakeClient(), poll_interval_s=0)
    result = runner.run_case_once(_build_case(trigger="after_turn"))

    assert result.passed is True
    assert runner.client.commit_calls == 0


def test_after_async_commit_polls_tasks_until_completed():
    _add_benchmark_package_to_path()

    from src.runner import BenchmarkRunner

    client = _FakeClient(task_statuses=["running", "completed"])
    runner = BenchmarkRunner(client=client, poll_interval_s=0)
    result = runner.run_case_once(_build_case(trigger="after_async_commit"))

    assert result.passed is True
    assert client.commit_calls == 1
    assert client.get_task_calls == ["task-1", "task-1"]


def test_after_compact_uses_compact_path_without_extra_commit_poll():
    _add_benchmark_package_to_path()

    from src.runner import BenchmarkRunner

    client = _FakeClient()
    runner = BenchmarkRunner(client=client, poll_interval_s=0)
    result = runner.run_case_once(_build_case(trigger="after_compact"))

    assert result.passed is True
    assert client.commit_calls == 0
    assert client.compact_calls == 1


def test_artifact_assertions_are_evaluated_against_wm_snapshot():
    _add_benchmark_package_to_path()

    from src.models import ArtifactAssertions
    from src.probes import ContextSnapshot
    from src.runner import BenchmarkRunner

    client = _FakeClient(answer="Bob")
    runner = BenchmarkRunner(
        client=client,
        poll_interval_s=0,
        context_probe=lambda _client: ContextSnapshot(
            wm_text="# Working Memory\n\n## Key Facts & Decisions\n- Current owner: Alice\n",
            wm_chars=62,
            wm_token_estimate=15,
            message_count=0,
            raw_context={},
            parsed_sections={"Key Facts & Decisions": "- Current owner: Alice"},
            section_token_estimates={"Key Facts & Decisions": 15},
            section_item_counts={"Key Facts & Decisions": 1},
            tool_pair_count=0,
        ),
    )
    case = _build_case(trigger="after_turn")
    case.artifact_assertions = ArtifactAssertions(wm_must_contain=["Bob"])

    result = runner.run_case_once(case)

    assert result.passed is False
    assert any(
        "wm_must_contain" in failure for failure in result.checkpoint_results[-1].failures
    )


def test_chat_e2e_repeat_uses_majority_pass_rule():
    _add_benchmark_package_to_path()

    from src.runner import aggregate_repeat_outcomes

    assert aggregate_repeat_outcomes([True, False, True], mode="chat_e2e") is True


def test_artifact_whitebox_repeat_requires_all_pass():
    _add_benchmark_package_to_path()

    from src.runner import aggregate_repeat_outcomes

    assert aggregate_repeat_outcomes([True, False, True], mode="artifact_whitebox") is False


def test_budget_assertions_fail_when_wm_token_budget_is_exceeded():
    _add_benchmark_package_to_path()

    from src.models import BudgetAssertions
    from src.probes import ContextSnapshot
    from src.runner import BenchmarkRunner

    client = _FakeClient(answer="Bob")
    runner = BenchmarkRunner(
        client=client,
        poll_interval_s=0,
        context_probe=lambda _client: ContextSnapshot(
            wm_text="# Working Memory\n\n## Key Facts & Decisions\n- alpha\n- beta\n",
            wm_chars=58,
            wm_token_estimate=20,
            message_count=0,
            raw_context={},
            parsed_sections={"Key Facts & Decisions": "- alpha\n- beta"},
            section_token_estimates={"Key Facts & Decisions": 20},
            section_item_counts={"Key Facts & Decisions": 2},
            tool_pair_count=0,
        ),
    )
    case = _build_case(trigger="after_turn")
    case.budget_assertions = BudgetAssertions(max_total_wm_tokens=10)

    result = runner.run_case_once(case)

    assert result.passed is False
    assert any(
        "max_total_wm_tokens" in failure
        for failure in result.checkpoint_results[-1].failures
    )


def test_async_commit_infra_failure_marks_checkpoint_without_crashing():
    _add_benchmark_package_to_path()

    from src.runner import BenchmarkRunner

    client = _FakeClient(task_statuses=["failed"])
    runner = BenchmarkRunner(client=client, poll_interval_s=0)

    result = runner.run_case_once(_build_case(trigger="after_async_commit"))

    assert result.passed is False
    assert result.checkpoint_results[0].infra_failure is True
    assert any("commit task failed" in failure.lower() for failure in result.checkpoint_results[0].failures)
