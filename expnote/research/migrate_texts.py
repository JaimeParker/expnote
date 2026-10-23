"""Pure text transforms of the P4 vault migration (no filesystem, no registry).

Everything here is deterministic and idempotent: running a transform on its own output
changes nothing, which is what lets every migration step be re-run as a no-op.
"""

from __future__ import annotations

import dataclasses
import re

from expnote.research.decisions import map_decision_status
from expnote.research.edits import (
    edit_frontmatter,
    frontmatter_end,
    key_of,
    yaml_list,
    yaml_quote,
)
from expnote.research.errors import ResearchError
from expnote.research.frontmatter import scalar_is_quoted
from expnote.research.model import Note
from expnote.research.profile import Profile
from expnote.research.textkit import (
    WIKILINK_RE,
    fold,
    is_separator_row,
    link_stem,
    link_target,
    split_table_row,
)

# ---- schema bump and decision ids ---------------------------------------------


def set_schema_first(text: str, schema: str = "3") -> str:
    """`schema: N` as the first frontmatter key (replaced in place when present)."""
    lines = text.split("\n")
    end = frontmatter_end(lines)
    if end is None:
        raise ResearchError("NO_FRONTMATTER", "the note has no frontmatter")
    for index in range(1, end):
        if key_of(lines[index]) == "schema":
            if lines[index].split(":", 1)[1].strip() == schema:
                return text
            lines[index] = f"schema: {schema}"
            return "\n".join(lines)
    lines.insert(1, f"schema: {schema}")
    return "\n".join(lines)


