"""`plan adopt` and `plan next-id`: make a hand-written draft a standard plan.

The draft's body is never touched. Only its frontmatter is completed from the plan
template, then the note moves to the plans folder under the `names.plan` pattern and
the wikilinks that point at it follow (the same link rewriter as the migration's
rename step). The three parts run as one `WritePlan`, so they are audited together.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import PurePosixPath

from expnote.research import newnote
from expnote.research.edits import edit_frontmatter, frontmatter_block, yaml_quote
from expnote.research.entities import notes_of
from expnote.research.errors import ResearchError
from expnote.research.frontmatter import load_frontmatter, split_frontmatter
from expnote.research.migrate_texts import retarget_links, set_schema_first
from expnote.research.model import Note, parse_note
from expnote.research.schemas import PLAN_STATUS_V3
from expnote.research.textkit import fold, headings, nfc
from expnote.research.view import ProjectView
from expnote.research.writeback import FileEdit
from expnote.research.writes import WritePlan

ID_NUMBER_RE = re.compile(r"^(\D*?)(\d+)")
NUMBERING_RE = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+")
# A leading "<id>:" or "<id> - " in a heading; the id is dropped from a derived title.
TITLE_ID_PREFIX_RE = re.compile(r"^(\S+?)(?:\s*[:\uff1a]|\s+[-\u2013\u2014])\s*(.*)$")
NEXT_ID_RULE = (
    "the most common prefix before the first number of the existing plan ids, "
    "its highest number plus one, zero-padded to the widest number seen with "
    "that prefix; null when there is no plan yet to continue from, or when the "
    "result does not match ids.plan_regex"
)


# ---- ids ---------------------------------------------------------------------


def is_standard_plan(view: ProjectView, note: Note) -> bool:
    """A `type: plan` note whose id exists and fits the profile's plan id regex."""
    value = note.get("id")
    if note.type != "plan" or not isinstance(value, str) or not value:
        return False
    regex = view.profile.plan_id_regex
    return not regex or re.fullmatch(regex, value) is not None


def next_plan_id(view: ProjectView) -> str | None:
    """The next free plan id (see NEXT_ID_RULE); None when the regex rejects it."""
    taken = {
        n.get("id") for n in notes_of(view, "plan") if isinstance(n.get("id"), str)
    }
    numbered = [m.groups() for i in taken if (m := ID_NUMBER_RE.match(i))]
    if not numbered:
        return None
    counts = Counter(prefix for prefix, _ in numbered)
    top = max(
        counts,
        key=lambda p: (counts[p], max(int(d) for q, d in numbered if q == p)),
    )
    digits = [d for prefix, d in numbered if prefix == top]
    number, width = max(int(d) for d in digits), max(len(d) for d in digits)
    candidate = f"{top}{number + 1:0{width}d}"
    while candidate in taken:
        number += 1
        candidate = f"{top}{number + 1:0{width}d}"
    regex = view.profile.plan_id_regex
    return candidate if not regex or re.fullmatch(regex, candidate) else None


# ---- finding drafts ------------------------------------------------------------


def unadopted_plan_drafts(view: ProjectView) -> list[Note]:
    """Notes in the plans folder that are not standard plans (archives excluded).

    The registry already leaves out template folders and the profile's ignore globs.
    """
    folder = PurePosixPath(newnote.folders_for(view.profile)["plan"]).parts
    drafts = []
    for note in view.snapshot.registry:
        parts = PurePosixPath(note.rel).parts
        if parts[: len(folder)] != folder or len(parts) == len(folder):
            continue
        if note.fm_error or view.snapshot.is_archived(note):
            continue
        untyped = not note.has_fm or note.type is None
        if untyped or (note.type == "plan" and not is_standard_plan(view, note)):
            drafts.append(note)
    return sorted(drafts, key=lambda n: n.rel)


def suggested_title(view: ProjectView, note: Note, plan_id: str | None = None) -> str:
    """The note's `title`, else its first H1 (minus a plan id), else its stem."""
    title = note.get("title")
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())
    h1 = next((t for _, level, t in headings(note.body) if level == 1), "")
    prefix = TITLE_ID_PREFIX_RE.match(h1)
    if prefix:
        token, rest = prefix.groups()
        regex = view.profile.plan_id_regex
        is_id = token == plan_id or (
            regex
            and any(c.isdigit() for c in token)
            and re.fullmatch(regex, token) is not None
        )
        if is_id:
            h1 = rest
    return " ".join(h1.split()) or note.stem


