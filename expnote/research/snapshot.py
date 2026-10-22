"""VaultSnapshot: everything the checks read, gathered in one scan.

Checks are pure functions of a snapshot, so an index-backed snapshot can replace
`scan_vault` without touching `checks.py`.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path, PurePosixPath

from expnote.research.decisions import (
    decision_concerns_plan,
    parse_decision_table,
    parse_deviation_log,
    stored_decisions,
)
from expnote.research.model import Decision, DeviationRow, Note, parse_note
from expnote.research.profile import Profile
from expnote.research.registry import ProjectContext
from expnote.research.schemas import ENTITY_TYPES
from expnote.research.textkit import (
    fold,
    glob_to_regex,
    is_attachment_name,
    nfc,
)


@dataclasses.dataclass
class VaultSnapshot:
    profile: Profile
    vault_root: Path
    project_dir: Path
    images_rel: str  # images directory relative to the vault root (POSIX)
    notes: list[Note]  # every project note, templates included
    registry: list[Note]  # notes subject to lifecycle and link checks
    md_index: dict[str, list[str]]  # folded stem -> vault-relative paths
    attach_index: dict[str, list[str]]  # folded file name -> vault-relative paths
    rel_noext: set[str]
    rel_attach: set[str]
    alias_index: dict[str, list[str]]
    images: list[str] | None  # file names in the images dir; None if it is missing
    decisions: dict[int, Decision]
    decision_problems: list[tuple[str, str, str]]  # (code, where, message)
    deviations: dict[str, DeviationRow]
    _by_cstem: dict[str, Note] = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        for note in self.registry:
            self._by_cstem.setdefault(note.cstem, note)

    # ---- name resolution ----
    def resolve(self, target: str) -> list[str]:
        if not target:
            return ["<self>"]
        key = fold(target)
        if "/" in key:
            hits = [p for p in self.rel_noext if p == key or p.endswith("/" + key)]
            hits += [p for p in self.rel_attach if p == key or p.endswith("/" + key)]
            return sorted(hits)
        if is_attachment_name(key, self.profile.attachment_exts):
            return self.attach_index.get(key, [])
        hits = self.md_index.get(key, []) or self.alias_index.get(key, [])
        if not hits and self.is_stored_decision(target):
            return ["<decision>"]
        return hits

    def is_stored_decision(self, target: str) -> bool:
        """`[[PREFIX-NNNN]]` naming a decision of the store (no note carries it)."""
        match = re.fullmatch(
            rf"{re.escape(self.profile.decision_id_prefix)}-(\d+)",
            target.strip(),
            re.I,
        )
        found = self.decisions.get(int(match.group(1))) if match else None
        return found is not None and found.source == "db"

    # ---- registry lookups ----
    def note(self, cstem: str) -> Note | None:
        return self._by_cstem.get(cstem)

    def is_archived(self, note: Note) -> bool:
        return bool(set(Path(note.rel).parts) & self.profile.archive_dir_names)

    def plan_page(self, plan_id: str) -> Note | None:
        hits = [n for n in self.registry if n.type == "plan" and n.get("id") == plan_id]
        hits.sort(key=self.is_archived)  # prefer a non-archived page
        return hits[0] if hits else None

    def plan_members(self, plan_id: str) -> list[Note]:
        page = self.plan_page(plan_id)
        if page is None:
            return []
        return [
            n
            for n in self.registry
            if n is not page and n.link_field("plan") == page.cstem
        ]

    def decisions_for_plan(self, plan_id: str) -> list[tuple[Decision, list[str]]]:
        page = self.plan_page(plan_id)
        stems = {m.cstem for m in self.plan_members(plan_id)}
        if page is not None:
            stems.add(page.cstem)
        pattern = re.compile(re.escape(plan_id) + r"(?![\w-])")
        out = []
        for number in sorted(self.decisions):
            decision = self.decisions[number]
            why = decision_concerns_plan(decision, plan_id, pattern, stems)
            if why:
                out.append((decision, why))
        return out

    def deviations_for_plan(self, plan_id: str) -> list[DeviationRow]:
        pattern = re.compile(re.escape(plan_id) + r"(?![\w-])")
        rows = sorted(self.deviations.values(), key=lambda d: _deviation_order(d.did))
        return [
            d for d in rows if pattern.search(d.experiment) or pattern.search(d.cards)
        ]

    def entity_type(self, note: Note) -> str | None:
        """Entity type after the profile's legacy type map; None for other notes."""
        mapped = self.profile.legacy_type_map.get(note.type or "", note.type)
        return mapped if mapped in ENTITY_TYPES else None


