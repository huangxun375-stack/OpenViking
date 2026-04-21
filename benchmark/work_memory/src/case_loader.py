from __future__ import annotations

from pathlib import Path

import yaml

from .models import ArtifactAssertions, BudgetAssertions, CaseSpec, CheckpointSpec

VALID_CASE_MODES = {"chat_e2e", "session_inject", "artifact_whitebox"}
VALID_CHECKPOINT_TRIGGERS = {"after_turn", "after_async_commit", "after_compact"}
VALID_SOURCE_EXPECTATIONS = {"any", "tail", "wm_candidate", "wm_required", "tool_evidence"}


class CaseValidationError(ValueError):
    """Raised when a benchmark case file is invalid."""


def load_cases(cases_dir: str | Path) -> list[CaseSpec]:
    cases_path = Path(cases_dir)
    if not cases_path.exists():
        raise CaseValidationError(f"Cases directory does not exist: {cases_path}")

    case_specs = [load_case(case_file) for case_file in sorted(cases_path.glob("*.yaml"))]
    return case_specs


def load_case(case_file: str | Path) -> CaseSpec:
    case_path = Path(case_file)
    raw = yaml.safe_load(case_path.read_text(encoding="utf-8")) or {}

    case_id = str(raw.get("case_id") or "").strip()
    if not case_id:
        raise CaseValidationError(f"{case_path.name}: missing case_id")
    if case_id != case_path.stem:
        raise CaseValidationError(
            f"{case_path.name}: case_id '{case_id}' must match file stem '{case_path.stem}'"
        )

    mode = str(raw.get("mode") or "").strip()
    if mode not in VALID_CASE_MODES:
        raise CaseValidationError(f"{case_path.name}: invalid mode '{mode}'")

    title = str(raw.get("title") or "").strip()
    if not title:
        raise CaseValidationError(f"{case_path.name}: missing title")

    capability = str(raw.get("capability") or "").strip()
    if not capability:
        raise CaseValidationError(f"{case_path.name}: missing capability")

    priority = str(raw.get("priority") or "").strip()
    if not priority:
        raise CaseValidationError(f"{case_path.name}: missing priority")

    checkpoints = _load_checkpoints(case_path, raw.get("checkpoints") or [])
    artifact_assertions = _load_artifact_assertions(raw.get("artifact_assertions") or {})
    if mode == "artifact_whitebox" and not checkpoints and not _has_artifact_assertions(
        artifact_assertions
    ):
        raise CaseValidationError(
            f"{case_path.name}: artifact_whitebox case needs checkpoints or artifact assertions"
        )

    return CaseSpec(
        case_id=case_id,
        title=title,
        mode=mode,
        capability=capability,
        priority=priority,
        tags=[str(tag) for tag in (raw.get("tags") or [])],
        expected_failure=bool(raw.get("expected_failure", False)),
        turns=list(raw.get("turns") or []),
        checkpoints=checkpoints,
        artifact_assertions=artifact_assertions,
        budget_assertions=_load_budget_assertions(raw.get("budget_assertions") or {}),
    )


def _load_checkpoints(case_path: Path, raw_checkpoints: list[dict]) -> list[CheckpointSpec]:
    checkpoints: list[CheckpointSpec] = []
    for index, raw_checkpoint in enumerate(raw_checkpoints):
        checkpoint_id = str(raw_checkpoint.get("id") or "").strip()
        if not checkpoint_id:
            raise CaseValidationError(f"{case_path.name}: checkpoint {index} missing id")

        trigger = str(raw_checkpoint.get("trigger") or "").strip()
        if trigger not in VALID_CHECKPOINT_TRIGGERS:
            raise CaseValidationError(
                f"{case_path.name}: checkpoint '{checkpoint_id}' has invalid trigger '{trigger}'"
            )

        source_expectation = str(raw_checkpoint.get("source_expectation") or "any").strip()
        if source_expectation not in VALID_SOURCE_EXPECTATIONS:
            raise CaseValidationError(
                f"{case_path.name}: checkpoint '{checkpoint_id}' has invalid "
                f"source_expectation '{source_expectation}'"
            )

        query = str(raw_checkpoint.get("query") or "").strip()
        if not query:
            raise CaseValidationError(
                f"{case_path.name}: checkpoint '{checkpoint_id}' is missing query"
            )

        checkpoints.append(
            CheckpointSpec(
                id=checkpoint_id,
                trigger=trigger,
                query=query,
                expected_any=[str(item) for item in (raw_checkpoint.get("expected_any") or [])],
                forbidden_any=[
                    str(item) for item in (raw_checkpoint.get("forbidden_any") or [])
                ],
                source_expectation=source_expectation,
            )
        )
    return checkpoints


def _load_artifact_assertions(raw: dict) -> ArtifactAssertions:
    return ArtifactAssertions(
        wm_must_contain=[str(item) for item in (raw.get("wm_must_contain") or [])],
        wm_must_not_contain=[str(item) for item in (raw.get("wm_must_not_contain") or [])],
        wm_may_contain_only_as_historical=[
            str(item) for item in (raw.get("wm_may_contain_only_as_historical") or [])
        ],
        tail_min_messages=(
            int(raw["tail_min_messages"]) if raw.get("tail_min_messages") is not None else None
        ),
        tool_pair_count=(
            int(raw["tool_pair_count"]) if raw.get("tool_pair_count") is not None else None
        ),
        key_file_paths_present=[
            str(item) for item in (raw.get("key_file_paths_present") or [])
        ],
        section_contains={
            str(key): [str(item) for item in value]
            for key, value in (raw.get("section_contains") or {}).items()
        },
        section_not_contains={
            str(key): [str(item) for item in value]
            for key, value in (raw.get("section_not_contains") or {}).items()
        },
    )


def _load_budget_assertions(raw: dict) -> BudgetAssertions:
    max_total_wm_tokens = raw.get("max_total_wm_tokens")
    return BudgetAssertions(
        max_total_wm_tokens=(
            int(max_total_wm_tokens) if max_total_wm_tokens is not None else None
        ),
        max_section_tokens={
            str(section_name): int(value)
            for section_name, value in (raw.get("max_section_tokens") or {}).items()
        },
        max_section_items={
            str(section_name): int(value)
            for section_name, value in (raw.get("max_section_items") or {}).items()
        },
    )


def _has_artifact_assertions(assertions: ArtifactAssertions) -> bool:
    return any(
        (
            assertions.wm_must_contain,
            assertions.wm_must_not_contain,
            assertions.wm_may_contain_only_as_historical,
            assertions.section_contains,
            assertions.section_not_contains,
            assertions.tail_min_messages is not None,
            assertions.tool_pair_count is not None,
            assertions.key_file_paths_present,
        )
    )
