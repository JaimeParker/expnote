"""What the index extracts from one note: links, decision references, manual TODOs.

Pure functions of text and the profile; `index.py` stores their results.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import PurePosixPath

from expnote.research.frontmatter import import_yaml
from expnote.research.model import Note
from expnote.research.profile import Profile
from expnote.research.schemas import field_table
from expnote.research.textkit import (
    FENCE_RE,
    INLINE_CODE_RE,
    WIKILINK_RE,
    headings,
    link_stem,
    link_target,
    sha256_text,
)


def _mask_code(text: str) -> str:
    """Drop code spans/fences but keep their newlines so line numbers stay true."""

    def blank(match: re.Match) -> str:
        return "\n" * match.group().count("\n")

    return INLINE_CODE_RE.sub(blank, FENCE_RE.sub(blank, text))


@dataclasses.dataclass(frozen=True)
class LinkRecord:
    kind: str
    raw: str
    target: str
    target_stem: str
    line: int
    in_frontmatter: bool


def extract_links(text: str, frontmatter_len: int) -> list[LinkRecord]:
    masked = _mask_code(text)
    out = []
    for match in WIKILINK_RE.finditer(masked):
        target = link_target(match.group(2))
        out.append(
            LinkRecord(
                "embed" if match.group(1) else "link",
                match.group(2),
                target,
                link_stem(target),
                masked.count("\n", 0, match.start()) + 1,
                match.start() < frontmatter_len,
            )
        )
    return out


def quote_map(nodes: dict) -> dict:
    """Quoting style of each frontmatter value (what `SCHEMA.UNQUOTED` needs)."""
    yaml = import_yaml()

    def style(node) -> str:
        if isinstance(node, yaml.ScalarNode):
            return "q" if node.style in ('"', "'") else "u"
        return "x"

    out = {}
    for key, node in nodes.items():
        if isinstance(node, yaml.SequenceNode):
            out[key] = [style(item) for item in node.value]
        else:
            out[key] = style(node)
    return out


def nodes_from_quote_map(quotes: dict) -> dict:
    yaml = import_yaml()
    tag = yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG

    def scalar(code: str):
        if code == "x":
            return yaml.SequenceNode(tag, [])
        return yaml.ScalarNode(tag, "", style='"' if code == "q" else None)

    nodes = {}
    for key, code in quotes.items():
        if isinstance(code, list):
            nodes[key] = yaml.SequenceNode(
                yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
                [scalar(c) for c in code],
            )
        elif code == "x":
            nodes[key] = yaml.MappingNode(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, [(None, None)]
            )
        else:
            nodes[key] = scalar(code)
    return nodes


def first_h1(body: str) -> str | None:
    for _, level, title in headings(body):
        if level == 1:
            return title
    return None


def aliases_of(note: Note) -> list[str]:
    value = note.fm.get("aliases")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [a for a in value if isinstance(a, str) and a]


# ---- decision references and manual TODOs ----------------------------------------

TYPED_DECISION_KEYS = ("label_authorization",)


def typed_decision_keys() -> frozenset[str]:
    keys = set(TYPED_DECISION_KEYS)
    for entity in ("plan", "exec-plan", "evidence", "result", "decision"):
        for key, spec in (field_table(entity, "3") or {}).items():
            if spec.kind in ("decision-id", "decision-ids"):
                keys.add(key)
    for entity in ("plan", "exec-plan", "result"):
        for key in field_table(entity, "2") or {}:
            if "decision" in key:
                keys.add(key)
    return frozenset(keys)


def decision_hub_stem(profile: Profile) -> str | None:
    table = profile.legacy_decision_table
    if not table:
        return None
    return PurePosixPath(table).name.removesuffix(".md")


def decision_hub_pattern(profile: Profile) -> re.Pattern | None:
    """`[[hub]] #12, #13`: group `inner` is the link tail, group `nums` the numbers."""
    hub = decision_hub_stem(profile)
    if not hub:
        return None
    return re.compile(
        rf"\[\[{re.escape(hub)}(?P<inner>(?:[#|\\][^\]]*)?)\]\]\s*"
        r"(?P<nums>#\d+(?:\s*[\u3001\uff0c\uff1b,;]\s*#\d+)*)"
    )


def extract_decision_refs(
    note: Note, text: str, profile: Profile
) -> list[tuple[int, str, int]]:
    """(number, form, line): typed fields, id links and the legacy hub form."""
    refs: list[tuple[int, str, int]] = []
    prefix = re.escape(profile.decision_id_prefix)
    number_re = re.compile(rf"(?:#|{prefix}-)?(\d+)")
    lines = text.split("\n")
    for key in typed_decision_keys():
        value = note.fm.get(key)
        values = value if isinstance(value, list) else [value]
        line = next(
            (i + 1 for i, row in enumerate(lines) if row.startswith(f"{key}:")), 1
        )
        for item in values:
            if isinstance(item, str):
                found = number_re.fullmatch(item.strip().strip("[]\"'"))
                if found:
                    refs.append((int(found.group(1)), "field", line))
    masked = _mask_code(text)
    id_link = re.compile(rf"\[\[{prefix}-(\d+)", re.I)
    for match in id_link.finditer(masked):
        refs.append(
            (int(match.group(1)), "id-link", masked.count("\n", 0, match.start()) + 1)
        )
    hub_re = decision_hub_pattern(profile)
    if hub_re is not None:
        for match in hub_re.finditer(masked):
            line = masked.count("\n", 0, match.start()) + 1
            for number in re.findall(r"#(\d+)", match.group("nums")):
                refs.append((int(number), "hub", line))
    return refs


TODO_LINE_RE = re.compile(r"^- \[( |x|X)\] (.*)$")
TODO_FIELD_RE = re.compile(r"\[(\w+)::\s*([^\]]*)\]")


@dataclasses.dataclass(frozen=True)
class TodoRecord:
    todo_id: str
    synthetic: bool
    state: str  # open | done
    line: int
    line_sha: str
    text: str
    fields: dict


def parse_todo_lines(text: str) -> list[TodoRecord]:
    """Manual TODO lines (`- [ ] text [id:: T-0001]`); indented lines continue."""
    out: list[TodoRecord] = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        match = TODO_LINE_RE.match(lines[index].rstrip())
        index += 1
        if not match:
            continue
        line_no = index
        body = match.group(2)
        extra = []
        while (
            index < len(lines)
            and lines[index][:1] in (" ", "\t")
            and lines[index].strip()
        ):
            extra.append(lines[index].strip())
            index += 1
        fields = dict(TODO_FIELD_RE.findall(body))
        text_only = TODO_FIELD_RE.sub("", body).strip()
        if extra:
            text_only = " ".join([text_only, *extra]).strip()
        line_sha = sha256_text(lines[line_no - 1].rstrip())
        todo_id = fields.get("id") or f"T~{line_sha[:8]}"
        out.append(
            TodoRecord(
                todo_id,
                "id" not in fields,
                "done" if match.group(1) in "xX" else "open",
                line_no,
                line_sha,
                text_only,
                fields,
            )
        )
    return out
