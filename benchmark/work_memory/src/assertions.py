from __future__ import annotations

import re
from collections.abc import Sequence

from .models import ArtifactAssertions, BudgetAssertions
from .normalization import (
    compile_phrase_pattern,
    contains_phrase,
    normalize_text,
    split_normalized_sentences,
    split_sections,
)

_LINE_HISTORICAL_MARKERS = (
    "previous",
    "formerly",
    "historical",
    "old path",
    "old owner",
    "former",
    "superseded",
    "prior",
    "\u4e4b\u524d",
    "\u66fe\u7ecf",
    "\u5386\u53f2",
)
_SENTENCE_HISTORICAL_MARKERS = _LINE_HISTORICAL_MARKERS + (
    "previously",
    "initially",
    "earlier",
    "before",
    "used to",
)
_CONTRAST_MARKERS = (
    "now",
    "current",
    "currently",
    "final",
    "instead",
    "but",
    "however",
)
_NEGATION_PREFIX_RE = re.compile(r"(?:^|[^0-9a-z])(?:not|never|no longer|no more)\s+$")


def evaluate_text_expectations(
    text: str,
    *,
    expected_any: Sequence[str] | None = None,
    forbidden_any: Sequence[str] | None = None,
) -> list[str]:
    failures, _, _ = inspect_text_expectations(
        text,
        expected_any=expected_any,
        forbidden_any=forbidden_any,
    )
    return failures


def inspect_text_expectations(
    text: str,
    *,
    expected_any: Sequence[str] | None = None,
    forbidden_any: Sequence[str] | None = None,
) -> tuple[list[str], bool | None, bool]:
    failures: list[str] = []
    normalized_text = normalize_text(text)
    normalized_sentences = split_normalized_sentences(text)
    expected_any = list(expected_any or [])
    forbidden_any = list(forbidden_any or [])
    matched_expected = None

    if expected_any:
        matched_expected = any(_contains_phrase(normalized_text, phrase) for phrase in expected_any)
    if expected_any and not matched_expected:
        failures.append(
            "expected_any did not match any candidate: " + ", ".join(sorted(expected_any))
        )

    leaking = [
        phrase
        for phrase in forbidden_any
        if _forbidden_phrase_leaks(
            normalized_text,
            normalized_sentences,
            phrase,
            expected_any=expected_any,
        )
    ]
    if leaking:
        failures.append("forbidden_any matched leaked candidate(s): " + ", ".join(sorted(leaking)))

    return failures, matched_expected, bool(leaking)


def evaluate_artifact_assertions(
    *,
    wm_text: str,
    artifact_assertions: ArtifactAssertions,
    message_count: int | None = None,
    tool_pair_count: int | None = None,
) -> list[str]:
    failures: list[str] = []
    normalized_wm = normalize_text(wm_text)
    lines = [line for line in wm_text.splitlines() if line.strip()]

    for phrase in artifact_assertions.wm_must_contain:
        if not _contains_phrase(normalized_wm, phrase):
            failures.append(f"wm_must_contain missing phrase: {phrase}")

    for phrase in artifact_assertions.wm_must_not_contain:
        if _contains_phrase(normalized_wm, phrase):
            failures.append(f"wm_must_not_contain leaked phrase: {phrase}")

    for phrase in artifact_assertions.wm_may_contain_only_as_historical:
        matching_lines = [line for line in lines if _contains_phrase(normalize_text(line), phrase)]
        if not matching_lines:
            continue
        if any(not _line_is_historical(line) for line in matching_lines):
            failures.append(f"phrase must appear only as historical context: {phrase}")

    sections = split_sections(wm_text)
    for section_name, phrases in artifact_assertions.section_contains.items():
        section_text = sections.get(section_name, "")
        normalized_section = normalize_text(section_text)
        for phrase in phrases:
            if not _contains_phrase(normalized_section, phrase):
                failures.append(
                    f"section_contains missing phrase in '{section_name}': {phrase}"
                )

    for section_name, phrases in artifact_assertions.section_not_contains.items():
        section_text = sections.get(section_name, "")
        normalized_section = normalize_text(section_text)
        for phrase in phrases:
            if _contains_phrase(normalized_section, phrase):
                failures.append(
                    f"section_not_contains leaked phrase in '{section_name}': {phrase}"
                )

    if (
        artifact_assertions.tail_min_messages is not None
        and (message_count or 0) < artifact_assertions.tail_min_messages
    ):
        failures.append(
            "tail_min_messages violated: "
            f"expected at least {artifact_assertions.tail_min_messages}, got {message_count or 0}"
        )

    if (
        artifact_assertions.tool_pair_count is not None
        and (tool_pair_count or 0) != artifact_assertions.tool_pair_count
    ):
        failures.append(
            "tool_pair_count violated: "
            f"expected {artifact_assertions.tool_pair_count}, got {tool_pair_count or 0}"
        )

    for file_path in artifact_assertions.key_file_paths_present:
        if not _contains_phrase(normalized_wm, file_path):
            failures.append(f"key_file_paths_present missing path: {file_path}")

    return failures


