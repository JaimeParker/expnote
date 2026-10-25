"""`new plan|exec|ev|res|profile`: notes created from vault templates or built-ins.

A project's own templates (profile `templates.files`) win; without them a small
built-in English skeleton is used, so a freshly initialised vault works at once.
Whatever the source, the result is schema 3 with the template-doc block and any
forbidden generated-block marker removed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from expnote.research import entities
from expnote.research.edits import edit_frontmatter, yaml_quote
from expnote.research.errors import ResearchError
from expnote.research.frontmatter import FrontmatterError, split_frontmatter
from expnote.research.snapshot import earlier_cycle_links
from expnote.research.textkit import fold, normalize_text
from expnote.research.view import ProjectView
from expnote.research.writeback import FileEdit
from expnote.research.writes import WritePlan, written_date

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
BAD_NAME_CHARS = re.compile(r'[/\\:*?"<>|#^\[\]\x00-\x1f]')
# Design 3.1: the suggested profile note name. Escapes keep module code free of
# project-language text; the name is a tool default, not a project constant.
PROFILE_NOTE_NAME = "expnote \u9879\u76ee\u914d\u7f6e.md"

DEFAULT_NAMES = {
    "plan": "{id}--{title}",
    "exec": "exec--{pid} {title}",
    "ev": "EV--{pid} {title}",
    "appendix": "appendix--{stem}",
    "res": "RES--{pid} {title}",
}
DEFAULT_FOLDERS = {
    "plan": "plans",
    "exec": "ops",
    "ev": "evidence",
    "appendix": "ops",
    "res": "results",
}
TEMPLATE_KINDS = ("plan", "exec", "ev", "appendix", "res")

BUILTIN_TEMPLATES = {
    "plan": """---
schema: 3
type: plan
id: "{{id}}"
title: "{{title}}"
status: draft
created: "{{date}}"
owner: ""
---
# {{id}}: {{title}}

## 1. Question

## 2. Scope and non-goals

## 3. What to test

## 4. How to read the results

## 5. Alternatives and prior evidence
""",
    "exec": """---
schema: 3
type: exec-plan
id: "{{id}}"
plan: "[[{{plan}}]]"
stage: "{{stage}}"
status: draft
created: "{{date}}"
servers: []
expnote_topic: ""
---
# Execution plan: {{plan_id}} {{title}}

Plan: [[{{plan}}]].

## 1. Stages

## 2. Materials and commands

## {{rules_heading}}

## 3. Risks and resources
""",
    "ev": """---
schema: 3
type: evidence
created: "{{date}}"
plan: "[[{{plan}}]]"
exec_plan: ""
question: ""
status: rendered
figures: []
runs: []
appendix: "[[{{appendix}}]]"
---
# {{ev}}

Numbers, run identity and provenance: see the appendix [[{{appendix}}]].

## Figures

## Endpoint table

## {{label_heading}}

| Arm | Value |
|---|---|
| ... |  |

## {{observation_heading}}
""",
    "appendix": """---
schema: 3
type: evidence-appendix
created: "{{date}}"
parent: "[[{{ev}}]]"
plan: "[[{{plan}}]]"
runs: []
expnote_snapshot: ""
generated_by: ""
---
# {{appendix}}

Evidence page: [[{{ev}}]].

## Run identity

## Tables

## Checks
""",
    "res": """---
schema: 3
type: result
id: "RES-{{plan_id}}"
plan: "[[{{plan}}]]"
status: draft
evidence: []
decisions: []
reviewed_by: ""
created: "{{date}}"
---
# RES--{{plan_id}} {{title}}

## 1. Question and summary

## 2. Pre-registered verdicts

## 3. Exploratory analysis

## 4. Scope and limits

