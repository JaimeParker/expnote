"""`ev split`: split a legacy evidence card into a figure page and an appendix.

A port of vault.py `render split-ev`. The page keeps the original file name (all
inbound links keep working); the appendix is a new note named by the profile's
`names.appendix`. Every figure section goes to the page (checksum lines to the
appendix), the label table is copied as it is with its remark folded into a
collapsed callout, and every other section moves verbatim to the appendix. The
project-specific words (heading prefixes, intro sentences) come from the profile's
`split` section; English defaults apply where it is silent.
"""

from __future__ import annotations

import dataclasses
import re

from expnote.research import entities
from expnote.research.checks import label_state
from expnote.research.edits import yaml_list, yaml_quote, yaml_scalar
from expnote.research.errors import ResearchError
from expnote.research.model import Note
from expnote.research.newnote import (
    _note_path,
    _unique_stem,
    format_name,
    names_for,
)
from expnote.research.textkit import (
    headings,
    is_attachment_name,
    iter_links,
    link_stem,
    markdown_tables,
    split_table_row,
)
from expnote.research.view import ProjectView
from expnote.research.writeback import FileEdit
from expnote.research.writes import WritePlan

DEFAULT_WORDS = {
    "figure_prefix": "Figure",
    "endpoint_prefix": "Table",
    "run_identity": "Run identity",
    "agent_filled_marker": "filled by agent",
    "md5_heading_suffix": " (file checksums; figures are on the page)",
    "page_note": (
        "> Numbers, run identity, provenance and checks: see the appendix "
        "[[{appendix}]]. This page holds figures, the endpoint table and the "
        "user's labels; no observation before the labels are confirmed."
    ),
    "appendix_note": (
        "Evidence page: [[{page}]]. Everything of the original card except the "
        "figures and the user's labels, kept verbatim."
    ),
    "endpoint_heading": "Endpoint table (row-by-row copy of appendix {endpoint})",
    "observation_heading": (
        "Observation (coordinator; written after the user's labels are confirmed)"
    ),
    "observation_comment": "leave empty until the labels are confirmed",
}
DEFAULT_REMARK_PREFIX = "Remark"


@dataclasses.dataclass(frozen=True)
class SplitResult:
    page_text: str
    appendix_text: str
    page_stem: str
    appendix_stem: str


def split_words(view: ProjectView) -> dict[str, str]:
    section = view.profile.raw.get("split")
    given = section if isinstance(section, dict) else {}
    return {**DEFAULT_WORDS, **{k: v for k, v in given.items() if isinstance(v, str)}}


def remark_prefix(view: ProjectView) -> str:
    labels = view.profile.raw.get("labels")
    value = labels.get("remark_prefix") if isinstance(labels, dict) else None
    return value if isinstance(value, str) and value else DEFAULT_REMARK_PREFIX


def fold_remark(label_lines: list[str], prefix: str) -> list[str]:
    """Fold the remark line and what follows into a collapsed callout, verbatim."""
    idx = next(
        (i for i, ln in enumerate(label_lines) if ln.strip().startswith(prefix)), None
    )
    if idx is None:
        return list(label_lines)
    head = label_lines[:idx]
    first = re.sub(r"^\s*" + re.escape(prefix) + r"[:\uff1a]?\s*", "", label_lines[idx])
    rest = ([first] if first else []) + label_lines[idx + 1 :]
    while rest and not rest[0].strip():
        rest.pop(0)
    while rest and not rest[-1].strip():
        rest.pop()
    if not rest:
        return head + [label_lines[idx]]
    return (
        head
        + [f"> [!note]- {prefix}"]
        + [("> " + ln) if ln.strip() else ">" for ln in rest]
    )


