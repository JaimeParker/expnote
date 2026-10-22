"""Data model: notes, findings, decisions and deviations."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from expnote.research.frontmatter import (
    FrontmatterError,
    load_frontmatter,
    split_frontmatter,
)
from expnote.research.textkit import fold, iter_links, link_stem, nfc, normalize_text

LEVELS = ("FAIL", "WARN", "INFO")


@dataclasses.dataclass(frozen=True)
class Finding:
    level: str
    code: str
    where: str
    message: str
    data: dict = dataclasses.field(default_factory=dict, compare=False, hash=False)

    @property
    def area(self) -> str:
        return self.code.split(".", 1)[0]

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.level, self.code, self.where, self.message)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "area": self.area,
            "where": self.where,
            "message": self.message,
            "data": self.data,
        }


class FindingSink:
    """Collects findings, dropping exact duplicates (like a set)."""

    def __init__(self):
        self._seen: set[tuple[str, str, str, str]] = set()
        self.items: list[Finding] = []

    def add(self, level: str, code: str, where: str, message: str, **data) -> None:
        finding = Finding(level, code, where, message, data)
        if finding.key in self._seen:
            return
        self._seen.add(finding.key)
        self.items.append(finding)


@dataclasses.dataclass
class Note:
    path: Path
    rel: str  # relative to the project directory, as stored on disk
    text: str
    body: str
    fm: dict
    fm_nodes: dict
    fm_error: str | None
    fm_error_kind: str | None
    has_fm: bool

    @property
    def stem(self) -> str:
        return nfc(self.path.stem)

    @property
    def cstem(self) -> str:
        return fold(self.stem)

    @property
    def type(self) -> str | None:
        value = self.fm.get("type")
        return value if isinstance(value, str) else None

    @property
    def schema(self) -> str | None:
        value = self.fm.get("schema")
        return value if isinstance(value, str) else None

    def get(self, key: str, default=None):
        return self.fm.get(key, default)

    def is_true(self, key: str) -> bool:
        return self.fm.get(key) == "true"

    def link_field(self, key: str) -> str | None:
        """Folded stem of a single-link frontmatter field."""
        value = self.fm.get(key)
        if isinstance(value, str):
            links = iter_links(value)
            if links:
                return fold(link_stem(links[0][1]))
        return None


def parse_note(path: Path, rel: str, text: str) -> Note:
    text = normalize_text(text)
    try:
        fm_text, body = split_frontmatter(text)
        if fm_text is None:
            return Note(path, rel, text, body, {}, {}, None, None, False)
        fm, nodes = load_frontmatter(fm_text)
        return Note(path, rel, text, body, fm, nodes, None, None, True)
    except FrontmatterError as error:
        return Note(path, rel, text, text, {}, {}, str(error), error.kind, True)


@dataclasses.dataclass
class DeviationRow:
    did: str
    date: str
    experiment: str
    content: str
    impact: str
    disposition: str
    cards: str
    status: str | None = None
    found: str | None = None
    closed_marker: str = ""

    @property
    def closed(self) -> bool:
        if self.status is not None:
            return self.status == "closed"
        return bool(self.closed_marker) and self.closed_marker in self.disposition


@dataclasses.dataclass
class Decision:
    """One decision, from a legacy table row or the decision store."""

    number: int
    where: str  # "#N"
    source: str  # "table" | "db"
    status: str | None
    status_raw: str
    legacy: bool
    review: str | None = None
    successor: int | None = None
    decision_id: str | None = None
    layer: str = ""
    date: str = ""
    text: str = ""  # searched for citations (links) and back-references
    decision_text: str = ""
    lands_text: str = ""
    plans: tuple[str, ...] = ()
    supersedes: tuple[int, ...] = ()
    title: str = ""  # store rows only; table rows derive it from decision_text
    reason_text: str = ""
    options_text: str = ""
    lands_raw: str = ""  # the lands text alone (lands_text also holds the targets)
    targets: tuple[str, ...] = ()
    confirmed_by: str | None = None
    confirmed_on: str | None = None
    authorization: str | None = None
    review_by: str | None = None
    version: int = 0
    recorded_via: str = ""  # store rows: migrated | cli | chat | gui

    @property
    def body_text(self) -> str:
        """The prose of the decision (what the number-in-prose heuristic reads)."""
        if self.source == "table":
            return self.text
        parts = (self.decision_text, self.reason_text)
        parts += (self.options_text, self.lands_raw)
        return "\n\n".join(p for p in parts if p)

    def cites_stem(self, cstem: str) -> bool:
        return any(fold(link_stem(t)) == cstem for _, t in iter_links(self.text))

    def refers_back_to(self, number: int) -> bool:
        if self.source == "table":
            return f"#{number}" in self.text
        return number in self.supersedes
