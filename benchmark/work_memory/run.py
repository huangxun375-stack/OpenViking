from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import uuid4


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.case_loader import CaseValidationError, load_cases
from src.openclaw_client import OpenClawBenchmarkClient
from src.report import write_result_bundle
from src.runner import BenchmarkRunner, aggregate_repeat_outcomes


DEFAULT_OPENCLAW_BASE_URL = "http://127.0.0.1:18789"
DEFAULT_OPENVIKING_BASE_URL = "http://127.0.0.1:1933"


def _build_scoped_session_key(
    *,
    run_id: str,
    case_id: str,
    repeat_index: int,
    base_session_key: str | None,
) -> str:
    if base_session_key and base_session_key.strip():
        prefix = base_session_key.strip()
    else:
        prefix = "wm-benchmark"
    return f"{prefix}:{run_id}:{case_id}:r{repeat_index + 1}"


def _build_scoped_session_id(
    *,
    run_id: str,
    case_id: str,
    repeat_index: int,
    base_session_id: str | None,
) -> str | None:
    if not base_session_id or not base_session_id.strip():
        return None
    prefix = base_session_id.strip()
    return f"{prefix}-{run_id}-{case_id}-r{repeat_index + 1}"


def _build_client_for_case(
    *,
    args: argparse.Namespace,
    case_id: str,
    repeat_index: int,
    run_id: str,
) -> OpenClawBenchmarkClient:
    return OpenClawBenchmarkClient(
        openclaw_base_url=args.openclaw_base_url,
        openclaw_token=args.openclaw_token,
        ov_base_url=args.ov_base_url,
        ov_api_key=args.ov_api_key,
        agent_id=args.agent_id,
        user=args.user,
        session_key=_build_scoped_session_key(
            run_id=run_id,
            case_id=case_id,
            repeat_index=repeat_index,
            base_session_key=args.session_key,
        ),
        session_id=_build_scoped_session_id(
            run_id=run_id,
            case_id=case_id,
            repeat_index=repeat_index,
            base_session_id=args.session_id,
        ),
        keep_recent_count=max(0, args.keep_recent_count),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Work Memory benchmark cases")
    parser.add_argument(
        "--cases-dir",
        default=str(Path(__file__).resolve().parent / "cases"),
        help="Directory containing benchmark case YAML files.",
    )
    parser.add_argument("--case", help="Run a single case by case_id.")
    parser.add_argument("--capability", help="Filter cases by capability.")
    parser.add_argument("--mode", help="Filter cases by execution mode.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "results" / "work_memory"),
        help="Directory to write benchmark outputs.",
    )
    parser.add_argument(
        "--openclaw-base-url",
        default=os.environ.get("OPENCLAW_BASE_URL", DEFAULT_OPENCLAW_BASE_URL),
        help="OpenClaw gateway base URL.",
    )
    parser.add_argument(
        "--openclaw-token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN"),
        help="OpenClaw gateway auth token.",
    )
    parser.add_argument(
        "--ov-base-url",
        default=(
            os.environ.get("OPENVIKING_BASE_URL")
            or os.environ.get("OPENVIKING_URL")
            or DEFAULT_OPENVIKING_BASE_URL
        ),
        help="OpenViking base URL.",
    )
    parser.add_argument(
        "--ov-api-key",
        default=os.environ.get("OPENVIKING_API_KEY"),
        help="OpenViking API key.",
    )
    parser.add_argument("--agent-id", default="wm-benchmark", help="Agent id for benchmark traffic.")
    parser.add_argument("--user", default="wm-benchmark", help="User id for chat_e2e turns.")
    parser.add_argument("--session-key", help="Explicit OpenClaw session key to reuse.")
    parser.add_argument("--session-id", help="Explicit OpenClaw session id to reuse.")
    parser.add_argument(
        "--keep-recent-count",
        type=int,
        default=10,
        help="keep_recent_count forwarded during async commit checkpoints.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to execute each selected case.",
    )
    parser.add_argument("--list-cases", action="store_true", help="List discovered case ids.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve cases without execution.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.cases_dir)
    except CaseValidationError as exc:
        print(f"[work-memory-benchmark] {exc}", file=sys.stderr)
        return 1

    filtered_cases = [
        case
        for case in cases
        if (not args.case or case.case_id == args.case)
        and (not args.capability or case.capability == args.capability)
        and (not args.mode or case.mode == args.mode)
    ]

    if not filtered_cases:
        print("[work-memory-benchmark] no cases matched the provided filters", file=sys.stderr)
        return 1

    if args.list_cases or args.dry_run:
        for case in filtered_cases:
            print(case.case_id)
        return 0

    if not args.openclaw_token:
        print("[work-memory-benchmark] missing --openclaw-token or OPENCLAW_GATEWAY_TOKEN", file=sys.stderr)
        return 2
    if not args.ov_api_key:
        print("[work-memory-benchmark] missing --ov-api-key or OPENVIKING_API_KEY", file=sys.stderr)
        return 2

    case_results = []
    repeat = max(1, int(args.repeat))
    run_id = uuid4().hex
    for case in filtered_cases:
        repeat_results = []
        for repeat_index in range(repeat):
            runner = BenchmarkRunner(
                client=_build_client_for_case(
                    args=args,
                    case_id=case.case_id,
                    repeat_index=repeat_index,
                    run_id=run_id,
                )
            )
            repeat_results.append(runner.run_case_once(case))
        final_result = repeat_results[-1]
        final_result.pass_count = sum(1 for result in repeat_results if result.passed)
        final_result.repeat_count = repeat
        if repeat > 1:
            final_result.passed = aggregate_repeat_outcomes(
                [result.passed for result in repeat_results],
                mode=case.mode,
            )
        case_results.append(final_result)

    outputs = write_result_bundle(
        output_dir=args.output_dir,
        cases=filtered_cases,
        case_results=case_results,
    )
    print(outputs["summary_json"])
    print(outputs["checkpoints_csv"])
    print(outputs["report_md"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