def _deviation_order(did: str) -> tuple[int, str]:
    digits = re.search(r"\d+", did)
    return (int(digits.group()) if digits else 0, did)


def _walk_names(vault_root: Path, profile: Profile):
    md_index: dict[str, list[str]] = {}
    attach_index: dict[str, list[str]] = {}
    rel_noext: set[str] = set()
    rel_attach: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [
            d for d in dirnames if d not in profile.skip_dirs and not d.startswith(".")
        ]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = nfc(full.relative_to(vault_root).as_posix())
            name = nfc(filename)
            if name.lower().endswith(".md"):
                md_index.setdefault(name[:-3].casefold(), []).append(rel)
                rel_noext.add(rel[:-3].casefold())
            else:
                attach_index.setdefault(name.casefold(), []).append(rel)
                rel_attach.add(rel.casefold())
    return md_index, attach_index, rel_noext, rel_attach


def _read_notes(project_dir: Path, profile: Profile) -> list[Note]:
    notes = []
    for path in sorted(project_dir.rglob("*.md")):
        rel = path.relative_to(project_dir)
        if any(p in profile.skip_dirs or p.startswith(".") for p in rel.parts[:-1]):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        notes.append(parse_note(path, rel.as_posix(), text))
    return notes


def _registry(notes: list[Note], profile: Profile) -> list[Note]:
    ignore = [glob_to_regex(g) for g in profile.ignore_globs]
    out = []
    for note in notes:
        if set(PurePosixPath(note.rel).parts) & profile.template_dirs:
            continue
        if any(rx.match(note.rel) for rx in ignore):
            continue
        out.append(note)
    return out


def _alias_index(notes: list[Note]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for note in notes:
        aliases = note.fm.get("aliases")
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias:
                    index.setdefault(fold(alias), []).append(note.rel)
    return index


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def build_decisions_and_deviations(
    profile: Profile,
    project_id: str,
    table_text: str | None,
    deviation_text: str | None,
) -> tuple[dict[int, Decision], list[tuple[str, str, str]], dict[str, DeviationRow]]:
    """Pure part of the scan: decisions (store rows over table rows) and deviations.

    The decision store is the truth once it exists; the legacy table only fills the
    numbers the store does not hold, so an unmigrated vault keeps working.
    """
    decisions: dict[int, Decision] = {}
    problems: list[tuple[str, str, str]] = []
    table_name = profile.legacy_decision_table
    if table_text is not None and table_name:
        rows, table_problems = parse_decision_table(table_text, profile, table_name)
        problems += table_problems
        for row in rows:
            if row.number in decisions:
                problems.append(
                    ("DEC.NUMBER_DUPLICATE", table_name, f"#{row.number} is duplicated")
                )
            decisions[row.number] = row
    decisions.update(stored_decisions(project_id) or {})

    deviations: dict[str, DeviationRow] = {}
    if deviation_text is not None:
        for row in parse_deviation_log(deviation_text, profile):
            deviations[row.did] = row
    return decisions, problems, deviations


def scan_vault(context: ProjectContext) -> VaultSnapshot:
    resolved, profile = context.resolved, context.profile
    vault_root, project_dir = resolved.vault_root, resolved.project_dir
    notes = _read_notes(project_dir, profile)
    registry = _registry(notes, profile)
    md_index, attach_index, rel_noext, rel_attach = _walk_names(vault_root, profile)

    table_name = profile.legacy_decision_table
    table_text = _read_text(project_dir / table_name) if table_name else None
    deviation_text = _read_text(project_dir / profile.deviation_register)
    decisions, problems, deviations = build_decisions_and_deviations(
        profile, context.entry.id, table_text, deviation_text
    )

    images_dir = resolved.images_dir
    images = (
        sorted(p.name for p in images_dir.iterdir() if p.is_file())
        if images_dir.is_dir()
        else None
    )
    try:
        images_rel = images_dir.relative_to(vault_root).as_posix()
    except ValueError:
        images_rel = images_dir.as_posix()
    return VaultSnapshot(
        profile=profile,
        vault_root=vault_root,
        project_dir=project_dir,
        images_rel=images_rel,
        notes=notes,
        registry=registry,
        md_index=md_index,
        attach_index=attach_index,
        rel_noext=rel_noext,
        rel_attach=rel_attach,
        alias_index=_alias_index(notes),
        images=images,
        decisions=decisions,
        decision_problems=problems,
        deviations=deviations,
    )