def canonical_decision_id(value: object, prefix: str) -> str | None:
    """`115`, `#115`, `<prefix>-115` -> `<prefix>-0115`; None for anything else."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(rf"#?(\d+)|{re.escape(prefix)}-(\d+)", value.strip())
    if not match:
        return None
    return f"{prefix}-{int(match.group(1) or match.group(2)):04d}"


def _all_quoted(node) -> bool:
    items = node.value if isinstance(node.value, list) else [node]
    return bool(items) and all(scalar_is_quoted(item) for item in items)


def canonicalise_decision_fields(
    text: str, note: Note, prefix: str, scalar_keys: tuple[str, ...], list_keys: tuple
) -> str:
    """Rewrite typed decision references to quoted canonical ids (others untouched)."""
    sets: list[tuple[str, str]] = []
    for key in scalar_keys:
        value = note.fm.get(key)
        canon = canonical_decision_id(value, prefix)
        node = note.fm_nodes.get(key)
        if canon is None or node is None:
            continue
        if value != canon or not _all_quoted(node):
            sets.append((key, yaml_quote(canon)))
    for key in list_keys:
        value = note.fm.get(key)
        node = note.fm_nodes.get(key)
        if not isinstance(value, list) or node is None:
            continue
        canon_list = [canonical_decision_id(v, prefix) for v in value]
        if any(c is None for c in canon_list):
            continue
        if canon_list != value or not _all_quoted(node):
            sets.append((key, yaml_list(canon_list)))
    return edit_frontmatter(text, sets) if sets else text


# ---- status block ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Excision:
    text: str
    removed: tuple[str, ...]  # the lines taken out, in order


def excise_marker_block(text: str, start: str, end: str) -> Excision:
    """Cut the generated block (marker lines included) and the blank lines before it.

    Everything else keeps its bytes. A start marker without an end marker leaves the
    text untouched (the caller reports it).
    """
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip() == start), None)
    if first is None:
        return Excision(text, ())
    last = next((i for i in range(first, len(lines)) if lines[i].strip() == end), None)
    if last is None:
        return Excision(text, ())
    cut = first
    while cut > 0 and not lines[cut - 1].strip():
        cut -= 1
    removed = tuple(lines[cut : last + 1])
    return Excision("\n".join(lines[:cut] + lines[last + 1 :]), removed)


def assert_pure_excision(before: str, after: str, removed: tuple[str, ...]) -> None:
    """The body-preservation assertion: `after` is `before` minus one contiguous cut."""
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    cut = len(removed)
    if len(before_lines) - len(after_lines) != cut:
        raise AssertionError("excision removed a different number of lines")
    for k in range(len(after_lines) + 1):
        if (
            before_lines[:k] == after_lines[:k]
            and before_lines[k : k + cut] == list(removed)
            and before_lines[k + cut :] == after_lines[k:]
        ):
            return
    raise AssertionError("the text outside the removed block changed")


# ---- wikilink rewriting (never inside code) -----------------------------------------

_LINK_OR_CODE_RE = re.compile(
    r"(?P<code>^(?P<fence>```|~~~).*?^(?P=fence)[^\n]*$|<!--.*?-->|`[^`\n]*`)"
    r"|(?P<link>!?\[\[[^\[\]\n]+?\]\])",
    re.M | re.S,
)
_SUFFIX_SPLIT_RE = re.compile(r"^([^#|\\]*)(.*)$", re.S)
_ALIAS_SPLIT_RE = re.compile(r"\\?\|")


def split_link(raw: str) -> tuple[str, str, str | None]:
    """(target text, suffix from the first `#` or `|`, alias or None) of a link."""
    match = _SUFFIX_SPLIT_RE.match(raw)
    target, suffix = match.group(1), match.group(2)
    alias = None
    if "|" in suffix:
        alias = _ALIAS_SPLIT_RE.split(suffix, maxsplit=1)[1]
    return target, suffix, alias


def rewrite_links(text: str, mapper) -> tuple[str, int]:
    """Replace wikilinks outside code. `mapper(is_embed, raw)` -> new text or None."""
    count = 0

    def sub(match: re.Match) -> str:
        nonlocal count
        if match.group("code") is not None:
            return match.group(0)
        bang, raw = WIKILINK_RE.fullmatch(match.group("link")).groups()
        replacement = mapper(bang == "!", raw)
        if replacement is None:
            return match.group(0)
        count += 1
        return replacement

    return _LINK_OR_CODE_RE.sub(sub, text), count


def retarget_links(text: str, renames: dict[str, str]) -> tuple[str, int]:
    """Point links whose folded stem is a key of `renames` at the new stem."""

    def mapper(is_embed: bool, raw: str) -> str | None:
        target = link_target(raw)
        new = renames.get(fold(link_stem(target)))
        if new is None:
            return None
        old_text, suffix, _ = split_link(raw)
        return f"{'!' if is_embed else ''}[[{new}{suffix}]]"

    return rewrite_links(text, mapper)


def runs_to_plain(text: str, stems: set[str]) -> tuple[str, int]:
    """`[[run]]` -> `` `run` `` (an alias stays in front): the target will not exist."""

    def mapper(is_embed: bool, raw: str) -> str | None:
        if is_embed:
            return None
        target = link_target(raw)
        if fold(link_stem(target)) not in stems:
            return None
        _, _, alias = split_link(raw)
        stem = link_stem(target)
        return f"{alias.strip()} (`{stem}`)" if alias and alias.strip() else f"`{stem}`"

    return rewrite_links(text, mapper)


# ---- names -------------------------------------------------------------------------

def result_id_slug(stem: str) -> str:
    base = re.sub(r"^RES--", "", stem)
    base = re.sub(r'[\s/\\:*?"<>|#^\[\]]+', "-", base).strip("-")
    return f"RES-{base}"


def straighten_quotes(name: str) -> str:
    """ASCII double quotes (not valid in Windows names) -> alternating CJK quotes."""
    out, opening = [], True
    for ch in name:
        if ch == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


OPEN_BRACKETS = "(" + chr(0xFF08)  # ASCII and fullwidth parentheses
CLOSE_BRACKETS = ")" + chr(0xFF09)


def first_clause(s: str, delimiters: str, limit: int = 90) -> str:
    """First clause of a decision cell (port of vault.py `first_clause`).

    Stops at the first delimiter at bracket depth 0, never inside `[[...]]` or `$...$`.
    """
    depth = 0
    in_link = False
    in_math = False
    i = 0
    while i < len(s):
        if s.startswith("[[", i):
            in_link = True
            i += 2
            continue
        if in_link and s.startswith("]]", i):
            in_link = False
            i += 2
            continue
        ch = s[i]
        if ch == "$" and not in_link:
            in_math = not in_math
        elif not in_link and not in_math:
            if ch in OPEN_BRACKETS:
                depth += 1
            elif ch in CLOSE_BRACKETS and depth > 0:
                depth -= 1
            elif depth == 0 and ch in delimiters:
                return s[:i].strip()
        if i >= limit and not in_link and not in_math and depth == 0:
            return s[:i].rstrip() + "…"
        i += 1
    return s.strip()


# ---- legacy decision table <-> decision files ----------------------------------------

CELL_FIELDS = ("number", "date", "decision", "reason", "lands", "status", "layer")
PIPE_ESCAPED_RE = re.compile(r"(?<!\\)\|")


def unescape_cell(cell: str) -> str:
    return cell.replace("\\|", "|")


def escape_cell_text(text: str) -> str:
    return PIPE_ESCAPED_RE.sub(r"\\|", text)


@dataclasses.dataclass(frozen=True)
class LegacyRow:
    number: int
    cells: dict[str, str]  # raw (still pipe-escaped) cells by CELL_FIELDS name


def parse_legacy_rows(text: str, columns: dict[str, str]) -> list[LegacyRow]:
    """All numbered rows of the legacy decision table with every cell kept raw."""
    index: dict[str, int] | None = None
    header_len = 0
    rows: list[LegacyRow] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        cells = split_table_row(line)
        if cells is None:
            continue
        if index is None:
            if all(columns[f] in cells for f in CELL_FIELDS):
                index = {f: cells.index(columns[f]) for f in CELL_FIELDS}
                header_len = len(cells)
            continue
        if is_separator_row(cells):
            continue
        number_cell = cells[index["number"]] if len(cells) > index["number"] else ""
        if len(cells) != header_len or not re.fullmatch(r"\d+", number_cell):
            continue
        rows.append(
            LegacyRow(int(number_cell), {f: cells[index[f]] for f in CELL_FIELDS})
        )
    return rows



def migration_words(profile: Profile) -> dict:
    """The profile's `migration` section: the words the one-shot migration looks for."""
    section = profile.raw.get("migration")
    return section if isinstance(section, dict) else {}


