"""Write planners: each turns a command into `FileEdit`s after checking its gate.

A planner reads a refreshed `ProjectView`, enforces the lifecycle gates of design
section 7.1 and the user-only rule of 7.2, and returns a `WritePlan`. It never
writes; `execute_plan` runs dry-run/apply through `writeback`. The GUI (P3b) calls
the same planners.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from datetime import UTC

from expnote.research import entities
from expnote.research.checks import (
    _check_exec_plan,
    _check_plan,
    _check_result,
    label_state,
    rules_sha256,
)
from expnote.research.decision_export import plan_export, sync_export
from expnote.research.decision_store import DecisionStore, decision_id, store_path
from expnote.research.decisions import decision_from_row, parse_decision_ref
from expnote.research.edits import (
    adopt_task_line,
    body_line_offset,
    edit_frontmatter,
    find_row,
    find_tables,
    find_task_line,
    format_row,
    format_task_line,
    mark_task,
    next_task_id,
    replace_cell,
    yaml_list,
    yaml_quote,
)
from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.extract import TODO_FIELD_RE, TODO_LINE_RE, parse_todo_lines
from expnote.research.model import FindingSink, Note
from expnote.research.schemas import DEVIATION_IMPACT
from expnote.research.textkit import find_section, split_table_row
from expnote.research.view import ProjectView
from expnote.research.writeback import (
    FileEdit,
    WriteContext,
    apply_changes,
    plan_changes,
)

DRY_RUN_EXIT = 4
PLAN_ORDER = entities.PLAN_ORDER
PLAN_TERMINAL = entities.PLAN_TERMINAL
EXEC_MOVES = {
    "draft": ("aborted",),
    "frozen": ("running", "aborted"),
    "running": ("done", "aborted"),
}


@dataclasses.dataclass
class StoreOp:
    """A change to the decision store, applied in one transaction with the edits."""

    name: str
    address: str
    summary: str  # what a dry-run shows
    apply: Callable[[DecisionStore, str, str], None]  # (store, actor, timestamp)


@dataclasses.dataclass
class WritePlan:
    edits: list[FileEdit]
    data: dict = dataclasses.field(default_factory=dict)
    authorization: str | None = None
    warnings: list[dict] = dataclasses.field(default_factory=list)
    # Fills data that only exists once the edits ran (an allocated id).
    finalize: Callable[[dict], None] | None = None
    store_op: StoreOp | None = None


def execute_plan(
    view: ProjectView,
    plan: WritePlan,
    *,
    command: str,
    apply: bool,
    no_quiet: bool = False,
    confirm_command: str | None = None,
    actor: str | None = None,
) -> CommandResult:
    """Dry-run (exit 4) unless `apply`; no changes at all is a plain success."""
    context = WriteContext.for_project(view.context, actor)
    changes = plan_changes(context, plan.edits)
    data = dict(plan.data)
    if plan.finalize is not None:
        plan.finalize(data)
    meta = view.meta()
    if plan.store_op is not None:
        return _execute_store_plan(
            view, context, plan, changes, data, meta,
            command=command, apply=apply, no_quiet=no_quiet,
            confirm_command=confirm_command,
        )  # fmt: skip
    if not changes:
        data.update(changes=[], applied=False, dryRun=False)
        return CommandResult(data, plan.warnings, meta)
    if not apply:
        data.update(
            changes=[c.as_dict() for c in changes],
            applied=False,
            dryRun=True,
            confirmCommand=confirm_command,
        )
        return CommandResult(data, plan.warnings, meta, exit_code=DRY_RUN_EXIT)
    result = apply_changes(
        context,
        changes,
        command=command,
        authorization=plan.authorization,
        no_quiet=no_quiet,
    )
    data.update(changes=result.applied, applied=True, dryRun=False)
    return CommandResult(data, [*plan.warnings, *result.warnings], meta)


def export_target(view: ProjectView):
    return view.context.resolved.project_dir / view.profile.decision_export


def written_at(view: ProjectView) -> str:
    return view.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _export_warning(error: ResearchError) -> dict:
    return {
        "code": error.code,
        "message": f"{error.message}; try: "
        f"{error.remediation or 'expnote decision export --apply'}",
    }


def _execute_store_plan(
    view: ProjectView,
    context: WriteContext,
    plan: WritePlan,
    changes: list,
    data: dict,
    meta: dict,
    *,
    command: str,
    apply: bool,
    no_quiet: bool,
    confirm_command: str | None,
) -> CommandResult:
    """A plan that changes the decision store (and perhaps a note) plus the export."""
    op = plan.store_op
    assert op is not None
    project_id = view.project_id
    target = export_target(view)
    stamp = written_at(view)
    warnings = list(plan.warnings)
    store_change = {
        "path": store_path(project_id).name,
        "absPath": str(store_path(project_id)),
        "op": op.name,
        "address": op.address,
        "beforeSha256": None,
        "afterSha256": None,
        "unifiedDiff": op.summary,
        "diffTruncated": False,
    }
    if not apply:
        with DecisionStore.open_for_preview(project_id) as store, store.dry_run():
            op.apply(store, context.actor, stamp)
            export = plan_export(store, context, target)
        if export.problem is not None:
            warnings.append(_export_warning(export.problem))
        listed = [store_change, *(c.as_dict() for c in changes)]
        if export.change is not None:
            listed.append(export.change.as_dict())
        data.update(
            changes=listed,
            applied=False,
            dryRun=True,
            confirmCommand=confirm_command,
        )
        return CommandResult(data, warnings, meta, exit_code=DRY_RUN_EXIT)
    applied = [store_change]
    with DecisionStore.open(project_id) as store:
        with store.transaction():
            op.apply(store, context.actor, stamp)
            if changes:
                result = apply_changes(
                    context,
                    changes,
                    command=command,
                    authorization=plan.authorization,
                    no_quiet=no_quiet,
                )
                applied += result.applied
                warnings += result.warnings
        try:
            _, record = sync_export(store, context, target, command=command)
        except ResearchError as error:
            # The decision is committed; a stale export is repaired by `export`.
            warnings.append(_export_warning(error))
        else:
            if record is not None:
                applied.append(record)
    data.update(changes=applied, applied=True, dryRun=False)
    return CommandResult(data, warnings, meta)


# ---- shared helpers ----------------------------------------------------------


def written_date(view: ProjectView) -> str:
    """Today's date in the user's time zone, for dates the tool writes into notes."""
    return view.clock().astimezone().date().isoformat()


def require_authorization(authorization: str | None, what: str) -> str:
    text = (authorization or "").strip()
    if not text:
        raise ResearchError(
            "USER_ONLY",
            f"{what} is the user's decision and cannot be made by an agent alone",
            exit_code=7,
            remediation=(
                "pass --authorization with the user's own words (or the id of an "
                "earlier confirmed decision), or let the user click in the GUI"
            ),
        )
    return text


def gate_failed(
    message: str, gates: list[dict], remediation: str | None = None
) -> ResearchError:
    return ResearchError(
        "GATE_FAILED",
        message,
        exit_code=1,
        remediation=remediation,
        details={"gates": gates},
    )


def _gate(code: str, message: str) -> dict:
    return {"code": code, "ok": False, "message": message}


def _gate_rows(findings) -> list[dict]:
    return [
        {"code": f.code, "ok": False, "message": f.message}
        for f in findings
        if f.level == "FAIL"
    ]


def _fm_edit(
    view: ProjectView,
    note: Note,
    items: list[tuple[str, str]],
    expected_sha256: str | None,
) -> FileEdit:
    keys = ",".join(key for key, _ in items)
    return FileEdit(
        view.abs_path(note),
        "set-key",
        f"frontmatter:{keys}",
        transform=lambda text: edit_frontmatter(text, items),
        # The gates were checked against the indexed text: swap only if it is current.
        expected_sha256=expected_sha256 or view.file_sha256(note.rel),
    )


def decision_ref_text(view: ProjectView, note: Note, number: int) -> str:
    """The form this note's schema accepts: an id string for 3, a number for 2."""
    if note.schema == "3":
        return f"{view.profile.decision_id_prefix}-{number:04d}"
    return str(number)


