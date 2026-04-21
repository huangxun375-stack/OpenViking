import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


def test_run_main_executes_case_and_writes_result_bundle(tmp_path, monkeypatch):
    _add_benchmark_package_to_path()

    import run as benchmark_run
    from src.models import CaseRunResult, CaseSpec, CheckpointRunResult

    case = CaseSpec(
        case_id="tail_fact_single",
        title="Tail fact single",
        mode="chat_e2e",
        capability="tail_recall",
        priority="critical",
    )

    monkeypatch.setattr(benchmark_run, "load_cases", lambda _path: [case])

    created_clients = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_clients.append(self)

    class _FakeRunner:
        def __init__(self, client, **kwargs):
            self.client = client
            self.kwargs = kwargs

        def run_case_once(self, case_spec):
            assert case_spec.case_id == "tail_fact_single"
            return CaseRunResult(
                case_id=case_spec.case_id,
                mode=case_spec.mode,
                passed=True,
                checkpoint_results=[
                    CheckpointRunResult(
                        id="cp_after_turn",
                        trigger="after_turn",
                        passed=True,
                        answer="alpha",
                    )
                ],
            )

    written = {}

    def _fake_write_result_bundle(*, output_dir, cases, case_results):
        written["output_dir"] = Path(output_dir)
        written["cases"] = cases
        written["case_results"] = case_results
        return {
            "summary_json": Path(output_dir) / "summary.json",
            "checkpoints_csv": Path(output_dir) / "checkpoints.csv",
            "report_md": Path(output_dir) / "report.md",
        }

    monkeypatch.setattr(benchmark_run, "OpenClawBenchmarkClient", _FakeClient)
    monkeypatch.setattr(benchmark_run, "BenchmarkRunner", _FakeRunner)
    monkeypatch.setattr(benchmark_run, "write_result_bundle", _fake_write_result_bundle)

    exit_code = benchmark_run.main(
        [
            "--case",
            "tail_fact_single",
            "--output-dir",
            str(tmp_path),
            "--openclaw-token",
            "gateway-token",
            "--ov-api-key",
            "ov-key",
        ]
    )

    assert exit_code == 0
    assert created_clients
    assert written["output_dir"] == tmp_path
    assert written["cases"][0].case_id == "tail_fact_single"
    assert written["case_results"][0].passed is True


def test_run_main_uses_fresh_client_per_case_and_repeat(tmp_path, monkeypatch):
    _add_benchmark_package_to_path()

    import run as benchmark_run
    from src.models import CaseRunResult, CaseSpec

    cases = [
        CaseSpec(
            case_id="tail_fact_single",
            title="Tail fact single",
            mode="chat_e2e",
            capability="tail_recall",
            priority="critical",
        ),
        CaseSpec(
            case_id="correction_current_owner",
            title="Correction current owner",
            mode="chat_e2e",
            capability="correction",
            priority="critical",
        ),
    ]

    monkeypatch.setattr(benchmark_run, "load_cases", lambda _path: cases)

    created_clients = []
    runner_client_ids = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_clients.append(self)

    class _FakeRunner:
        def __init__(self, client, **kwargs):
            self.client = client

        def run_case_once(self, case_spec):
            runner_client_ids.append((case_spec.case_id, id(self.client)))
            return CaseRunResult(
                case_id=case_spec.case_id,
                mode=case_spec.mode,
                passed=True,
            )

    monkeypatch.setattr(benchmark_run, "OpenClawBenchmarkClient", _FakeClient)
    monkeypatch.setattr(benchmark_run, "BenchmarkRunner", _FakeRunner)
    monkeypatch.setattr(
        benchmark_run,
        "write_result_bundle",
        lambda **kwargs: {
            "summary_json": Path(tmp_path) / "summary.json",
            "checkpoints_csv": Path(tmp_path) / "checkpoints.csv",
            "report_md": Path(tmp_path) / "report.md",
        },
    )

    exit_code = benchmark_run.main(
        [
            "--output-dir",
            str(tmp_path),
            "--repeat",
            "2",
            "--openclaw-token",
            "gateway-token",
            "--ov-api-key",
            "ov-key",
        ]
    )

    assert exit_code == 0
    assert len(created_clients) == 4
    assert len({client.kwargs["session_key"] for client in created_clients}) == 4
    assert len({client_id for _, client_id in runner_client_ids}) == 4