def split_ev(
    view: ProjectView,
    note: Note,
    *,
    endpoint: str = "M-1",
    exec_plan: str = "",
    labelled_by: str | None = None,
    label_authorization: str = "",
) -> SplitResult:
    profile = view.profile
    words = split_words(view)
    prefix = remark_prefix(view)
    body = note.body
    lines = body.split("\n")
    hs = headings(body)
    h1 = next((t for _, lvl, t in hs if lvl == 1), note.stem)
    level2 = [(i, t) for i, lvl, t in hs if lvl == 2]
    sections = [
        (t, i, level2[k + 1][0] if k + 1 < len(level2) else len(lines))
        for k, (i, t) in enumerate(level2)
    ]
    pre_end = level2[0][0] if level2 else len(lines)
    preamble = [ln for ln in lines[:pre_end] if not ln.startswith("# ")]
    while preamble and not preamble[0].strip():
        preamble.pop(0)
    page_stem = note.stem
    app_stem = format_name(names_for(profile)["appendix"], stem=note.stem)
    endpoint_re = re.compile(
        re.escape(words["endpoint_prefix"]) + r" ?" + re.escape(endpoint) + r"(?![\w-])"
    )

    label_sec = endpoint_sec = run_sec = None
    figure_secs = []
    for sec in sections:
        if any(k in sec[0] for k in profile.heading_labels) and label_sec is None:
            label_sec = sec
        elif sec[0].startswith(words["figure_prefix"]):
            figure_secs.append(sec)
        elif endpoint_re.search(sec[0]) and endpoint_sec is None:
            endpoint_sec = sec
        if words["run_identity"] in sec[0] and run_sec is None:
            run_sec = sec

    figures: list[str] = []
    figure_page: list[str] = []
    figure_appendix: dict[int, list[str]] = {}
    for title, start, stop in figure_secs:
        in_md5 = False
        part, md5 = [], []
        for ln in lines[start + 1 : stop]:
            if "md5" in ln:
                in_md5 = True
            if in_md5:
                md5.append(ln)
                continue
            figures += [
                link_stem(t)
                for embed, t in iter_links(ln)
                if embed and is_attachment_name(t, profile.attachment_exts)
            ]
            part.append(ln)
        while part and not part[-1].strip():
            part.pop()
        figure_page += [f"## {title}", *part, ""]
        figure_appendix[start] = md5
    runs: list[str] = []
    if run_sec:
        tables = markdown_tables("\n".join(lines[run_sec[1] + 1 : run_sec[2]]))
        if tables:
            head = split_table_row(tables[0][0]) or []
            col = next(
                (k for k, c in enumerate(head) if re.search(r"wandb\s*id", c, re.I)),
                None,
            )
            if col is not None:
                for row in tables[0][2:]:
                    cells = split_table_row(row) or []
                    if len(cells) > col:
                        value = cells[col].strip().strip("`")
                        if value and value not in runs:
                            runs.append(value)
    endpoint_table: list[str] = []
    if endpoint_sec:
        found = markdown_tables("\n".join(lines[endpoint_sec[1] + 1 : endpoint_sec[2]]))
        endpoint_table = found[0] if found else []
    label_lines = lines[label_sec[1] : label_sec[2]] if label_sec else []
    label_text = "\n".join(label_lines)
    state = label_state(label_text, profile) if label_sec else "missing"
    optional_note = bool(label_sec) and any(
        k in label_sec[0] for k in profile.heading_labels[1:]
    )
    if state == "filled":
        status = "labelled"
    elif optional_note and state in ("empty", "missing"):
        status = "label-na"
    else:
        status = "rendered"
    if labelled_by is None:
        labelled_by = (
            "agent-for-user" if words["agent_filled_marker"] in label_text else ""
        )
    if status != "labelled":
        labelled_by = ""
    label_block = fold_remark(label_lines, prefix)

    project_name = view.context.entry.name
    plan_value = note.get("plan") or ""
    page = [
        "---",
        "schema: 3",
        "type: evidence",
        f"project: {yaml_scalar(project_name)}",
    ]
    if note.get("date"):
        page.append(f"date: {yaml_scalar(note.get('date'))}")
    page += [
        f"plan: {yaml_quote(plan_value)}",
        f"exec_plan: {yaml_quote(exec_plan)}",
        'question: ""',
        f"status: {status}",
        f"figures: {yaml_list(figures)}",
        f"runs: {yaml_list(runs)}",
        f"appendix: {yaml_quote('[[' + app_stem + ']]')}",
        f"labelled_by: {labelled_by}" if labelled_by else 'labelled_by: ""',
        'labelled_on: ""',
        f"label_authorization: {yaml_quote(label_authorization)}",
    ]
    if note.get("decision"):
        page.append(f"decision: {yaml_scalar(note.get('decision'))}")
    page += [
        "---",
        f"# {h1}",
        "",
        words["page_note"].format(appendix=app_stem),
        "",
    ]
    page += figure_page
    if endpoint_table:
        page += [
            f"## {words['endpoint_heading'].format(endpoint=endpoint)}",
            "",
            *endpoint_table,
            "",
        ]
    if label_block:
        page += label_block
        while page and not page[-1].strip():
            page.pop()
        page.append("")
    page += [
        f"## {words['observation_heading']}",
        "",
        f"<!-- {words['observation_comment']} -->",
        "",
    ]

    appendix = [
        "---",
        "schema: 3",
        "type: evidence-appendix",
        f"project: {yaml_scalar(project_name)}",
    ]
    if note.get("date"):
        appendix.append(f"date: {yaml_scalar(note.get('date'))}")
    appendix += [
        f"parent: {yaml_quote('[[' + page_stem + ']]')}",
        f"plan: {yaml_quote(plan_value)}",
        f"runs: {yaml_list(runs)}",
        'expnote_snapshot: ""',
        "generated_by: expnote ev split",
        "---",
        f"# {format_name(names_for(profile)['appendix'], stem=h1)}",
        "",
        words["appendix_note"].format(page=page_stem),
        "",
    ]
    appendix += preamble
    for title, start, stop in sections:
        if label_sec and start == label_sec[1]:
            continue
        if start in figure_appendix:
            if figure_appendix[start]:
                suffix = words["md5_heading_suffix"]
                appendix += [f"## {title}{suffix}", "", *figure_appendix[start]]
            continue
        appendix += lines[start:stop]
    while appendix and not appendix[-1].strip():
        appendix.pop()
    appendix.append("")
    return SplitResult("\n".join(page) + "\n", "\n".join(appendix), page_stem, app_stem)


