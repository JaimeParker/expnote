"""Pure text edits on Markdown notes: frontmatter keys, table cells, task lines.

Nothing here touches the filesystem. `writeback` decodes a file into LF text, hands
it to one of these functions and re-encodes the result with the file's own BOM and
line ending, so every edit changes only the addressed line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from expnote.research.errors import ResearchError
from expnote.research.extract import TODO_FIELD_RE, TODO_LINE_RE, parse_todo_lines
from expnote.research.textkit import is_separator_row, sha256_text, split_table_row

BOM = b"\xef\xbb\xbf"
KEY_NAME = r"[A-Za-z_][\w-]*"
# A top-level key, bare or quoted ("key": / 'key':); the group is the unquoted name.
KEY_LINE_RE = re.compile(rf"^(?:\"({KEY_NAME})\"|'({KEY_NAME})'|({KEY_NAME}))\s*:")
SIMPLE_SCALAR_RE = re.compile(r"[0-9A-Za-z_.-]+")
UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")
DONE_FIELD_RE = re.compile(r"\s*\[done::\s*[^\]]*\]")
TASK_ID_RE = re.compile(r"\[id::\s*([^\]]*)\]")


# ---- bytes <-> LF text -------------------------------------------------------------


@dataclass(frozen=True)
class FileFormat:
    """What a decode must give back at encode time."""

    bom: bool = False
    crlf: bool = False


def decode_note(data: bytes) -> tuple[str, FileFormat]:
    """(LF text, format). Mixed line endings cannot be restored line by line."""
    bom = data.startswith(BOM)
    try:
        text = (data[len(BOM) :] if bom else data).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ResearchError(
            "NOT_UTF8", f"file is not valid UTF-8: {error}", exit_code=2
        ) from None
    crlf_count = text.count("\r\n")
    lf_only = text.count("\n") - crlf_count
    if crlf_count and lf_only:
        raise ResearchError(
            "MIXED_LINE_ENDINGS",
            "file mixes CRLF and LF line endings; an edit could not keep them",
            exit_code=2,
            remediation="normalise the line endings of this note in your editor",
        )
    return text.replace("\r\n", "\n"), FileFormat(bom, bool(crlf_count))


NEW_FILE_FORMAT = FileFormat()


def encode_note(text: str, fmt: FileFormat = NEW_FILE_FORMAT) -> bytes:
    body = text.replace("\n", "\r\n") if fmt.crlf else text
    return (BOM if fmt.bom else b"") + body.encode("utf-8")


# ---- YAML literals -----------------------------------------------------------


def yaml_quote(value: str) -> str:
    """A JSON string is a valid YAML double-quoted scalar."""
    _single_line(value)
    return json.dumps(value, ensure_ascii=False)


def yaml_scalar(value: str, *, quote: bool = False) -> str:
    """Bare when it is a plain token, else quoted (ids and links pass quote=True)."""
    if not quote and SIMPLE_SCALAR_RE.fullmatch(value):
        return value
    return yaml_quote(value)


def yaml_list(values) -> str:
    return "[" + ", ".join(yaml_quote(v) for v in values) + "]"


def _single_line(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ResearchError(
            "USAGE_MULTILINE_VALUE",
            "this value must be a single line",
            exit_code=2,
        )


# ---- frontmatter -------------------------------------------------------------


def frontmatter_end(lines: list[str]) -> int | None:
    """Index of the closing `---` line, None when the note has no frontmatter."""
    if not lines or lines[0].rstrip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() in ("---", "..."):
            return index
    raise ResearchError(
        "FRONTMATTER_UNCLOSED",
        "frontmatter is not closed (missing closing ---)",
        exit_code=2,
    )


def body_line_offset(text: str) -> int:
    """Number of lines before the body (0 without frontmatter)."""
    end = frontmatter_end(text.split("\n"))
    return 0 if end is None else end + 1


def _require_frontmatter(lines: list[str]) -> int:
    end = frontmatter_end(lines)
    if end is None:
        raise ResearchError(
            "NO_FRONTMATTER", "the note has no frontmatter", exit_code=2
        )
    return end


def key_of(line: str) -> str | None:
    """Unquoted name of the top-level key a frontmatter line starts, else None."""
    match = KEY_LINE_RE.match(line)
    return next(g for g in match.groups() if g) if match else None


def _blocks(fm_lines: list[str]) -> list[tuple[str | None, list[str]]]:
    blocks: list[tuple[str | None, list[str]]] = []
    for line in fm_lines:
        name = key_of(line)
        if name is not None:
            blocks.append((name, [line]))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            blocks.append((None, [line]))
    return blocks


def frontmatter_block(text: str, key: str) -> list[str] | None:
    """The lines of one top-level key (value continuation included)."""
    lines = text.split("\n")
    end = frontmatter_end(lines)
    if end is None:
        return None
    for name, block in _blocks(lines[1:end]):
        if name == key:
            return block
    return None


def edit_frontmatter(
    text: str,
    set_items: list[tuple[str, str]] = (),  # type: ignore[assignment]
    remove: set[str] = frozenset(),  # type: ignore[assignment]
) -> str:
    """Set (value is a ready YAML literal) or remove top-level keys, textually.

    A key's whole block (a flow list on one line or a multi-line value) is replaced;
    everything else keeps its bytes, order and formatting. New keys go last.
    """
    lines = text.split("\n")
    end = _require_frontmatter(lines)
    wanted = dict(set_items)
    for key in [*wanted, *remove]:
        if not re.fullmatch(KEY_NAME, key):
            raise ResearchError(
                "USAGE_BAD_KEY", f"{key!r} is not a frontmatter key", exit_code=2
            )
    out: list[str] = []
    for name, block in _blocks(lines[1:end]):
        if name in remove:
            continue
        if name in wanted:
            trailing = 0
            while trailing < len(block) - 1 and not block[-1 - trailing].strip():
                trailing += 1
            out.append(f"{name}: {wanted.pop(name)}")
            out.extend(block[len(block) - trailing :] if trailing else [])
        else:
            out.extend(block)
    insert_at = len(out)
    while insert_at > 0 and not out[insert_at - 1].strip():
        insert_at -= 1
    out[insert_at:insert_at] = [f"{key}: {value}" for key, value in wanted.items()]
    return "\n".join([lines[0], *out, *lines[end:]])


# ---- sections and tables -----------------------------------------------------


def _code_and_comment_mask(lines: list[str]) -> list[bool]:
    """True for lines inside a fenced block or an HTML comment (not table material)."""
    mask = [False] * len(lines)
    fence = None
    in_comment = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if fence is not None:
            mask[index] = True
            if stripped.startswith(fence):
                fence = None
            continue
        if in_comment:
            mask[index] = True
            if "-->" in line:
                in_comment = False
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            mask[index] = True
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
            mask[index] = True
    return mask


@dataclass(frozen=True)
class TableLines:
    header_line: int
    last_line: int  # index of the last row (the separator when there are no rows)
    header: list[str]

    def row_lines(self) -> range:
        return range(self.header_line + 2, self.last_line + 1)


def find_tables(lines: list[str], start: int = 0, end: int | None = None):
    end = len(lines) if end is None else end
    mask = _code_and_comment_mask(lines)
    tables: list[TableLines] = []
    index = start
    while index < end:
        if mask[index] or not lines[index].strip().startswith("|"):
            index += 1
            continue
        run = index
        while run < end and not mask[run] and lines[run].strip().startswith("|"):
            run += 1
        sep = split_table_row(lines[index + 1]) if index + 1 < run else None
        if sep is not None and is_separator_row(sep):
            tables.append(
                TableLines(index, run - 1, split_table_row(lines[index]) or [])
            )
        index = run
    return tables


def cell_spans(line: str) -> list[tuple[int, int]]:
    """(start, end) of each cell's raw text; needs the leading and trailing pipe."""
    stripped_end = len(line.rstrip())
    first = len(line) - len(line.lstrip())
    pipes = [m.start() for m in UNESCAPED_PIPE_RE.finditer(line, first, stripped_end)]
    if len(pipes) < 2 or pipes[0] != first or pipes[-1] != stripped_end - 1:
        return []
    return list(zip((p + 1 for p in pipes[:-1]), pipes[1:], strict=True))