def _file_name_part(title: str) -> str:
    """The title as part of a file name: reserved characters become spaces."""
    cleaned = " ".join(newnote.BAD_NAME_CHARS.sub(" ", title).split())
    return cleaned.rstrip(". ")


# ---- resolving the NOTE argument -------------------------------------------------


def resolve_draft(view: ProjectView, ref: str) -> Note:
    """A project note by absolute, vault-relative or project-relative path, or stem."""
    text = nfc(ref.strip().replace("\\", "/"))
    notes = view.snapshot.notes
    hits = [
        n
        for n in notes
        if text in (nfc(n.rel), nfc(view.vault_rel(n)), nfc(n.path.as_posix()))
    ]
    if not hits and "/" not in text:
        stem = text[:-3] if text.endswith(".md") else text
        hits = [n for n in notes if n.stem == stem or n.cstem == fold(stem)]
    if not hits:
        raise ResearchError(
            "NOT_FOUND",
            f"no note {ref!r} in project {view.project_id}",
            exit_code=3,
            remediation="pass a path inside the project folder, or the note's name",
        )
    if len(hits) > 1:
        raise ResearchError(
            "AMBIGUOUS",
            f"{ref!r} matches {len(hits)} notes",
            exit_code=3,
            remediation="pass the path instead of the name",
            details={"paths": [view.vault_rel(h) for h in hits]},
        )
    return hits[0]


def _check_adoptable(view: ProjectView, note: Note) -> None:
    template_dirs = view.profile.template_dirs
    if set(PurePosixPath(note.rel).parts[:-1]) & template_dirs:
        raise ResearchError(
            "USAGE_NOT_ADOPTABLE",
            f"{view.vault_rel(note)} lies in a template folder",
            exit_code=2,
        )
    if note.fm_error:
        raise ResearchError(
            "FRONTMATTER_INVALID",
            f"{view.vault_rel(note)} has unreadable frontmatter ({note.fm_error})",
            exit_code=2,
            remediation="repair the frontmatter in your editor, then adopt it",
        )
    if note.type is not None and note.type != "plan":
        raise ResearchError(
            "USAGE_NOT_ADOPTABLE",
            f"{view.vault_rel(note)} has type {note.type!r}; only notes without a "
            "type, or with type plan, can be adopted as plans",
            exit_code=2,
            remediation="remove or change its type first",
        )


# ---- the frontmatter ---------------------------------------------------------------


def _literal(rendered: str, key: str) -> str:
    """The template's own YAML literal for a key (a block value keeps its lines)."""
    block = frontmatter_block(rendered, key) or [f"{key}:"]
    first = block[0].split(":", 1)[1].strip()
    return "\n".join([first, *block[1:]])


def _status_items(note: Note) -> list[tuple[str, str]]:
    status = note.fm.get("status")
    if status is None or (isinstance(status, str) and status in PLAN_STATUS_V3):
        return [("status", "draft")] if status is None else []
    items = [("status", "draft")]
    original = status if isinstance(status, str) else str(status)
    original = " ".join(original.split())
    if original and "legacy_status" not in note.fm:
        items.append(("legacy_status", yaml_quote(original)))
    return items