## 5. Deviations
""",
}


def _section(profile, name: str) -> dict:
    value = profile.raw.get(name)
    return value if isinstance(value, dict) else {}


def names_for(profile) -> dict[str, str]:
    return {**DEFAULT_NAMES, **_section(profile, "names")}


def folders_for(profile) -> dict[str, str]:
    return {**DEFAULT_FOLDERS, **_section(profile, "folders")}


def format_name(pattern: str, **parts: str) -> str:
    try:
        return pattern.format(**parts)
    except (KeyError, IndexError):
        raise ResearchError(
            "PROFILE_INVALID",
            f"profile `names` pattern {pattern!r} uses an unknown placeholder",
            exit_code=6,
        ) from None


def check_name(stem: str) -> str:
    if not stem.strip() or BAD_NAME_CHARS.search(stem):
        raise ResearchError(
            "USAGE_BAD_NAME",
            f"{stem!r} is not a usable note name (no path or reserved characters)",
            exit_code=2,
            remediation='avoid / \\ : * ? " < > | # ^ [ ]',
        )
    return stem.strip()


def _template_text(view: ProjectView, kind: str) -> str:
    profile = view.profile
    files = _section(profile, "templates").get("files")
    name = files.get(kind) if isinstance(files, dict) else None
    if not name:
        return BUILTIN_TEMPLATES[kind]
    directory = _section(profile, "templates").get("dir") or "templates"
    path = view.context.resolved.project_dir / directory / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BUILTIN_TEMPLATES[kind]


def _strip_doc_block(text: str, markers: tuple[str, ...]) -> str:
    if len(markers) != 2 or markers[0] not in text or markers[1] not in text:
        return text
    start = text.index(markers[0])
    end = text.index(markers[1]) + len(markers[1])
    return text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")


def _strip_marker_lines(text: str, markers: tuple[str, ...]) -> str:
    if not markers:
        return text
    kept = [ln for ln in text.split("\n") if not any(m in ln for m in markers)]
    return "\n".join(kept)


def render_template(
    view: ProjectView, kind: str, subs: dict[str, str]
) -> tuple[str, list[str]]:
    """(note text, placeholders left unfilled)."""
    profile = view.profile
    markers = tuple(_section(profile, "templates").get("doc_markers") or ())
    text = normalize_text(_template_text(view, kind))
    text = _strip_doc_block(text, tuple(markers))
    text = _strip_marker_lines(text, profile.forbidden_markers)
    try:
        front, body = split_frontmatter(text)
    except FrontmatterError as error:
        raise ResearchError(
            "TEMPLATE_INVALID", f"template for {kind}: {error}", exit_code=6
        ) from None
    if front is None:
        raise ResearchError(
            "TEMPLATE_INVALID",
            f"the template for {kind} has no frontmatter",
            exit_code=6,
        )

    def fill(part: str, escape: bool) -> str:
        def sub(match: re.Match) -> str:
            value = subs.get(match.group(1))
            if value is None:
                return match.group(0)
            return json.dumps(value, ensure_ascii=False)[1:-1] if escape else value

        return PLACEHOLDER_RE.sub(sub, part)

    text = f"---\n{fill(front, True)}\n---\n{fill(body, False)}"
    text = edit_frontmatter(text, [("schema", "3")])
    left = sorted(set(PLACEHOLDER_RE.findall(text)))
    return text.rstrip("\n") + "\n", left


def _default_subs(view: ProjectView, **extra: str) -> dict[str, str]:
    profile = view.profile
    return {
        "date": written_date(view),
        "rules_heading": profile.heading_rules,
        "label_heading": profile.heading_labels[0],
        "observation_heading": profile.heading_observation,
        **extra,
    }


def _unique_stem(view: ProjectView, stem: str) -> None:
    if view.snapshot.md_index.get(fold(stem)):
        raise ResearchError(
            "NAME_COLLISION",
            f"a note named {stem!r} already exists in the vault "
            "(names are unique, ignoring case)",
            exit_code=5,
            details={"paths": view.snapshot.md_index[fold(stem)]},
        )


def _note_path(view: ProjectView, kind: str, stem: str) -> Path:
    folder = folders_for(view.profile)[kind]
    return view.context.resolved.project_dir / folder / f"{stem}.md"


def _created(view: ProjectView, paths: list[Path]) -> list[dict]:
    root = view.context.resolved.vault_root
    return [{"path": p.relative_to(root).as_posix(), "absPath": str(p)} for p in paths]


def _warnings(left: list[str], kind: str) -> list[dict]:
    return [
        {
            "code": "TEMPLATE_PLACEHOLDER_UNFILLED",
            "message": f"{kind} template: {{{{{name}}}}} was not filled",
        }
        for name in left
    ]


def _plan_page(view: ProjectView, plan_id: str):
    return entities.find_plan(view, plan_id)


def _exec_stem(view: ProjectView, exec_ident: str) -> str:
    return entities.find_by_id(view, "exec-plan", exec_ident).stem


def plan_new_plan(view: ProjectView, *, plan_id: str, title: str) -> WritePlan:
    if view.snapshot.plan_page(plan_id) is not None:
        raise ResearchError(
            "ID_EXISTS", f"a plan with id {plan_id!r} already exists", exit_code=5
        )
    stem = check_name(
        format_name(names_for(view.profile)["plan"], id=plan_id, title=title)
    )
    _unique_stem(view, stem)
    text, left = render_template(
        view,
        "plan",
        _default_subs(view, id=plan_id, title=title, plan_id=plan_id, plan=""),
    )
    path = _note_path(view, "plan", stem)
    edit = FileEdit(path, "create", "new-file", new_text=text)
    data = {"created": _created(view, [path]), "leftoverPlaceholders": left}
    return WritePlan([edit], data, warnings=_warnings(left, "plan"))


def plan_new_exec(
    view: ProjectView,
    *,
    plan_id: str,
    stage: str,
    title: str,
    exec_id: str | None = None,
) -> WritePlan:
    page = _plan_page(view, plan_id)
    exec_id = exec_id or f"{plan_id}-{stage}"
    if any(n.get("id") == exec_id for n in entities.notes_of(view, "exec-plan")):
        raise ResearchError(
            "ID_EXISTS", f"an exec-plan with id {exec_id!r} already exists", exit_code=5
        )
    stem = check_name(
        format_name(
            names_for(view.profile)["exec"], pid=plan_id, title=title, id=exec_id
        )
    )
    _unique_stem(view, stem)
    text, left = render_template(
        view,
        "exec",
        _default_subs(
            view,
            id=exec_id,
            title=title,
            plan=page.stem,
            plan_id=plan_id,
            stage=stage,
        ),
    )
    path = _note_path(view, "exec", stem)
    edit = FileEdit(path, "create", "new-file", new_text=text)
    data = {
        "id": exec_id,
        "created": _created(view, [path]),
        "leftoverPlaceholders": left,
    }
    return WritePlan([edit], data, warnings=_warnings(left, "exec"))


def plan_new_ev(
    view: ProjectView,
    *,
    plan_id: str,
    title: str,
    exec_ident: str | None = None,
) -> WritePlan:
    profile = view.profile
    page = _plan_page(view, plan_id)
    names = names_for(profile)
    ev_stem = check_name(format_name(names["ev"], pid=plan_id, title=title))
    appendix_stem = check_name(format_name(names["appendix"], stem=ev_stem))
    _unique_stem(view, ev_stem)
    _unique_stem(view, appendix_stem)
    subs = _default_subs(
        view,
        title=title,
        plan=page.stem,
        plan_id=plan_id,
        ev=ev_stem,
        appendix=appendix_stem,
    )
    ev_text, left_ev = render_template(view, "ev", subs)
    if exec_ident:
        link = yaml_quote(f"[[{_exec_stem(view, exec_ident)}]]")
        ev_text = edit_frontmatter(ev_text, [("exec_plan", link)])
    appendix_text, left_app = render_template(view, "appendix", subs)
    ev_path = _note_path(view, "ev", ev_stem)
    appendix_path = _note_path(view, "appendix", appendix_stem)
    edits = [
        FileEdit(ev_path, "create", "new-file", new_text=ev_text),
        FileEdit(appendix_path, "create", "new-file", new_text=appendix_text),
    ]
    left = sorted({*left_ev, *left_app})
    data = {
        "created": _created(view, [ev_path, appendix_path]),
        "leftoverPlaceholders": left,
    }
    return WritePlan(edits, data, warnings=_warnings(left, "ev"))


CYCLE_ID_SUFFIX = re.compile(r"-cycle(\d+)$")


def _cycle_number(view: ProjectView, page) -> int:
    """1 for a never-reopened plan, else one more than the latest earlier cycle.

    A reopen records no counter, but it lists every note of the finished cycle in
    `earlier_cycle`; the earlier results carry their cycle in their id (`...-cycleN`,
    a plain id is cycle 1), so a plan reopened twice reads 3 whatever the history.
    """
    if not (page.get("reopened_on") or earlier_cycle_links(page)):
        return 1
    _, earlier = entities.plan_cycle_members(view, page)
    cycles = [
        int(match.group(1))
        if (match := CYCLE_ID_SUFFIX.search(str(m.get("id") or "")))
        else 1
        for m in earlier
        if view.snapshot.entity_type(m) == "result"
    ]
    return max(cycles, default=1) + 1


def plan_new_res(view: ProjectView, *, plan_id: str, title: str) -> WritePlan:
    profile = view.profile
    page = _plan_page(view, plan_id)
    prefix = _section(profile, "ids").get("res_prefix") or "RES-"
    cycle = _cycle_number(view, page)
    res_id = f"{prefix}{plan_id}" + (f"-cycle{cycle}" if cycle > 1 else "")
    if any(n.get("id") == res_id for n in entities.notes_of(view, "result")):
        raise ResearchError(
            "ID_EXISTS", f"a result with id {res_id!r} already exists", exit_code=5
        )
    stem = check_name(format_name(names_for(profile)["res"], pid=plan_id, title=title))
    _unique_stem(view, stem)
    text, left = render_template(
        view,
        "res",
        _default_subs(view, title=title, plan=page.stem, plan_id=plan_id),
    )
    # the built-in and legacy templates spell the id RES-<plan>
    if res_id != f"RES-{plan_id}":
        text = edit_frontmatter(text, [("id", yaml_quote(res_id))])
    # A vault template written before the review flow may still say first-read.
    text = edit_frontmatter(text, [("status", "draft")])
    path = _note_path(view, "res", stem)
    edit = FileEdit(path, "create", "new-file", new_text=text)
    data = {
        "id": res_id,
        "cycle": cycle,
        "created": _created(view, [path]),
        "leftoverPlaceholders": left,
    }
    return WritePlan([edit], data, warnings=_warnings(left, "res"))


# ---- profile scaffold --------------------------------------------------------


def decision_prefix_for(project_id: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", project_id).strip("-").upper()
    return f"{base or 'PROJECT'}-DEC"


def profile_text(project_id: str, name: str, images_rel: str | None = None) -> str:
    """The section 3.4 profile shape with generic English defaults."""
    images = ['  required_prefix: ""']
    if images_rel:
        images.append(f"  dir: {yaml_quote(images_rel)}")
    lines = [
        "---",
        "type: project-profile",
        'schema: "3"',
        f"project_id: {yaml_quote(project_id)}",
        "images:",
        *images,
        "headings:",
        '  rules: "Rules summary (needs confirmation)"',
        '  labels: ["Figure labels", "User remarks"]',
        '  observation: "Observation"',
        '  decision_required: ["Decision", "Rationale"]',
        '  decision_options: "Options"',
        '  decision_consequences: "Consequences"',
        "decisions:",
        f"  id_prefix: {yaml_quote(decision_prefix_for(project_id))}",
        '  strict_from: "1"',
        '  layers: ["research", "engineering"]',
        '  options_warn_layers: ["research"]',
        "paths:",
        '  deviation_register: "ops/deviations.md"',
        '  decision_export: "ops/decisions.jsonl"',
        '  todo_export: "ops/todos.jsonl"',
        "runs:",
        "  id_regex: '^[a-z0-9]{8}$'",
        "scan:",
        "  skip_dirs: []",
        '  template_dirs: ["templates"]',
        '  archive_dir_names: ["archive"]',
        "  ignore_globs: []",
        "templates:",
        '  doc_markers: ["<!-- template-doc:start -->", "<!-- template-doc:end -->"]',
        "labels:",
        '  value_columns: ["1"]',
        '  remark_prefix: "Remark"',
        "deviation:",
        "  id_regex: 'D-\\d+'",
        "  columns:",
        '    id: "ID"',
        '    date: "Date"',
        '    experiment: "Experiment"',
        '    content: "Deviation"',
        '    impact: "Impact on validity"',
        '    disposition: "Disposition"',
        '    cards: "Related notes"',
        '    status: "Status"',
        '    found: "Found at"',
        '  found_values: ["before data", "after data", "not applicable"]',
        '  closed_marker: "closed"',
        "ids:",
        "  plan_regex: '^[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$'",
        "  exec_regex: '^[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+-S\\d+$'",
        '  res_prefix: "RES-"',
        "names:",
        '  plan: "{id}--{title}"',
        '  exec: "exec--{pid} {title}"',
        '  ev: "EV--{pid} {title}"',
        '  appendix: "appendix--{stem}"',
        '  res: "RES--{pid} {title}"',
        "folders:",
        '  plan: "plans"',
        '  exec: "ops"',
        '  ev: "evidence"',
        '  appendix: "ops"',
        '  res: "results"',
        "---",
        f"# {name}: expnote profile",
        "",
        "Project conventions for the expnote research layer: the frontmatter is the "
        "configuration, and the tool reads this note but never rewrites it. Edit it "
        "here (or with your own editor) when the project's conventions change.",
        "",
    ]
    return "\n".join(lines)


def profile_edit(
    project_dir: Path,
    *,
    project_id: str,
    name: str,
    images_rel: str | None = None,
    path: Path | None = None,
) -> FileEdit:
    """A create edit for the profile note; the writer refuses to overwrite."""
    target = path or project_dir / PROFILE_NOTE_NAME
    text = profile_text(project_id, name, images_rel)
    return FileEdit(target, "create", "profile-scaffold", new_text=text)
