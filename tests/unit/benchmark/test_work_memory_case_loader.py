import subprocess
import sys
from pathlib import Path


def test_list_cases_prints_case_ids_from_yaml_directory(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "tail_fact_single.yaml").write_text(
        "\n".join(
            [
                "case_id: tail_fact_single",
                "title: Tail fact single",
                "mode: chat_e2e",
                "capability: tail_recall",
                "priority: critical",
                "checkpoints:",
                "  - id: cp_after_turn",
                "    trigger: after_turn",
                '    query: "What is the recent fact?"',
                '    expected_any: ["alpha"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmark" / "work_memory" / "run.py"),
            "--cases-dir",
            str(cases_dir),
            "--list-cases",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "tail_fact_single" in result.stdout


def test_list_cases_uses_default_benchmark_case_directory():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmark" / "work_memory" / "run.py"),
            "--list-cases",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    discovered_case_ids = set(result.stdout.splitlines())
    assert discovered_case_ids == {
        "tail_fact_single",
        "tail_task_state_recent",
        "tail_recent_file_focus",
        "tail_recent_decision",
        "tail_recent_open_issue",
        "tail_recent_goal_shift",
        "wm_durable_fact_after_compact",
        "wm_durable_task_goal_after_compact",
        "wm_durable_file_context_after_compact",
        "wm_durable_decision_after_many_turns",
        "wm_cross_session_state_after_compact",
        "wm_open_issue_continuity_after_compact",
        "correction_current_owner",
        "correction_file_path_renamed",
        "correction_task_status",
        "correction_preference_override",
        "tool_result_fact_inject",
        "tool_result_status_change_inject",
        "tool_error_then_fix_inject",
        "tool_generated_file_inject",
        "files_keep_key_paths_drop_bulk_urls",
        "files_keep_active_modules_drop_noise_refs",
        "growth_append_only_key_facts",
        "growth_files_context_budget",
    }


def test_list_cases_reports_invalid_mode_validation_error(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "invalid_mode_case.yaml").write_text(
        "\n".join(
            [
                "case_id: invalid_mode_case",
                "title: Invalid mode case",
                "mode: bad_mode",
                "capability: tail_recall",
                "priority: critical",
                "checkpoints:",
                "  - id: cp_after_turn",
                "    trigger: after_turn",
                '    query: "What happened?"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmark" / "work_memory" / "run.py"),
            "--cases-dir",
            str(cases_dir),
            "--list-cases",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "invalid mode 'bad_mode'" in result.stderr


def test_list_cases_reports_case_id_mismatch(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "tail_fact_single.yaml").write_text(
        "\n".join(
            [
                "case_id: some_other_case_id",
                "title: Mismatch case id",
                "mode: chat_e2e",
                "capability: tail_recall",
                "priority: critical",
                "checkpoints:",
                "  - id: cp_after_turn",
                "    trigger: after_turn",
                '    query: "What happened?"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmark" / "work_memory" / "run.py"),
            "--cases-dir",
            str(cases_dir),
            "--list-cases",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must match file stem 'tail_fact_single'" in result.stderr