def decision_ref_literal(view: ProjectView, note: Note, number: int) -> str:
    text = decision_ref_text(view, note, number)
    return yaml_quote(text) if note.schema == "3" else text


def _decision_number(view: ProjectView, ident: str) -> int:
    return entities.parse_decision_ident(view, ident)


def _decision(view: ProjectView, ident: str):
    number = _decision_number(view, ident)
    row = view.snapshot.decisions.get(number)
    if row is None:
        raise ResearchError(
            "NOT_FOUND",
            f"no decision {ident!r}",
            exit_code=3,
            remediation="expnote decision list",
        )
    return number, row


def _stem_link(note: Note) -> str:
    return f"[[{note.stem}]]"


def _clone(note: Note, **fm) -> Note:
    return dataclasses.replace(note, fm={**note.fm, **fm})


# ---- decisions ---------------------------------------------------------------


def _split_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _wrap_link(target: str) -> str:
    return target if target.startswith("[[") else f"[[{target}]]"


def _record_summary(record: dict) -> str:
    """The new decision as text, for the dry-run (the store has no file to diff)."""
    lines = [
        f"+id: {record['id']}",
        f"+title: {record['title']}",
        f"+status: {record['status']}",
        f"+layer: {record['layer']}",
    ]
    for key in ("plans", "targets", "supersedes"):
        if record[key]:
            lines.append(f"+{key}: {record[key]}")
    for key in ("decided", "reason", "options", "lands"):
        if record[key]:
            lines.append(f"+{key}: {record[key]}")
    return "\n".join(lines)


def plan_decision_new(
    view: ProjectView,
    *,
    title: str,
    layer: str,
    decided: str,
    reason: str,
    options: str = "",
    lands: str = "",
    plans: str | None = None,
    targets: str | None = None,
    supersedes: str | None = None,
    review_by: str | None = None,
    status: str = "proposed",
    authorization: str | None = None,
) -> WritePlan:
    """A proposed decision, or (`confirmed`) one the user already confirmed in chat."""
    profile, snap = view.profile, view.snapshot
    if status not in ("proposed", "confirmed"):
        raise ResearchError(
            "USAGE_BAD_OPTION",
            "--status takes proposed, or confirmed for a decision the user "
            "already confirmed in chat",
            exit_code=2,
        )
    if status == "confirmed":
        authorization = require_authorization(
            authorization, "recording a decision as confirmed"
        )
    required = (("--title", title), ("--decided", decided), ("--reason", reason))
    for name, text in required:
        if not text.strip():
            raise ResearchError("USAGE_EMPTY_TEXT", f"{name} is empty", exit_code=2)
    if profile.decision_layers and layer not in profile.decision_layers:
        raise ResearchError(
            "USAGE_BAD_OPTION",
            f"--layer takes one of {', '.join(profile.decision_layers)}",
            exit_code=2,
        )
    if review_by is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", review_by):
        raise ResearchError(
            "USAGE_BAD_OPTION", "--review-by takes YYYY-MM-DD", exit_code=2
        )
    plan_ids = _split_list(plans)
    for plan_id in plan_ids:
        entities.find_plan(view, plan_id)
    predecessors = [_decision(view, ident)[0] for ident in _split_list(supersedes)]
    number = max(snap.decisions, default=0) + 1
    ident = decision_id(profile.decision_id_prefix, number)
    stamp = written_at(view)
    record = {
        "number": number,
        "id": ident,
        "title": " ".join(title.split()),
        "date": written_date(view),
        "status": status,
        "layer": layer,
        "decided": decided.strip(),
        "reason": reason.strip(),
        "options": options.strip(),
        "lands": lands.strip(),
        "plans": plan_ids,
        "targets": [_wrap_link(t) for t in _split_list(targets)],
        "supersedes": predecessors,
        "review_by": review_by,
        "status_raw": "",  # only migrated rows keep the old status cell
        "source": "chat" if status == "confirmed" else "cli",
    }
    if status == "confirmed":
        record |= {
            "confirmed_by": "agent-for-user",
            "confirmed_on": written_date(view),
            "authorization": authorization,
        }
    op = StoreOp(
        "decision-new",
        f"decision:{ident}",
        _record_summary(record),
        lambda store, actor, at: store.insert(record, actor=actor, at=at),
    )
    data = {"id": ident, "number": number, "status": status, "createdAt": stamp}
    return WritePlan([], data, authorization=authorization, store_op=op)


