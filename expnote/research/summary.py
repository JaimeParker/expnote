"""Mechanical one-line summaries: copied text, truncated, never interpreted."""

from __future__ import annotations

import re

from expnote.research.textkit import headings

ONE_LINE_LIMIT = 160
SENTENCE_END = re.compile(
    r"(?<=[.!?\u3002\uff01\uff1f])(?:\s|$)|(?<=[\u3002\uff01\uff1f])"
)


def truncate(text: str, limit: int = ONE_LINE_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first_sentence(body: str, limit: int = ONE_LINE_LIMIT) -> str:
    """First sentence of the first paragraph after the H1 (or from the top)."""
    lines = body.split("\n")
    start = 0
    for index, level, _ in headings(body):
        if level == 1:
            start = index + 1
            break
    paragraph: list[str] = []
    in_fence = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        skip = (
            in_fence
            or stripped.startswith(("#", "|", "<!--", "---"))
            or (not stripped and not paragraph)
        )
        if skip:
            continue
        if not stripped:
            break
        paragraph.append(stripped)
    text = re.sub(r"^(?:[-*+>]\s+)+", "", " ".join(paragraph))
    match = SENTENCE_END.search(text)
    if match:
        text = (
            text[: match.start()].rstrip() + text[match.start() : match.end()].strip()
        )
    return truncate(text, limit)
