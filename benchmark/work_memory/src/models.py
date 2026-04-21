from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CheckpointSpec:
    id: str
    trigger: str
    query: str
    expected_any: list[str] = field(default_factory=list)
    forbidden_any: list[str] = field(default_factory=list)
    source_expectation: str = "any"


@dataclass(slots=True)
class ArtifactAssertions:
    wm_must_contain: list[str] = field(default_factory=list)
    wm_must_not_contain: list[str] = field(default_factory=list)
    wm_may_contain_only_as_historical: list[str] = field(default_factory=list)
    section_contains: dict[str, list[str]] = field(default_factory=dict)
    section_not_contains: dict[str, list[str]] = field(default_factory=dict)
    tail_min_messages: int | None = None
    tool_pair_count: int | None = None
    key_file_paths_present: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BudgetAssertions:
    max_total_wm_tokens: int | None = None
    max_section_tokens: dict[str, int] = field(default_factory=dict)
    max_section_items: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class CaseSpec:
    case_id: str
    title: str
    mode: str
    capability: str
    priority: str
    tags: list[str] = field(default_factory=list)
    expected_failure: bool = False
    turns: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[CheckpointSpec] = field(default_factory=list)
    artifact_assertions: ArtifactAssertions = field(default_factory=ArtifactAssertions)
    budget_assertions: BudgetAssertions = field(default_factory=BudgetAssertions)


@dataclass(slots=True)
class CheckpointRunResult:
    id: str
    trigger: str
    passed: bool
    answer: str = ""
    failures: list[str] = field(default_factory=list)
    infra_failure: bool = False
    source_expectation: str = "any"
    matched_expected: bool | None = None
    matched_forbidden: bool = False
    wm_chars: int | None = None
    wm_token_estimate: int | None = None
    message_count: int | None = None
    section_token_estimates: dict[str, int] = field(default_factory=dict)
    section_item_counts: dict[str, int] = field(default_factory=dict)
    tool_pair_count: int | None = None


@dataclass(slots=True)
class CaseRunResult:
    case_id: str
    mode: str
    passed: bool
    checkpoint_results: list[CheckpointRunResult] = field(default_factory=list)
    pass_count: int | None = None
    repeat_count: int = 1