def plan_decision_set(
    view: ProjectView,
    ident: str,
    status: str,
    *,
    authorization: str | None = None,
    by: str | None = None,
    expected_version: int | None = None,
    by_user: bool = False,
) -> WritePlan:
    """`by_user` is the GUI path: the user clicked, so no authorization is required.

    `authorization` then carries the user's optional note.
    """
    if status in ("confirmed", "rejected"):
        if by_user:
            authorization = (authorization or "").strip() or None
        else:
            authorization = require_authorization(
                authorization, f"marking a decision {status}"
            )
    elif status != "superseded":
        raise ResearchError(
            "USAGE_BAD_OPTION",
            "--status takes confirmed, rejected or superseded",
            exit_code=2,
        )
    number, decision = _decision(view, ident)
    ident = entities.decision_id(view, decision)
    if decision.source != "db":
        raise ResearchError(
            "DECISION_NOT_EDITABLE",
            f"{ident} is a row of the legacy decision table, not of the store",
            exit_code=2,
            remediation="expnote migrate apply --step decisions",
        )
    current = decision.status
    base = {"id": ident, "from": current, "to": status}
    if current == status:
        return WritePlan([], base)
    if expected_version is not None and expected_version != decision.version:
        raise ResearchError(
            "CONFLICT",
            f"{ident} changed since it was read (expected version "
            f"{expected_version}, now {decision.version})",
            exit_code=5,
            remediation="reload the decision and decide again",
            details={
                "expectedVersion": expected_version,
                "currentVersion": decision.version,
            },
        )
    allowed = {
        "confirmed": ("proposed",),
        "rejected": ("proposed",),
        "superseded": ("confirmed",),
    }[status]
    if current not in allowed:
        raise gate_failed(
            f"{ident} is {current!r}; {status} needs {' or '.join(allowed)}",
            [_gate("DEC.TRANSITION", f"{current} -> {status}")],
        )
    version = decision.version if expected_version is None else expected_version
    warnings: list[dict] = []
    successor: int | None = None
    if status == "superseded":
        successor, warnings = _check_successor(view, ident, number, by)
    confirmed_by = "user" if by_user else "agent-for-user"
    today = written_date(view)

    def apply(store: DecisionStore, actor: str, at: str) -> None:
        store.transition(
            number,
            status,
            expected_version=version,
            actor=actor,
            at=at,
            confirmed_by=confirmed_by if status != "superseded" else None,
            confirmed_on=today,
            authorization=authorization,
            superseded_by=successor,
        )

    summary = [f"-status: {current}", f"+status: {status}"]
    if status == "superseded":
        summary.append(f"+superseded_by: {decision_id_of(view, successor)}")
    else:
        summary.append(f"+confirmed_by: {confirmed_by}")
        if authorization:
            summary.append(f"+authorization: {authorization}")
    op = StoreOp("decision-set", f"decision:{ident}", "\n".join(summary), apply)
    return WritePlan(
        [], base, authorization=authorization, warnings=warnings, store_op=op
    )


def decision_id_of(view: ProjectView, number: int | None) -> str:
    return decision_id(view.profile.decision_id_prefix, number or 0)


def _check_successor(view, ident: str, number: int, by: str | None):
    if not by:
        raise ResearchError(
            "USAGE_MISSING_OPTION",
            "superseding a decision needs --by <successor decision id>",
            exit_code=2,
        )
    successor_number, successor = _decision(view, by)
    successor_id = entities.decision_id(view, successor)
    warnings: list[dict] = []
    if not successor.refers_back_to(number):
        if successor.legacy or successor.source == "table":
            warnings.append(
                {
                    "code": "DEC.NO_BACKREF",
                    "message": f"{successor_id} does not list {ident}",
                }
            )
        else:
            raise gate_failed(
                f"{successor_id} must list {ident} in `supersedes`",
                [_gate("DEC.NO_BACKREF", successor_id)],
            )
    return successor_number, warnings


# ---- evidence labels ---------------------------------------------------------


def _label_predicate(view: ProjectView):
    keys = view.profile.heading_labels
    return lambda title: any(k in title for k in keys)


def _remark_prefix(view: ProjectView) -> str | None:
    labels = view.profile.raw.get("labels")
    value = labels.get("remark_prefix") if isinstance(labels, dict) else None
    return value if isinstance(value, str) and value else None


def _label_table_edit(view: ProjectView, row_key: str, values, remark: str | None):
    profile = view.profile
    predicate = _label_predicate(view)
    prefix = _remark_prefix(view)
    if remark is not None and prefix is None:
        raise ResearchError(
            "PROFILE_KEY_MISSING",
            "--remark needs labels.remark_prefix in the profile note",
            exit_code=2,
        )

    def transform(text: str) -> str:
        lines = text.split("\n")
        offset = body_line_offset(text)
        section = find_section("\n".join(lines[offset:]), predicate)
        if section is None:
            raise ResearchError(
                "ADDRESS_NOT_FOUND",
                f"the note has no section headed {profile.heading_labels[0]!r}",
                exit_code=3,
            )
        start, end = offset + section[0] + 1, offset + section[1]
        tables = find_tables(lines, start, end)
        if not tables:
            raise ResearchError(
                "ADDRESS_NOT_FOUND", "the label section has no table", exit_code=3
            )
        table = tables[0]
        row_at = find_row(lines, table, 0, row_key)
        if row_at is None:
            known = [(split_table_row(lines[i]) or [""])[0] for i in table.row_lines()]
            raise ResearchError(
                "ADDRESS_NOT_FOUND",
                f"no label row {row_key!r}",
                exit_code=3,
                details={"rows": known},
            )
        for column, value in values:
            header = [h.strip() for h in table.header]
            if column.strip() not in header or header.index(column.strip()) == 0:
                raise ResearchError(
                    "ADDRESS_NOT_FOUND",
                    f"no value column {column!r}",
                    exit_code=3,
                    details={"columns": header[1:]},
                )
            lines[row_at] = replace_cell(
                lines[row_at], header.index(column.strip()), value
            )
        if remark is not None:
            _set_remark(lines, table.last_line + 1, end, prefix or "", remark)
        return "\n".join(lines)

    return transform


