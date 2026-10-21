"""Pure text helpers: normalisation, code stripping, wikilinks, sections, tables."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.M | re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
PIPE_SPLIT_RE = re.compile(r"(?<!\\)\|")
RESERVED_NAME_CHARS_RE = re.compile(r"[#^\[\]|]")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(text: str) -> str:
    """Comparison key for names: NFC then casefold."""
    return nfc(text).casefold()


def normalize_text(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_for_hash(text: str) -> str:
    lines = [nfc(line).rstrip() for line in normalize_text(text).split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def hash_section(text: str) -> str:
    return sha256_text(normalize_for_hash(text))


def strip_code(text: str, *, comments: bool = False) -> str:
    """Remove fenced blocks and inline code; optionally HTML comments too."""
    out = INLINE_CODE_RE.sub("", FENCE_RE.sub("", text))
    return HTML_COMMENT_RE.sub("", out) if comments else out


# ---- wikilinks --------------------------------------------------------------


def link_target(raw: str) -> str:
    """Wikilink inner text -> target without alias, #heading/^block or .md.

    Handles the escaped pipe used inside tables (``[[a\\|b]]``).
    """
    target = re.split(r"\\?\|", raw, maxsplit=1)[0]
    target = target.split("#", 1)[0].strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    return nfc(target)


def iter_links(text: str, *, comments: bool = False) -> list[tuple[bool, str]]:
    """[(is_embed, target)] for every wikilink outside code."""
    stripped = strip_code(text, comments=comments)
    return [
        (bang == "!", link_target(raw)) for bang, raw in WIKILINK_RE.findall(stripped)
    ]


def link_stem(target: str) -> str:
    """Last path segment of a link target (path-suffix links keep only the name)."""
    return target.rsplit("/", 1)[-1]


def is_attachment_name(name: str, extensions: frozenset[str] | set[str]) -> bool:
    if "." not in name:
        return False
    return name.rsplit(".", 1)[1].lower() in extensions


def reserved_name_chars(name: str) -> list[str]:
    return sorted(set(RESERVED_NAME_CHARS_RE.findall(name)))


# ---- headings and sections ---------------------------------------------------


def headings(body: str) -> list[tuple[int, int, str]]:
    """[(line_idx, level, title)] outside fenced code."""
    found = []
    in_fence = False
    for index, line in enumerate(body.split("\n")):
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            found.append((index, len(match.group(1)), match.group(2)))
    return found


def find_section(body: str, predicate) -> tuple[int, int, str] | None:
    """(start, end, title): lines[start] is the heading, lines[start+1:end] its body."""
    lines = body.split("\n")
    heads = headings(body)
    for position, (index, level, title) in enumerate(heads):
        if predicate(title):
            end = len(lines)
            for index2, level2, _ in heads[position + 1 :]:
                if level2 <= level:
                    end = index2
                    break
            return index, end, title
    return None


def section_text(body: str, predicate) -> str | None:
    section = find_section(body, predicate)
    if section is None:
        return None
    lines = body.split("\n")
    return "\n".join(lines[section[0] + 1 : section[1]])


# ---- tables -------------------------------------------------------------------


def split_table_row(line: str) -> list[str] | None:
    """Cells of a Markdown table row (escaped pipes kept); None if not a row."""
    if not line.strip().startswith("|"):
        return None
    cells = PIPE_SPLIT_RE.split(line.strip())
    if len(cells) >= 2:
        cells = cells[1:-1]
    return [cell.strip() for cell in cells]


def is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r"[-: ]*", cell) for cell in cells)


def _is_table_start(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    cells = split_table_row(lines[1])
    return bool(cells) and is_separator_row(cells)


def markdown_tables(text: str) -> list[list[str]]:
    """Every table as its raw lines (header, separator, rows)."""
    tables: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip().startswith("|"):
            current.append(line)
        else:
            if _is_table_start(current):
                tables.append(current)
            current = []
    if _is_table_start(current):
        tables.append(current)
    return tables


@dataclass(frozen=True)
class TableRow:
    cells: list[str]
    by_header: dict[str, str]


def table_rows_by_header(table_lines: list[str]) -> list[TableRow]:
    """Rows of one table keyed by header name (separator row skipped)."""
    header = split_table_row(table_lines[0]) or []
    rows = []
    for line in table_lines[2:]:
        cells = split_table_row(line) or []
        rows.append(TableRow(cells, dict(zip(header, cells, strict=False))))
    return rows


# ---- paths --------------------------------------------------------------------


def glob_to_regex(pattern: str) -> re.Pattern:
    """Glob with ``**`` (any depth), ``*`` and ``?`` over POSIX relative paths."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i):
            out.append("(?:/.*)?")
            i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")