def word_list(profile: Profile, key: str) -> list[str]:
    value = migration_words(profile).get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def supersedes_pattern(profile: Profile) -> re.Pattern:
    words = "|".join(re.escape(w) for w in word_list(profile, "supersedes_words"))
    separators = migration_words(profile).get("number_separators")
    chars = re.escape(separators) if isinstance(separators, str) else ""
    if not words:
        return re.compile(r"(?!)")
    return re.compile(rf"(?:{words})\s*((?:#\d+[{chars}\s]*)+)")

_SUPERSEDED_BY_RE = re.compile(r"superseded by\s*#(\d+)")
_STATUS_WORD_RE = re.compile(r"^(proposed|confirmed|superseded|rejected)\b")
_DATE_IN_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclasses.dataclass
class DecisionImport:
    number: int
    decision_id: str
    record: dict  # the decision-store columns of the row
    problems: list[str]


def _iso(date: str) -> bool:
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is not None


def decision_record(
    row: LegacyRow,
    profile: Profile,
    plan_ids: list[str],
    *,
    title_limit: int = 90,
    predecessors: tuple[int, ...] = (),
) -> DecisionImport:
    """The decision-store row for one legacy table row, and what looked doubtful."""
    problems: list[str] = []
    prefix = profile.decision_id_prefix
    decision_id = f"{prefix}-{row.number:04d}"
    cell = {f: unescape_cell(row.cells[f]) for f in CELL_FIELDS}
    delimiters = ""
    section = profile.raw.get("decisions")
    if isinstance(section, dict) and isinstance(section.get("title_delimiters"), str):
        delimiters = section["title_delimiters"]
    title = " ".join(
        first_clause(cell["decision"], delimiters, title_limit).split()
    ) or decision_id
    mapped = map_decision_status(
        cell["status"],
        row.number,
        profile.decision_strict_from,
        profile.legacy_status_rules,
    )
    status = mapped.status
    if status is None:
        problems.append(f"status cell {cell['status']!r} does not start with a status")
        status = "confirmed"
    if not _iso(cell["date"]):
        problems.append(f"date cell {cell['date']!r} is not an ISO date")
    legacy = row.number < profile.decision_strict_from

    targets: list[str] = []
    for _, raw in WIKILINK_RE.findall(cell["lands"]):
        stem = link_stem(link_target(raw))
        if stem and f"[[{stem}]]" not in targets:
            targets.append(f"[[{stem}]]")
    scan = f"{cell['decision']}\n{cell['lands']}"
    plans = [p for p in plan_ids if re.search(re.escape(p) + r"(?![\w-])", scan)]
    supersedes: list[int] = []
    for found in supersedes_pattern(profile).findall(cell["decision"]):
        for number in re.findall(r"#(\d+)", found):
            if int(number) not in supersedes and int(number) != row.number:
                supersedes.append(int(number))
    mention_text = " ".join(cell[f] for f in ("decision", "reason", "lands", "status"))
    for number in predecessors:  # rows this one replaces and says so (`#N`)
        if f"#{number}" in mention_text and number not in supersedes:
            supersedes.append(number)
    successor = _SUPERSEDED_BY_RE.search(cell["status"])
    superseded_by = int(successor.group(1)) if successor else mapped.successor
    if status == "superseded" and superseded_by is None:
        problems.append("status superseded but no `superseded by #N` in the cell")

    confirmed_by = confirmed_on = None
    if status == "confirmed":
        if legacy:
            confirmed_by = "legacy"
        elif any(w in cell["status"] for w in word_list(profile, "user_words")):
            confirmed_by = "user"
        else:
            problems.append("non-legacy confirmed row: cannot tell who confirmed it")
        if not legacy:
            found = _DATE_IN_TEXT_RE.search(cell["status"])
            found = found or _DATE_IN_TEXT_RE.search(cell["date"])
            confirmed_on = found.group(0) if found else None
    stamp = f"{cell['date'] if _iso(cell['date']) else '1970-01-01'}T00:00:00Z"
    record = {
        "number": row.number,
        "id": decision_id,
        "title": title,
        "date": cell["date"],
        "status": status,
        "layer": cell["layer"],
        "decided": cell["decision"],
        "reason": cell["reason"],
        "options": "",
        "lands": cell["lands"],
        "plans": plans,
        "targets": targets,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "confirmed_by": confirmed_by,
        "confirmed_on": confirmed_on,
        "authorization": None,
        "legacy": legacy,
        "status_raw": cell["status"],
        "review": mapped.review,
        "review_by": None,
        "source": "migrated",
        "created_at": stamp,
        "updated_at": stamp,
        "version": 1,
    }
    return DecisionImport(row.number, decision_id, record, problems)