def escape_cell(value: str) -> str:
    _single_line(value)
    return UNESCAPED_PIPE_RE.sub(r"\\|", value.strip())


def replace_cell(line: str, index: int, value: str) -> str:
    spans = cell_spans(line)
    if index >= len(spans):
        raise ResearchError(
            "ADDRESS_NOT_FOUND",
            f"table row has no column {index + 1}",
            exit_code=3,
        )
    start, end = spans[index]
    raw = line[start:end]
    lead = raw[: len(raw) - len(raw.lstrip())] or " "
    trail = raw[len(raw.rstrip()) :] or " "
    cell = escape_cell(value)
    padded = f"{lead[:1]}{cell}{trail[:1]}" if cell else f"{lead}{trail}"
    return line[:start] + padded + line[end:]


def format_row(cells: list[str]) -> str:
    return "| " + " | ".join(escape_cell(c) for c in cells) + " |"


def find_row(lines: list[str], table: TableLines, key_column: int, key: str):
    """Index of the first row whose key cell equals `key` (whitespace-trimmed)."""
    for index in table.row_lines():
        cells = split_table_row(lines[index]) or []
        if len(cells) > key_column and cells[key_column].strip() == key.strip():
            return index
    return None


# ---- task lines --------------------------------------------------------------


def task_ids(text: str) -> list[str]:
    return [t.todo_id for t in parse_todo_lines(text) if not t.synthetic]


