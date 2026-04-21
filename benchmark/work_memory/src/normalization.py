from __future__ import annotations

import re

_SURROUNDING_PUNCTUATION = " \t\r\n\"'`.,:;!?()[]{}<>"
_SENTENCE_BOUNDARY_RE = re.compile(r"(?:[.!?;。！？]+|\n+)")
_ASCII_WORD_CHARS = "0-9a-z"


def normalize_text(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(_SURROUNDING_PUNCTUATION)


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    pattern = compile_phrase_pattern(phrase)
    if pattern is None:
        return False
    return bool(pattern.search(normalized_text))


def compile_phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase:
        return None

    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    if normalized_phrase[0].isalnum():
        pattern = rf"(?<![{_ASCII_WORD_CHARS}]){pattern}"
    if normalized_phrase[-1].isalnum():
        pattern = rf"{pattern}(?![{_ASCII_WORD_CHARS}])"
    return re.compile(pattern)


def split_sections(wm_text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    for raw_line in wm_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections.setdefault(current_section, [])
            continue
        if current_section is not None:
            sections[current_section].append(line)

    return {
        section_name: "\n".join(lines).strip()
        for section_name, lines in sections.items()
    }


def split_normalized_sentences(value: str) -> list[str]:
    normalized = normalize_text(value)
    if not normalized:
        return []
    return [
        segment.strip()
        for segment in _SENTENCE_BOUNDARY_RE.split(normalized)
        if segment.strip()
    ]