def build_decision_records(
    rows: list[LegacyRow], profile: Profile, plan_ids: list[str]
) -> list[DecisionImport]:
    replaced_by: dict[int, list[int]] = {}
    for row in rows:
        found = _SUPERSEDED_BY_RE.search(unescape_cell(row.cells["status"]))
        if found:
            replaced_by.setdefault(int(found.group(1)), []).append(row.number)
    return [
        decision_record(
            row, profile, plan_ids, predecessors=tuple(replaced_by.get(row.number, ()))
        )
        for row in rows
    ]


def cells_from_record(record: dict) -> dict[str, str]:
    """Regenerate the legacy cells (raw, pipe-escaped) from a decision-store row."""
    return {
        "number": str(record["number"]),
        "date": record["date"],
        "decision": escape_cell_text(record["decided"]),
        "reason": escape_cell_text(record["reason"]),
        "lands": escape_cell_text(record["lands"]),
        "status": escape_cell_text(record["status_raw"]),
        "layer": record["layer"],
    }


def roundtrip_mismatches(
    rows: list[LegacyRow], records: dict[int, dict]
) -> list[str]:
    """Cell-by-cell comparison; empty when the store regenerates the table exactly.

    The store keeps the table's text as it was imported (later steps rewrite vault
    notes, never decision text), so the cells are compared as they stand.
    """
    problems = []
    for row in rows:
        record = records.get(row.number)
        if record is None:
            problems.append(f"#{row.number}: not in the decision store")
            continue
        regenerated = cells_from_record(record)
        for field in CELL_FIELDS:
            if regenerated[field] != row.cells[field]:
                problems.append(f"#{row.number}: cell {field!r} differs")
    return problems


IMMUTABLE_IMPORT_COLUMNS = (
    "id",
    "title",
    "date",
    "layer",
    "decided",
    "reason",
    "options",
    "lands",
    "plans",
    "targets",
    "supersedes",
    "legacy",
    "status_raw",
    "review",
)


def import_differences(wanted: dict, have: dict) -> list[str]:
    """Immutable columns where a stored row is not what the table would import."""
    return [c for c in IMMUTABLE_IMPORT_COLUMNS if wanted[c] != have[c]]