def plan_ev_split(
    view: ProjectView,
    ident: str,
    *,
    endpoint: str = "M-1",
    exec_ident: str | None = None,
    labelled_by: str | None = None,
    label_authorization: str = "",
    expected_sha256: str | None = None,
) -> WritePlan:
    note = entities.find_by_id(view, "evidence", ident)
    if note.get("appendix"):
        raise ResearchError(
            "ALREADY_SPLIT",
            f"{note.stem} already has an appendix twin",
            exit_code=2,
        )
    if labelled_by not in (None, "", "user", "agent-for-user"):
        raise ResearchError(
            "USAGE_BAD_OPTION",
            "--labelled-by takes user or agent-for-user",
            exit_code=2,
        )
    exec_link = (
        f"[[{entities.find_by_id(view, 'exec-plan', exec_ident).stem}]]"
        if exec_ident
        else ""
    )
    result = split_ev(
        view,
        note,
        endpoint=endpoint,
        exec_plan=exec_link,
        labelled_by=labelled_by,
        label_authorization=label_authorization,
    )
    _unique_stem(view, result.appendix_stem)
    appendix_path = _note_path(view, "appendix", result.appendix_stem)
    base_sha = expected_sha256 or view.file_sha256(note.rel)
    edits = [
        FileEdit(appendix_path, "create", "new-file", new_text=result.appendix_text),
        FileEdit(
            view.abs_path(note),
            "split",
            "body:split",
            transform=lambda _text: result.page_text,
            expected_sha256=base_sha,
        ),
    ]
    root = view.context.resolved.vault_root
    data = {
        "ev": result.page_stem,
        "appendix": result.appendix_stem,
        "appendixPath": appendix_path.relative_to(root).as_posix(),
    }
    return WritePlan(edits, data)