def _front_items(
    note: Note, rendered: str, template_fm: dict, plan_id: str, title: str
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if note.fm.get("type") != "plan":
        items.append(("type", "plan"))
    if note.fm.get("id") != plan_id:
        items.append(("id", yaml_quote(plan_id)))
    if note.fm.get("title") != title:
        items.append(("title", yaml_quote(title)))
    items += _status_items(note)
    placed = {key for key, _ in items} | {"schema", "legacy_status"}
    items += [
        (key, _literal(rendered, key))
        for key in template_fm
        if key not in note.fm and key not in placed
    ]
    return items


def _adopted_text(
    note: Note, rendered: str, template_fm: dict, plan_id: str, title: str
) -> str:
    if not note.has_fm:
        front, _ = split_frontmatter(rendered)
        return f"---\n{front}\n---\n{note.text}"
    text = note.text
    if note.fm.get("schema") != "3":
        text = set_schema_first(text, "3")
    items = _front_items(note, rendered, template_fm, plan_id, title)
    return edit_frontmatter(text, items)


def _frontmatter_changes(before: dict, after: dict) -> list[dict]:
    changes = []
    for key, value in after.items():
        if key not in before:
            changes.append(
                {"key": key, "action": "added", "before": None, "after": value}
            )
        elif before[key] != value:
            changes.append(
                {"key": key, "action": "changed", "before": before[key], "after": value}
            )
    return changes


def _section_key(title: str) -> str:
    return fold(" ".join(NUMBERING_RE.sub("", title.strip()).split()))


def missing_sections(template_body: str, draft_body: str) -> list[str]:
    """Template headings (below the title) that the draft has no heading for."""
    present = {_section_key(t) for _, _, t in headings(draft_body)}
    return [
        title
        for _, level, title in headings(template_body)
        if level >= 2 and _section_key(title) not in present
    ]


# ---- the link rewrite --------------------------------------------------------------


def _linking_notes(view: ProjectView, note: Note) -> list[tuple[str, str, str]]:
    """(vault-relative path, text, sha256) of every other note that links to `note`."""
    rows = view.conn.execute(
        "SELECT DISTINCT f.rel, n.fm_raw, n.body, f.sha256 FROM links l "
        "JOIN files f ON f.id = l.src_file_id JOIN notes n ON n.file_id = f.id "
        "WHERE f.is_conflict = 0 AND f.rel != ? AND l.resolved_file_id = "
        "(SELECT id FROM files WHERE rel = ?) ORDER BY f.rel",
        (view.vault_rel(note), view.vault_rel(note)),
    )
    return [(r["rel"], r["fm_raw"] + r["body"], r["sha256"]) for r in rows]


# ---- the planner ---------------------------------------------------------------------


def _check_new_id(view: ProjectView, note: Note, plan_id: str) -> None:
    regex = view.profile.plan_id_regex
    if not plan_id or plan_id != plan_id.strip() or (
        regex and re.fullmatch(regex, plan_id) is None
    ):
        raise ResearchError(
            "USAGE_BAD_ID",
            f"{plan_id!r} is not a valid plan id"
            + (f" (profile ids.plan_regex: {regex})" if regex else ""),
            exit_code=2,
            remediation="expnote plan next-id",
        )
    existing = view.snapshot.plan_page(plan_id)
    if existing is not None and existing is not note:
        raise ResearchError(
            "ID_EXISTS",
            f"a plan with id {plan_id!r} already exists",
            exit_code=5,
            details={"path": view.vault_rel(existing)},
        )


def _frontmatter_plan(
    view: ProjectView,
    note: Note,
    plan_id: str,
    title: str,
    expected_sha256: str | None,
) -> tuple[list[FileEdit], list[dict], dict, list[str]]:
    """(edit, warnings, frontmatter after, missing sections) of the draft's own text."""
    subs = newnote._default_subs(
        view,
        id=plan_id,
        title=title,
        plan_id=plan_id,
        plan="",
        project=view.project_id,
    )
    rendered, left = newnote.render_template(view, "plan", subs)
    front, template_body = split_frontmatter(rendered)
    template_fm, _ = load_frontmatter(front or "")
    new_text = _adopted_text(note, rendered, template_fm, plan_id, title)
    after = parse_note(note.path, note.rel, new_text)
    if after.fm_error or after.body != note.body:
        raise ResearchError(
            "ADOPT_INTERNAL",
            "the adopted text would not keep the body byte for byte; nothing written",
            exit_code=6,
        )

    warnings = newnote._warnings(left, "plan")
    status = after.fm.get("status")
    if status != "draft":
        warnings.append(
            {
                "code": "ADOPT_STATUS_KEPT",
                "message": f"status {status!r} is kept; the lifecycle gates of that "
                "status apply (check may FAIL until their decisions and notes exist)",
            }
        )
    edits = [
        FileEdit(
            note.path,
            "adopt",
            "frontmatter:plan",
            transform=lambda text: _adopted_text(
                parse_note(note.path, note.rel, text),
                rendered,
                template_fm,
                plan_id,
                title,
            ),
            expected_sha256=expected_sha256 or view.file_sha256(note.rel),
        )
    ]
    return edits, warnings, after.fm, missing_sections(template_body, note.body)


def plan_plan_adopt(
    view: ProjectView,
    note_ref: str,
    *,
    plan_id: str,
    title: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    note = resolve_draft(view, note_ref)
    _check_adoptable(view, note)
    standard = is_standard_plan(view, note)
    if standard and note.get("id") != plan_id:
        raise ResearchError(
            "PLAN_ALREADY_STANDARD",
            f"{view.vault_rel(note)} is already the plan {note.get('id')!r}",
            exit_code=5,
            remediation="adopt only drafts; a plan keeps its id",
        )
    if not standard:
        _check_new_id(view, note, plan_id)

    if standard:  # an interrupted adopt: only the move is left, so keep its title
        title = suggested_title(view, note, plan_id)
    else:
        title = " ".join((title or "").split()) or suggested_title(view, note, plan_id)
    stem = newnote.check_name(
        newnote.format_name(
            newnote.names_for(view.profile)["plan"],
            id=plan_id,
            title=_file_name_part(title),
        )
    )
    target = newnote._note_path(view, "plan", stem)
    moved = target != note.path
    if standard and not moved:
        data = {
            "adopted": False,
            "alreadyStandard": True,
            "moved": False,
            "id": plan_id,
            "path": view.vault_rel(note),
        }
        return WritePlan([], data)
    if moved:
        if os.path.lexists(target):
            raise ResearchError(
                "TARGET_EXISTS",
                f"{target} already exists; this command never overwrites",
                exit_code=5,
                details={"path": str(target)},
            )
        own = nfc(view.vault_rel(note))
        others = [
            p for p in view.snapshot.md_index.get(fold(stem), []) if nfc(p) != own
        ]
        if others:
            raise ResearchError(
                "NAME_COLLISION",
                f"a note named {stem!r} already exists in the vault "
                "(names are unique, ignoring case)",
                exit_code=5,
                details={"paths": others},
            )

    if standard:
        edits, warnings, after_fm, missing = [], [], note.fm, []
    else:
        edits, warnings, after_fm, missing = _frontmatter_plan(
            view, note, plan_id, title, expected_sha256
        )

    link_edits = link_files = 0
    if moved and stem != note.stem:
        if len(view.snapshot.md_index.get(fold(note.stem), [])) > 1:
            warnings.append(
                {
                    "code": "ADOPT_LINKS_AMBIGUOUS",
                    "message": f"another note is also named {note.stem!r}; links to "
                    "that name were left alone",
                }
            )
        else:
            renames = {fold(note.stem): stem}
            for rel, text, sha in _linking_notes(view, note):
                count = retarget_links(text, renames)[1]
                if not count:
                    continue
                link_edits += count
                link_files += 1
                edits.append(
                    FileEdit(
                        view.context.resolved.vault_root / rel,
                        "retarget-links",
                        f"links:{note.stem}",
                        transform=lambda t, r=renames: retarget_links(t, r)[0],
                        expected_sha256=sha,
                    )
                )
            if retarget_links(note.body, renames)[1]:
                warnings.append(
                    {
                        "code": "ADOPT_SELF_LINKS",
                        "message": "the body links to its own old name; the body is "
                        "never edited, so those links need a manual fix",
                    }
                )
    new_rel = (
        target.relative_to(view.context.resolved.vault_root).as_posix()
        if moved
        else view.vault_rel(note)
    )
    data = {
        "adopted": not standard,
        "alreadyStandard": standard,
        "id": plan_id,
        "title": title,
        "from": view.vault_rel(note),
        "path": new_rel,
        "moved": moved,
        "frontmatterChanges": _frontmatter_changes(note.fm, after_fm),
        "linkEdits": link_edits,
        "linkFiles": link_files,
        "missingSections": missing,
    }
    renames_plan = [(note.path, target)] if moved else []
    return WritePlan(edits, data, warnings=warnings, renames=renames_plan)