def evaluate_budget_assertions(
    *,
    wm_token_estimate: int,
    section_token_estimates: dict[str, int],
    section_item_counts: dict[str, int],
    budget_assertions: BudgetAssertions,
) -> list[str]:
    failures: list[str] = []

    if (
        budget_assertions.max_total_wm_tokens is not None
        and wm_token_estimate > budget_assertions.max_total_wm_tokens
    ):
        failures.append(
            "max_total_wm_tokens exceeded: "
            f"{wm_token_estimate} > {budget_assertions.max_total_wm_tokens}"
        )

    for section_name, max_tokens in budget_assertions.max_section_tokens.items():
        actual_tokens = section_token_estimates.get(section_name, 0)
        if actual_tokens > max_tokens:
            failures.append(
                f"max_section_tokens exceeded for '{section_name}': {actual_tokens} > {max_tokens}"
            )

    for section_name, max_items in budget_assertions.max_section_items.items():
        actual_items = section_item_counts.get(section_name, 0)
        if actual_items > max_items:
            failures.append(
                f"max_section_items exceeded for '{section_name}': {actual_items} > {max_items}"
            )

    return failures


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    return contains_phrase(normalized_text, phrase)


def _line_is_historical(line: str) -> bool:
    normalized_line = normalize_text(line)
    return any(_contains_phrase(normalized_line, marker) for marker in _LINE_HISTORICAL_MARKERS)


def _forbidden_phrase_leaks(
    normalized_text: str,
    normalized_sentences: Sequence[str],
    phrase: str,
    *,
    expected_any: Sequence[str],
) -> bool:
    if not _contains_phrase(normalized_text, phrase):
        return False

    sentences = list(normalized_sentences) or [normalized_text]
    for sentence in sentences:
        if not _contains_phrase(sentence, phrase):
            continue
        if _sentence_is_historical(sentence):
            continue
        if _phrase_is_negated(sentence, phrase):
            continue
        if _sentence_has_contrastive_expected(sentence, expected_any):
            continue
        return True
    return False


def _sentence_is_historical(sentence: str) -> bool:
    return any(_contains_phrase(sentence, marker) for marker in _SENTENCE_HISTORICAL_MARKERS)


def _phrase_is_negated(sentence: str, phrase: str) -> bool:
    pattern = compile_phrase_pattern(phrase)
    if pattern is None:
        return False

    for match in pattern.finditer(sentence):
        prefix = sentence[: match.start()].rstrip()
        candidate = prefix[-32:]
        if _NEGATION_PREFIX_RE.search(candidate):
            return True
    return False


def _sentence_has_contrastive_expected(sentence: str, expected_any: Sequence[str]) -> bool:
    if not expected_any:
        return False
    if not any(_contains_phrase(sentence, phrase) for phrase in expected_any):
        return False
    return any(_contains_phrase(sentence, marker) for marker in _CONTRAST_MARKERS)
