from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalization import split_sections


@dataclass(slots=True)
class ContextSnapshot:
    wm_text: str
    wm_chars: int
    wm_token_estimate: int
    message_count: int
    raw_context: dict[str, Any]
    parsed_sections: dict[str, str]
    section_token_estimates: dict[str, int]
    section_item_counts: dict[str, int]
    tool_pair_count: int


def collect_context_snapshot(client: Any) -> ContextSnapshot:
    payload = client.get_session_context()
    wm_text = _extract_working_memory_text(payload.get("latest_archive_overview"))
    estimated_tokens = payload.get("estimatedTokens")
    wm_token_estimate = (
        int(estimated_tokens)
        if isinstance(estimated_tokens, (int, float))
        else (max(1, len(wm_text) // 4) if wm_text else 0)
    )
    messages = payload.get("messages") or []
    parsed_sections = split_sections(wm_text)
    return ContextSnapshot(
        wm_text=wm_text,
        wm_chars=len(wm_text),
        wm_token_estimate=wm_token_estimate,
        message_count=len(messages),
        raw_context=payload,
        parsed_sections=parsed_sections,
        section_token_estimates={
            section_name: _estimate_tokens(section_text)
            for section_name, section_text in parsed_sections.items()
        },
        section_item_counts={
            section_name: _count_section_items(section_text)
            for section_name, section_text in parsed_sections.items()
        },
        tool_pair_count=_count_tool_pairs(messages),
    )


def _extract_working_memory_text(latest_archive_overview: Any) -> str:
    if isinstance(latest_archive_overview, str):
        return latest_archive_overview
    if isinstance(latest_archive_overview, dict):
        for key in ("working_memory", "text", "content"):
            value = latest_archive_overview.get(key)
            if isinstance(value, str):
                return value
    return str(latest_archive_overview or "")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _count_section_items(section_text: str) -> int:
    count = 0
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ")):
            count += 1
            continue
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] == ". ":
            count += 1
    return count


def _count_tool_pairs(messages: list[Any]) -> int:
    count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            count += 1
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool" or part.get("tool_name"):
                count += 1
    return count
