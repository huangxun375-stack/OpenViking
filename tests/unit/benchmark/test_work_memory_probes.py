import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


def test_collect_context_snapshot_extracts_wm_text_and_counts():
    _add_benchmark_package_to_path()

    from src.probes import collect_context_snapshot

    class _FakeClient:
        def get_session_context(self):
            return {
                "latest_archive_overview": "## Working Memory\nalpha beta gamma",
                "messages": [{"role": "user"}, {"role": "assistant"}],
                "estimatedTokens": 42,
            }

    snapshot = collect_context_snapshot(_FakeClient())

    assert snapshot.wm_text == "## Working Memory\nalpha beta gamma"
    assert snapshot.wm_chars == len("## Working Memory\nalpha beta gamma")
    assert snapshot.wm_token_estimate == 42
    assert snapshot.message_count == 2
