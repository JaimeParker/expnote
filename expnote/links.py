from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable
from urllib.parse import quote

_PROTECTED_RE = re.compile(
    r"(```.*?```|~~~.*?~~~|`[^`\n]+`|\[[^\]]+\]\([^)]+\)|https?://\S+)",
    re.DOTALL,
)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def render_obsidian_run_links(text: str, active_run_ids: Iterable[str]) -> str:
    return _render_run_links(
        text,
        active_run_ids,
        active_wikilink=lambda run_id: f"[[{run_id}]]",
        active_bare=lambda run_id: f"[[{run_id}]]",
        inactive_wikilink=lambda run_id: run_id,
    )


def render_html_run_links(text: str, active_run_ids: Iterable[str]) -> str:
    return _render_run_links(
        text,
        active_run_ids,
        active_wikilink=_html_run_link,
        active_bare=_html_run_link,
        inactive_wikilink=html.escape,
    )


def _render_run_links(
    text: str,
    active_run_ids: Iterable[str],
    *,
    active_wikilink: Callable[[str], str],
    active_bare: Callable[[str], str],
    inactive_wikilink: Callable[[str], str],
) -> str:
    active_ids = {run_id for run_id in active_run_ids if run_id}
    if not text or not active_ids:
        return _WIKILINK_RE.sub(lambda match: inactive_wikilink(match.group(1)), text)

    parts = _split_protected(text)
    return "".join(
        part
        if protected
        else _render_unprotected(
            part,
            active_ids,
            active_wikilink=active_wikilink,
            active_bare=active_bare,
            inactive_wikilink=inactive_wikilink,
        )
        for part, protected in parts
    )


def _split_protected(text: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    pos = 0
    for match in _PROTECTED_RE.finditer(text):
        if match.start() > pos:
            parts.append((text[pos : match.start()], False))
        parts.append((match.group(0), True))
        pos = match.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts


def _render_unprotected(
    text: str,
    active_ids: set[str],
    *,
    active_wikilink: Callable[[str], str],
    active_bare: Callable[[str], str],
    inactive_wikilink: Callable[[str], str],
) -> str:
    chunks: list[tuple[str, bool]] = []
    pos = 0
    for match in _WIKILINK_RE.finditer(text):
        if match.start() > pos:
            chunks.append((text[pos : match.start()], False))
        run_id = match.group(1)
        renderer = active_wikilink if run_id in active_ids else inactive_wikilink
        chunks.append((renderer(run_id), True))
        pos = match.end()
    if pos < len(text):
        chunks.append((text[pos:], False))
    return "".join(
        chunk if protected else _render_bare_run_links(chunk, active_ids, active_bare)
        for chunk, protected in chunks
    )


def _render_bare_run_links(
    text: str,
    active_ids: set[str],
    active_bare: Callable[[str], str],
) -> str:
    bare_ids = {run_id for run_id in active_ids if len(run_id) >= 4}
    if not bare_ids:
        return text
    pattern = re.compile(
        r"(?<![A-Za-z0-9._/#-])("
        + "|".join(
            re.escape(run_id) for run_id in sorted(bare_ids, key=len, reverse=True)
        )
        + r")(?![A-Za-z0-9._/#-])"
    )
    return pattern.sub(lambda match: active_bare(match.group(1)), text)


def _html_run_link(run_id: str) -> str:
    escaped = html.escape(run_id)
    href = "#/run/" + quote(run_id, safe="")
    return f'<a href="{href}" data-run-link="{escaped}">{escaped}</a>'