def _set_remark(lines: list[str], start: int, end: int, prefix: str, text: str) -> None:
    if "\n" in text or "\r" in text:
        raise ResearchError(
            "USAGE_MULTILINE_VALUE", "--remark must be a single line", exit_code=2
        )
    pattern = re.compile(rf"^(\s*{re.escape(prefix)}(?:\s*[:\uff1a]\s*|\s+|$))(.*)$")
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            head = match.group(1)
            has_colon = re.search(r"[:\uff1a]", head) is not None
            lines[index] = head + text if has_colon else f"{head.rstrip()}: {text}"
            return
    lines[start:start] = ["", f"{prefix}: {text}"]


def _parse_values(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for item in values:
        column, sep, value = item.partition("=")
        if not sep or not column.strip():
            raise ResearchError(
                "USAGE_BAD_OPTION",
                f"--value takes COLUMN=TEXT, got {item!r}",
                exit_code=2,
            )
        pairs.append((column, value))
    return pairs


def plan_ev_label_set(
    view: ProjectView,
    ident: str,
    *,
    row: str,
    values: list[str],
    remark: str | None,
    authorization: str | None,
    expected_sha256: str | None = None,
) -> WritePlan:
    authorization = require_authorization(authorization, "filling the user's labels")
    note = entities.find_by_id(view, "evidence", ident)
    if note.get("status") != "rendered":
        raise gate_failed(
            f"{note.stem} is {note.get('status')!r}; labels are edited while it is "
            "rendered",
            [{"code": "EV.NOT_RENDERED", "ok": False, "message": note.stem}],
        )
    if not values and remark is None:
        raise ResearchError(
            "USAGE_MISSING_OPTION",
            "give --value COLUMN=TEXT and/or --remark",
            exit_code=2,
        )
    transform = _label_table_edit(view, row, _parse_values(values), remark)
    edit = FileEdit(
        view.abs_path(note),
        "edit-row",
        f"label-row:{row}",
        transform=transform,
        expected_sha256=expected_sha256 or view.file_sha256(note.rel),
    )
    return WritePlan([edit], {"ev": note.stem, "row": row}, authorization=authorization)


def plan_ev_label_commit(
    view: ProjectView,
    ident: str,
    *,
    authorization: str | None,
    expected_sha256: str | None = None,
) -> WritePlan:
    authorization = require_authorization(authorization, "committing the user's labels")
    note = entities.find_by_id(view, "evidence", ident)
    if note.get("status") == "labelled":
        return WritePlan([], {"ev": note.stem, "to": "labelled"})
    if note.get("status") != "rendered":
        raise gate_failed(
            f"{note.stem} is {note.get('status')!r}; only rendered pages are labelled",
            [{"code": "EV.NOT_RENDERED", "ok": False, "message": note.stem}],
        )
    state = label_state(note.body, view.profile)
    if state != "filled":
        raise gate_failed(
            f"the label table of {note.stem} is {state}; every row needs a value",
            [{"code": "EV.LABEL_NOT_FILLED", "ok": False, "message": state}],
        )
    reference = parse_decision_ref(authorization, view.profile, lenient=True)
    if reference is not None:
        cited = view.snapshot.decisions.get(reference)
        if cited is None or cited.status != "confirmed":
            raise gate_failed(
                f"authorization cites decision {authorization}, which is not confirmed",
                [{"code": "DEC.NOT_CONFIRMED", "ok": False, "message": authorization}],
            )
    items = [
        ("status", "labelled"),
        ("labelled_by", "agent-for-user"),
        ("labelled_on", written_date(view)),
        ("label_authorization", yaml_quote(authorization)),
    ]
    return WritePlan(
        [_fm_edit(view, note, items, expected_sha256)],
        {"ev": note.stem, "from": "rendered", "to": "labelled"},
        authorization=authorization,
    )


def remark_supported(view: ProjectView) -> bool:
    return _remark_prefix(view) is not None


def plan_ev_labels_by_user(
    view: ProjectView,
    ident: str,
    *,
    rows: list[tuple[str, list[tuple[str, str]]]],
    remark: str | None = None,
    commit: bool = False,
    expected_sha256: str | None = None,
) -> WritePlan:
    """The GUI path: several label rows (and optionally the commit) as one edit.

    `rows` is `[(row key, [(column, text)])]`. One `FileEdit` carries every row and the
    status flip, so the file is read, hashed and replaced once.
    """
    note = entities.find_by_id(view, "evidence", ident)
    base = {"ev": note.stem, "rows": [key for key, _ in rows], "committed": commit}
    if note.get("status") == "labelled" and commit and not rows and remark is None:
        return WritePlan([], base)
    if note.get("status") != "rendered":
        raise gate_failed(
            f"{note.stem} is {note.get('status')!r}; labels are edited while it is "
            "rendered",
            [{"code": "EV.NOT_RENDERED", "ok": False, "message": note.stem}],
        )
    if not rows and remark is None and not commit:
        raise ResearchError(
            "USAGE_MISSING_OPTION",
            "give at least one row, a remark or commit",
            exit_code=2,
        )
    if remark is not None and not rows:
        raise ResearchError(
            "USAGE_MISSING_OPTION", "a remark is saved together with a row", exit_code=2
        )
    value_columns = _value_column_names(view, note)
    for _, pairs in rows:
        for column, _text in pairs:
            if column.strip() not in value_columns:
                raise ResearchError(
                    "ADDRESS_NOT_FOUND",
                    f"{column!r} is not a column the user fills",
                    exit_code=3,
                    details={"columns": sorted(value_columns)},
                )
    today = written_date(view)
    profile = view.profile

    def transform(text: str) -> str:
        for index, (key, pairs) in enumerate(rows):
            last = index == len(rows) - 1
            text = _label_table_edit(view, key, pairs, remark if last else None)(text)
        if commit:
            body = "\n".join(text.split("\n")[body_line_offset(text) :])
            state = label_state(body, profile)
            if state != "filled":
                raise gate_failed(
                    f"the label table of {note.stem} is {state}; every row needs a "
                    "value",
                    [{"code": "EV.LABEL_NOT_FILLED", "ok": False, "message": state}],
                )
            text = edit_frontmatter(
                text,
                [
                    ("status", "labelled"),
                    ("labelled_by", "user"),
                    ("labelled_on", today),
                ],
            )
        return text

    edit = FileEdit(
        view.abs_path(note),
        "edit-row",
        "label-rows:"
        + ",".join(key for key, _ in rows)
        + (":commit" if commit else ""),
        transform=transform,
        expected_sha256=expected_sha256 or view.file_sha256(note.rel),
    )
    return WritePlan([edit], base)


def _value_column_names(view: ProjectView, note: Note) -> set[str]:
    table = entities.ev_tables(view, note)["labelTable"]
    if not table:
        return set()
    header = table["header"]
    return {header[i].strip() for i in table["valueColumns"] if 0 < i < len(header)}


# ---- manual TODO file --------------------------------------------------------


def todo_file_path(view: ProjectView):
    name = view.profile.todo_file
    if not name:
        raise ResearchError(
            "PROFILE_KEY_MISSING",
            "the profile has no paths.todo_file",
            exit_code=2,
        )
    return view.context.resolved.project_dir / name


TODO_SKELETON = "---\ntype: todo-list\n---\n# Todo\n\n"


def plan_todo_add(
    view: ProjectView,
    text: str,
    *,
    plan: str | None = None,
    due: str | None = None,
    by: str = "agent",
    owner: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    if TODO_FIELD_RE.search(text):
        raise ResearchError(
            "USAGE_BAD_VALUE",
            "the task text may not contain `[key:: value]` fields; use the options",
            exit_code=2,
        )
    if due is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise ResearchError("USAGE_BAD_OPTION", "--due takes YYYY-MM-DD", exit_code=2)
    path = todo_file_path(view)
    allocated: list[str] = []

    def line_for(existing: str) -> str:
        task_id = next_task_id(existing)
        allocated[:] = [task_id]
        fields = [("id", task_id)]
        if plan:
            fields.append(("plan", plan))
        if due:
            fields.append(("due", due))
        fields.append(("by", by))
        if owner:
            fields.append(("owner", owner))
        return format_task_line(text, fields)

    if not path.exists():
        content = f"{TODO_SKELETON}{line_for('')}\n"
        edit = FileEdit(path, "create", "new-file", new_text=content)
    else:

        def append(existing: str) -> str:
            head = existing if existing.endswith("\n") else existing + "\n"
            return f"{head}{line_for(existing)}\n"

        edit = FileEdit(
            path,
            "add-task",
            "task:new",
            transform=append,
            expected_sha256=expected_sha256,
        )

    def finalize(data: dict) -> None:
        data["id"] = allocated[0] if allocated else None

    return WritePlan([edit], {"id": None}, finalize=finalize)


def plan_todo_done(
    view: ProjectView,
    ident: str,
    *,
    undo: bool = False,
    expected_sha256: str | None = None,
) -> WritePlan:
    path = todo_file_path(view)
    today = written_date(view)
    assigned: list[str] = []

    def transform(text: str) -> str:
        lines = text.split("\n")
        index = find_task_line(lines, ident)
        if index is None:
            raise ResearchError(
                "ADDRESS_NOT_FOUND",
                f"no task {ident!r} in {path.name}",
                exit_code=3,
                remediation="expnote todo list --source manual --include-done",
            )
        match = TODO_LINE_RE.match(lines[index].rstrip())
        assert match is not None
        is_done = match.group(1) in "xX"
        if is_done != undo:
            return text  # already in the requested state
        has_id = "[id::" in match.group(2)
        new_id = None if has_id else next_task_id(text)
        if new_id:
            assigned.append(new_id)
        lines[index] = mark_task(
            lines[index], done=not undo, today=today, new_id=new_id
        )
        return "\n".join(lines)

    edit = FileEdit(path, "edit-task", f"task:{ident}", transform=transform,
                    expected_sha256=expected_sha256)  # fmt: skip
    return WritePlan([edit], {"id": ident, "done": not undo, "assignedIds": assigned})


def plan_todo_adopt(
    view: ProjectView, ref: str | None, *, expected_sha256: str | None = None
) -> WritePlan:
    path = todo_file_path(view)
    mapping: dict[str, str] = {}

    def transform(text: str) -> str:
        lines = text.split("\n")
        synthetic = [t for t in parse_todo_lines(text) if t.synthetic]
        chosen = [t for t in synthetic if ref is None or t.todo_id == ref]
        if ref is not None and not chosen:
            raise ResearchError(
                "ADDRESS_NOT_FOUND",
                f"no id-less task {ref!r} in {path.name}",
                exit_code=3,
            )
        working = text
        for record in chosen:
            new_id = next_task_id(working)
            lines[record.line - 1] = adopt_task_line(lines[record.line - 1], new_id)
            working = "\n".join(lines)
            mapping[record.todo_id] = new_id
        return working

    edit = FileEdit(path, "edit-task", "task:adopt", transform=transform,
                    expected_sha256=expected_sha256)  # fmt: skip
    return WritePlan([edit], {"adopted": mapping})


# ---- plan / exec / result lifecycle ------------------------------------------


def _active_results(view: ProjectView, plan: Note) -> list[Note]:
    snap = view.snapshot
    return [
        m
        for m in entities.plan_members(view, plan)
        if snap.entity_type(m) == "result"
        and m.get("status") not in ("superseded", "withdrawn")
    ]


def plan_plan_advance(
    view: ProjectView,
    plan_id: str,
    to: str,
    *,
    decision: str | None = None,
    superseded_by: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    page = entities.find_plan(view, plan_id)
    status = page.get("status")
    base = {"plan": plan_id, "from": status, "to": to}
    if status == to:
        return WritePlan([], {**base, "gates": []})
    _check_transition(status, to)
    updates: dict[str, str] = {"status": to}
    literals: dict[str, str] = {"status": to}
    if to in ("approved", "closed", "superseded", "withdrawn"):
        key = "approved_decision" if to == "approved" else "closed_decision"
        if decision:
            number, _ = _decision(view, decision)
            updates[key] = decision_ref_text(view, page, number)
            literals[key] = decision_ref_literal(view, page, number)
        elif not page.get(key):
            raise ResearchError(
                "USAGE_MISSING_OPTION",
                f"moving a plan to {to} needs --decision <decision id>",
                exit_code=2,
            )
    if to == "superseded":
        if not superseded_by:
            raise ResearchError(
                "USAGE_MISSING_OPTION",
                "superseding a plan needs --superseded-by <plan id>",
                exit_code=2,
            )
        link = _stem_link(entities.find_plan(view, superseded_by))
        updates["superseded_by"], literals["superseded_by"] = link, yaml_quote(link)
    if to == "confirmed":
        active = _active_results(view, page)
        if len(active) == 1:
            link = _stem_link(active[0])
            updates["result"], literals["result"] = link, yaml_quote(link)
    sink = FindingSink()
    _check_plan(view.snapshot, _clone(page, **updates), sink)
    unmet = _gate_rows(sink.items)
    if unmet:
        raise gate_failed(
            f"plan {plan_id} cannot move {status} -> {to}",
            unmet,
            remediation=f"expnote plan show {plan_id} --json",
        )
    passed = [{"code": "LIFE.GATES", "ok": True, "message": f"gates for {to} pass"}]
    edit = _fm_edit(view, page, list(literals.items()), expected_sha256)
    return WritePlan([edit], {**base, "gates": passed})


def _decisions_wording(view: ProjectView, key: str, default: str, plan_id: str) -> str:
    section = view.profile.raw.get("decisions")
    value = section.get(key) if isinstance(section, dict) else None
    text = value if isinstance(value, str) and value else default
    return text.replace("{plan}", plan_id)


def plan_plan_close(
    view: ProjectView,
    plan_id: str,
    *,
    reason: str,
    force: bool = False,
    authorization: str | None = None,
    by_user: bool = False,
    expected_sha256: str | None = None,
) -> WritePlan:
    """Close a plan on the user's word: a confirmed decision plus the plan's keys.

    Without `force` the plan must be `confirmed` and pass the closed gates. With it the
    plan closes from any open status, `forced_close: true` records that the gates were
    skipped, and `check` keeps them visible as WARN.
    """
    if by_user:
        authorization = (authorization or "").strip() or None
    else:
        authorization = require_authorization(authorization, "closing a plan")
    if not reason.strip():
        raise ResearchError("USAGE_EMPTY_TEXT", "--reason is empty", exit_code=2)
    profile, snap = view.profile, view.snapshot
    page = entities.find_plan(view, plan_id)
    status = page.get("status")
    base = {"plan": plan_id, "from": status, "to": "closed", "forced": force}
    if status == "closed":
        return WritePlan([], {**base, "gates": []})
    if status not in PLAN_ORDER:
        raise gate_failed(
            f"plan {plan_id} is {status!r}; only an open plan can be closed",
            [_gate("LIFE.TRANSITION", f"{status} -> closed")],
        )
    number = max(snap.decisions, default=0) + 1
    ident = decision_id(profile.decision_id_prefix, number)
    today = written_date(view)
    default_decided = (
        f"Close {plan_id} without completing its gates."
        if force
        else f"Close {plan_id}."
    )
    decided = _decisions_wording(
        view,
        "close_forced_decided" if force else "close_decided",
        default_decided,
        plan_id,
    )
    record = {
        "number": number,
        "id": ident,
        "title": _decisions_wording(view, "close_title", f"Close {plan_id}", plan_id),
        "date": today,
        "status": "confirmed",
        "layer": profile.decision_layers[0] if profile.decision_layers else "",
        "decided": decided,
        "reason": reason.strip(),
        "options": _decisions_wording(view, "close_options", "", plan_id),
        "lands": "",
        "plans": [plan_id],
        "targets": [_stem_link(page)],
        "supersedes": [],
        "status_raw": "",
        "source": "gui" if by_user else "chat",
        "confirmed_by": "user" if by_user else "agent-for-user",
        "confirmed_on": today,
        "authorization": authorization,
    }
    updates = {
        "status": "closed",
        "closed_decision": decision_ref_text(view, page, number),
    }
    literals = {
        "status": "closed",
        "closed_decision": decision_ref_literal(view, page, number),
    }
    waived = _close_gates(view, page, record, updates, status)
    if force:
        literals["forced_close"] = "true"
    if waived and not force:
        raise gate_failed(
            f"plan {plan_id} cannot be closed: {len(waived)} gate(s) are unmet",
            waived,
            remediation=(
                f"finish them, or close it anyway with `expnote plan close {plan_id} "
                "--force --reason <why> --authorization <the user's words> --apply`"
            ),
        )
    edit = _fm_edit(view, page, list(literals.items()), expected_sha256)
    op = StoreOp(
        "decision-new",
        f"decision:{ident}",
        _record_summary(record),
        lambda store, actor, at: store.insert(record, actor=actor, at=at),
    )
    data = {
        **base,
        "decision": ident,
        "gates": []
        if waived
        else [{"code": "LIFE.GATES", "ok": True, "message": "gates for closed pass"}],
        "waivedGates": waived,
    }
    return WritePlan([edit], data, authorization=authorization, store_op=op)


def _close_gates(view, page: Note, record: dict, updates: dict, status) -> list[dict]:
    """Gates a normal close would fail: the confirmed -> closed rule set."""
    synthetic = dataclasses.replace(
        view.snapshot,
        decisions={
            **view.snapshot.decisions,
            record["number"]: decision_from_row(
                {
                    **record,
                    "legacy": False,
                    "superseded_by": None,
                    "review": None,
                    "review_by": None,
                    "version": 1,
                }
            ),
        },
    )
    sink = FindingSink()
    _check_plan(synthetic, _clone(page, **updates), sink)
    unmet = _gate_rows(sink.items)
    if status != "confirmed":
        unmet.insert(
            0,
            _gate("LIFE.TRANSITION", f"plan is {status!r}; closing follows confirmed"),
        )
    return unmet


def _check_transition(status, to: str) -> None:
    if to in PLAN_TERMINAL and status not in PLAN_TERMINAL:
        return
    if status in PLAN_ORDER and to in PLAN_ORDER:
        if PLAN_ORDER.index(to) == PLAN_ORDER.index(status) + 1:
            return
    raise gate_failed(
        f"a plan moves one step forward (or to {'/'.join(PLAN_TERMINAL)}): "
        f"{status} -> {to} is not allowed",
        [{"code": "LIFE.TRANSITION", "ok": False, "message": f"{status} -> {to}"}],
    )


def _same_decision(view: ProjectView, note: Note, key: str, number: int) -> bool:
    stored = parse_decision_ref(note.get(key), view.profile, lenient=True)
    return stored == number


def plan_exec_freeze(
    view: ProjectView,
    ident: str,
    *,
    rules_decision: str | None = None,
    amend: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    profile = view.profile
    note = entities.find_by_id(view, "exec-plan", ident)
    status = note.get("status")
    exec_id = note.get("id") or note.stem
    if status not in ("draft", "frozen", "running", "done"):
        raise gate_failed(
            f"{exec_id} is {status!r} and cannot be frozen",
            [_gate("LIFE.EXEC_TRANSITION", str(status))],
        )
    digest = rules_sha256(note.body, profile)
    if digest is None:
        raise gate_failed(
            f"{exec_id} has no rules section ({profile.heading_rules!r})",
            [_gate("EXEC.NO_RULES_SECTION", exec_id)],
        )
    stored = note.get("rules_sha256")
    frozen = status != "draft"
    if frozen and stored and stored != digest and not amend:
        raise gate_failed(
            f"the rules of {exec_id} changed after freezing",
            [_gate("EXEC.RULES_CHANGED", exec_id)],
            remediation="register a deviation, then freeze with --amend <D-id>",
        )
    matches_previous = frozen and stored == digest and not amend
    updates: dict = {}
    literals: dict[str, str] = {}
    if rules_decision:
        number, _ = _decision(view, rules_decision)
        updates["rules_decision"] = decision_ref_text(view, note, number)
        if not _same_decision(view, note, "rules_decision", number):
            literals["rules_decision"] = decision_ref_literal(view, note, number)
    if stored != digest:
        updates["rules_sha256"] = literals["rules_sha256"] = digest
    if not matches_previous:
        updates["frozen_on"] = literals["frozen_on"] = written_date(view)
    if amend:
        previous = note.get("rules_amended_by")
        listed = list(previous) if isinstance(previous, list) else []
        if amend not in listed:
            listed.append(amend)
        updates["rules_amended_by"] = listed
        literals["rules_amended_by"] = yaml_list(listed)
    if status == "draft":
        updates["status"] = literals["status"] = "frozen"
    sink = FindingSink()
    _check_exec_plan(view.snapshot, _clone(note, **updates), sink)
    unmet = _gate_rows(sink.items)
    if unmet:
        raise gate_failed(f"{exec_id} cannot be frozen", unmet)
    order = (
        "rules_decision",
        "rules_sha256",
        "frozen_on",
        "rules_amended_by",
        "status",
    )
    items = [(key, literals[key]) for key in order if key in literals]
    data = {
        "id": exec_id,
        "rulesSha256": digest,
        "frozenOn": updates.get("frozen_on", note.get("frozen_on")),
        "matchesPrevious": bool(matches_previous),
    }
    return WritePlan([_fm_edit(view, note, items, expected_sha256)], data)


def plan_exec_advance(
    view: ProjectView,
    ident: str,
    to: str,
    *,
    expected_sha256: str | None = None,
) -> WritePlan:
    note = entities.find_by_id(view, "exec-plan", ident)
    status = note.get("status")
    exec_id = note.get("id") or note.stem
    base = {"id": exec_id, "from": status, "to": to}
    if status == to:
        return WritePlan([], base)
    if to not in EXEC_MOVES.get(status, ()):
        raise gate_failed(
            f"{exec_id} cannot move {status} -> {to}",
            [_gate("LIFE.EXEC_TRANSITION", f"{status} -> {to}")],
        )
    sink = FindingSink()
    _check_exec_plan(view.snapshot, _clone(note, status=to), sink)
    unmet = _gate_rows(sink.items)
    if unmet:
        raise gate_failed(f"{exec_id} cannot move {status} -> {to}", unmet)
    return WritePlan([_fm_edit(view, note, [("status", to)], expected_sha256)], base)


def plan_res_confirm(
    view: ProjectView,
    ident: str,
    *,
    decision: str,
    deviation_impact: str,
    expected_sha256: str | None = None,
) -> WritePlan:
    if deviation_impact not in DEVIATION_IMPACT:
        raise ResearchError(
            "USAGE_BAD_OPTION",
            f"--deviation-impact takes one of {', '.join(DEVIATION_IMPACT)}",
            exit_code=2,
        )
    note = entities.find_by_id(view, "result", ident)
    res_id = note.get("id") or note.stem
    status = note.get("status")
    if status not in ("first-read", "confirmed"):
        raise gate_failed(
            f"{res_id} is {status!r}; only a first-read result is confirmed",
            [_gate("RES.TRANSITION", str(status))],
        )
    number, row = _decision(view, decision)
    if row.status == "proposed":
        raise ResearchError(
            "USER_ONLY",
            f"decision {decision} is still proposed; confirming a result needs the "
            "user's confirmed decision",
            exit_code=7,
            remediation=(
                "the user confirms it in the GUI, or you record their words with "
                "`decision set <id> --status confirmed --authorization ...`"
            ),
        )
    updates = {
        "status": "confirmed",
        "confirmed_decision": decision_ref_text(view, note, number),
        "deviation_impact": deviation_impact,
    }
    sink = FindingSink()
    _check_result(view.snapshot, _clone(note, **updates), sink)
    unmet = _gate_rows(sink.items)
    if unmet:
        raise gate_failed(f"{res_id} cannot be confirmed", unmet)
    items = [
        ("status", "confirmed"),
        ("confirmed_decision", decision_ref_literal(view, note, number)),
        ("deviation_impact", deviation_impact),
    ]
    base = {"id": res_id, "from": status, "to": "confirmed"}
    return WritePlan([_fm_edit(view, note, items, expected_sha256)], base)


# ---- deviation register ------------------------------------------------------


def _deviation_file(view: ProjectView):
    return view.context.resolved.project_dir / view.profile.deviation_register


def _register_table(lines: list[str], id_column: str):
    for table in find_tables(lines):
        if id_column in table.header:
            return table
    raise ResearchError(
        "ADDRESS_NOT_FOUND",
        f"the deviation register has no table with a {id_column!r} column",
        exit_code=3,
    )


def _next_deviation_id(view: ProjectView, existing: list[str]) -> str:
    regex = view.profile.deviation_id_regex
    numbered = [m.groups() for i in existing if (m := re.fullmatch(r"(\D*)(\d+)", i))]
    if numbered:
        prefix = numbered[0][0]
        width = max(len(digits) for _, digits in numbered)
        top = max(int(digits) for _, digits in numbered)
    else:
        head = re.match(r"[A-Za-z_-]*", regex.replace("\\", ""))
        prefix, width, top = (head.group() if head else "") or "D-", 2, 0
    new_id = f"{prefix}{top + 1:0{width}d}"
    if not re.fullmatch(regex, new_id):
        raise ResearchError(
            "PROFILE_INVALID",
            f"generated id {new_id!r} does not match deviation.id_regex",
            exit_code=6,
        )
    return new_id


def plan_deviation_add(
    view: ProjectView,
    *,
    experiment: str,
    content: str,
    impact: str,
    disposition: str = "",
    cards: str = "",
    found: str | None = None,
    date: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    profile = view.profile
    columns = profile.deviation_columns
    path = _deviation_file(view)
    if found is not None and found not in profile.deviation_found_values:
        raise ResearchError(
            "USAGE_BAD_OPTION",
            f"--found takes one of {', '.join(profile.deviation_found_values)}",
            exit_code=2,
        )
    if date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ResearchError("USAGE_BAD_OPTION", "--date takes YYYY-MM-DD", exit_code=2)
    allocated: list[str] = []

    def transform(text: str) -> str:
        lines = text.split("\n")
        table = _register_table(lines, columns["id"])
        header = table.header
        if columns.get("found") in header and found is None:
            raise ResearchError(
                "USAGE_MISSING_OPTION",
                f"--found is required ({', '.join(profile.deviation_found_values)})",
                exit_code=2,
            )
        id_at = header.index(columns["id"])
        ids = [
            (split_table_row(lines[i]) or [])[id_at : id_at + 1]
            for i in table.row_lines()
        ]
        new_id = _next_deviation_id(view, [cell[0] for cell in ids if cell and cell[0]])
        allocated[:] = [new_id]
        values = {
            "id": new_id,
            "date": date or written_date(view),
            "experiment": experiment,
            "content": content,
            "impact": impact,
            "disposition": disposition,
            "cards": cards,
            "status": "open",
            "found": found or "",
        }
        by_column = {columns[f]: v for f, v in values.items() if f in columns}
        lines.insert(
            table.last_line + 1, format_row([by_column.get(h, "") for h in header])
        )
        return "\n".join(lines)

    edit = FileEdit(
        path,
        "add-row",
        "table-row:new",
        transform=transform,
        expected_sha256=expected_sha256,
    )

    def finalize(data: dict) -> None:
        data["id"] = allocated[0] if allocated else None

    return WritePlan([edit], {"id": None}, finalize=finalize)


def plan_deviation_close(
    view: ProjectView,
    ident: str,
    *,
    disposition: str | None = None,
    expected_sha256: str | None = None,
) -> WritePlan:
    profile = view.profile
    columns = profile.deviation_columns
    if ident not in view.snapshot.deviations:
        raise ResearchError(
            "NOT_FOUND",
            f"no deviation {ident!r}",
            exit_code=3,
            remediation="expnote deviation list",
        )
    path = _deviation_file(view)

    def transform(text: str) -> str:
        lines = text.split("\n")
        table = _register_table(lines, columns["id"])
        header = table.header
        at = find_row(lines, table, header.index(columns["id"]), ident)
        if at is None:
            raise ResearchError(
                "ADDRESS_NOT_FOUND", f"no register row {ident!r}", exit_code=3
            )
        status_name, dispo_name = columns.get("status"), columns.get("disposition")
        dispo_at = header.index(dispo_name) if dispo_name in header else None
        if status_name in header:
            lines[at] = replace_cell(lines[at], header.index(status_name), "closed")
            if disposition is not None and dispo_at is not None:
                lines[at] = replace_cell(lines[at], dispo_at, disposition)
            return "\n".join(lines)
        marker = profile.deviation_closed_marker
        if not marker or dispo_at is None:
            raise ResearchError(
                "PROFILE_KEY_MISSING",
                "the register has no status column and the profile has no "
                "deviation.closed_marker",
                exit_code=2,
            )
        cells = split_table_row(lines[at]) or []
        current = cells[dispo_at] if len(cells) > dispo_at else ""
        merged = disposition if disposition is not None else current
        if marker not in merged:
            merged = f"{merged} {marker}".strip()
        lines[at] = replace_cell(lines[at], dispo_at, merged)
        return "\n".join(lines)

    edit = FileEdit(
        path,
        "edit-row",
        f"table-row:{ident}",
        transform=transform,
        expected_sha256=expected_sha256,
    )
    return WritePlan([edit], {"id": ident, "to": "closed"})