def next_task_id(text: str, prefix: str = "T-", width: int = 4) -> str:
    numbers = [
        int(m.group(1))
        for ident in task_ids(text)
        if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", ident))
    ]
    return f"{prefix}{max(numbers, default=0) + 1:0{width}d}"


def find_task_line(lines: list[str], ident: str) -> int | None:
    """Line index of a task by `[id::]`, or by `T~<sha8>` for a line without one."""
    for index, line in enumerate(lines):
        match = TODO_LINE_RE.match(line.rstrip())
        if not match:
            continue
        found = TASK_ID_RE.search(match.group(2))
        if found:
            if found.group(1).strip() == ident:
                return index
        elif ident == f"T~{sha256_text(line.rstrip())[:8]}":
            return index
    return None


def format_task_line(text: str, fields: list[tuple[str, str]]) -> str:
    _single_line(text)
    if not text.strip():
        raise ResearchError("USAGE_EMPTY_TEXT", "the task text is empty", exit_code=2)
    parts = [f"- [ ] {text.strip()}"]
    for key, value in fields:
        _single_line(value)
        if "]" in value:
            raise ResearchError(
                "USAGE_BAD_VALUE", f"field {key!r} cannot contain ']'", exit_code=2
            )
        parts.append(f"[{key}:: {value}]")
    return " ".join(parts)


def mark_task(line: str, *, done: bool, today: str, new_id: str | None) -> str:
    """Flip `[ ]`/`[x]`, set or drop `[done::]`, optionally append a real id."""
    stripped = line.rstrip()
    match = TODO_LINE_RE.match(stripped)
    assert match is not None
    body = DONE_FIELD_RE.sub("", match.group(2))
    if new_id is not None and not TASK_ID_RE.search(body):
        body = f"{body} [id:: {new_id}]"
    if done:
        body = f"{body} [done:: {today}]"
    return f"- [{'x' if done else ' '}] {body}"


def edit_task_line(
    line: str,
    *,
    text: str | None = None,
    set_fields: dict[str, str] | None = None,
    clear_fields: tuple[str, ...] = (),
) -> str:
    """Rewrite a task line: replace the text and/or set/clear inline fields.

    Existing fields stay where they are (their values replaced in place); new ones
    are appended at the end. The checkbox state is kept.
    """
    match = TODO_LINE_RE.match(line.rstrip())
    assert match is not None
    body = match.group(2)
    for key, value in (set_fields or {}).items():
        field_re = re.compile(rf"\[{key}::\s*[^\]]*\]")
        replacement = f"[{key}:: {value}]"
        if field_re.search(body):
            body = field_re.sub(lambda _m, new=replacement: new, body, count=1)
        else:
            body = f"{body} {replacement}"
    for key in clear_fields:
        body = re.sub(rf"\s*\[{key}::\s*[^\]]*\]", "", body)
    if text is not None:
        fields = " ".join(m.group(0) for m in TODO_FIELD_RE.finditer(body))
        body = f"{text.strip()} {fields}".rstrip()
    return f"- [{match.group(1)}] {body}"


def adopt_task_line(line: str, new_id: str) -> str:
    return f"{line.rstrip()} [id:: {new_id}]"
