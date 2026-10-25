"""Typer commands that write the vault: dry-run by default, `--apply` to write.

Each command is a thin wrapper: build a `WritePlan` with a planner from `writes.py`
(or `newnote.py`/`split.py`), then `execute_plan`. Exit codes follow section 6.1:
4 for a dry-run, 5 for a conflict, 7 for a user-only refusal.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from expnote.research import (
    adopt,
    entities,
    newnote,
    split,
    todo_export,
    todo_store,
    todo_writes,
    writes,
)
from expnote.research.cliutil import confirm_command, render_write
from expnote.research.commands import (
    NoRefreshOption,
    decision_app,
    deviation_app,
    ev_app,
    exec_app,
    plan_app,
    res_app,
    todo_app,
)
from expnote.research.decision_export import (
    parse_export,
    plan_export,
    restore_rows,
    sync_export,
)
from expnote.research.decision_store import DecisionStore, store_exists
from expnote.research.envelope import CommandResult, run_command
from expnote.research.errors import ResearchError
from expnote.research.project_init import find_profile_notes, resolve_vault_root
from expnote.research.view import ProjectView, open_view
from expnote.research.writeback import WriteContext, apply_changes, plan_changes

JsonOption = Annotated[bool, typer.Option("--json", help="Print the JSON envelope.")]
ProjectOption = Annotated[
    str | None, typer.Option("--project", help="Project id (default: active project).")
]
ApplyOption = Annotated[
    bool, typer.Option("--apply", help="Write the change (default: dry-run, exit 4).")
]
NoQuietOption = Annotated[
    bool,
    typer.Option("--no-quiet", help="Skip the quiet window (audited; CLI only)."),
]
ExpectedOption = Annotated[
    str | None,
    typer.Option(
        "--expected-sha256", help="Refuse unless the note still has this sha256."
    ),
]
AuthorizationOption = Annotated[
    str | None,
    typer.Option(
        "--authorization",
        help="The user's own words (or an earlier confirmed decision id).",
    ),
]

new_app = typer.Typer(
    help="Create notes from templates: plan, exec, ev, res, decision, profile "
    "(dry-run unless --apply).",
    epilog="Example: `expnote new exec --plan <PLAN_ID> --stage <STAGE> "
    '--title "<TITLE>" --apply`.',
)
ev_label_app = typer.Typer(
    help="User labels of an evidence page (user-only): show, set, commit."
)
ev_app.add_typer(ev_label_app, name="label")


def run_write(
    command: str,
    build: Callable[[ProjectView], writes.WritePlan],
    *,
    project: str | None,
    apply: bool,
    no_quiet: bool,
    as_json: bool,
) -> None:
    def action() -> CommandResult:
        with open_view(project) as view:
            plan = build(view)
            return writes.execute_plan(
                view,
                plan,
                command=command,
                apply=apply,
                no_quiet=no_quiet,
                confirm_command=confirm_command(),
            )

    run_command(command, as_json, action, render_write)


def _read_text(path: Path) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise ResearchError(
            "USAGE_PATH_NOT_FOUND", f"cannot read {path}: {error}", exit_code=2
        ) from None


def _strip_markers(view: ProjectView, body: str | None) -> str | None:
    markers = view.profile.forbidden_markers
    if body is None or not markers:
        return body
    kept = [ln for ln in body.split("\n") if not any(m in ln for m in markers)]
    return "\n".join(kept)


# ---- decisions ---------------------------------------------------------------


def _text_value(view: ProjectView, value: str | None) -> str:
    """Option text, or the contents of a file when the value is `@path`."""
    if value is None:
        return ""
    text = _read_text(Path(value[1:])) if value.startswith("@") else value
    return _strip_markers(view, text) or ""


def _decision_new(
    command: str,
    title: str,
    layer: str,
    decided: str,
    reason: str,
    options: str | None,
    lands: str | None,
    plans: str | None,
    targets: str | None,
    supersedes: str | None,
    review_by: str | None,
    status: str,
    authorization: str | None,
    project: str | None,
    apply: bool,
    no_quiet: bool,
    as_json: bool,
) -> None:
    run_write(
        command,
        lambda view: writes.plan_decision_new(
            view,
            title=title,
            layer=layer,
            decided=_text_value(view, decided),
            reason=_text_value(view, reason),
            options=_text_value(view, options),
            lands=_text_value(view, lands),
            plans=plans,
            targets=targets,
            supersedes=supersedes,
            review_by=review_by,
            status=status,
            authorization=authorization,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


TitleOption = Annotated[
    str, typer.Option("--title", help="One sentence, readable on its own.")
]
LayerOption = Annotated[str, typer.Option("--layer", help="Layer from the profile.")]
DecidedOption = Annotated[
    str, typer.Option("--decided", help="The decision (text, or @file).")
]
ReasonOption = Annotated[
    str, typer.Option("--reason", help="Why: context and drivers (text, or @file).")
]
OptionsOption = Annotated[
    str | None, typer.Option("--options", help="Alternatives considered (text, @file).")
]
LandsOption = Annotated[
    str | None, typer.Option("--lands", help="What it changes (text, or @file).")
]
PlansOption = Annotated[
    str | None, typer.Option("--plans", help="Comma list of plan ids.")
]
TargetsOption = Annotated[
    str | None, typer.Option("--targets", help="Comma list of notes affected.")
]
SupersedesOption = Annotated[
    str | None, typer.Option("--supersedes", help="Comma list of decision ids.")
]
ReviewByOption = Annotated[
    str | None, typer.Option("--review-by", help="YYYY-MM-DD: surfaces as a TODO.")
]
StatusOption = Annotated[
    str,
    typer.Option(
        "--status",
        help="proposed, or confirmed (the user already said yes in chat).",
    ),
]


@decision_app.command("new")
def decision_new(
    title: TitleOption,
    layer: LayerOption,
    decided: DecidedOption,
    reason: ReasonOption,
    options: OptionsOption = None,
    lands: LandsOption = None,
    plans: PlansOption = None,
    targets: TargetsOption = None,
    supersedes: SupersedesOption = None,
    review_by: ReviewByOption = None,
    status: StatusOption = "proposed",
    authorization: AuthorizationOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Record a decision in the store (same as `new decision`)."""
    _decision_new(
        "decision new", title, layer, decided, reason, options, lands, plans,
        targets, supersedes, review_by, status, authorization, project, apply,
        no_quiet, as_json,
    )  # fmt: skip


@new_app.command("decision")
def new_decision(
    title: TitleOption,
    layer: LayerOption,
    decided: DecidedOption,
    reason: ReasonOption,
    options: OptionsOption = None,
    lands: LandsOption = None,
    plans: PlansOption = None,
    targets: TargetsOption = None,
    supersedes: SupersedesOption = None,
    review_by: ReviewByOption = None,
    status: StatusOption = "proposed",
    authorization: AuthorizationOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Record a decision in the store."""
    _decision_new(
        "new decision", title, layer, decided, reason, options, lands, plans,
        targets, supersedes, review_by, status, authorization, project, apply,
        no_quiet, as_json,
    )  # fmt: skip


@decision_app.command("set")
def decision_set(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    status: Annotated[
        str, typer.Option("--status", help="confirmed, rejected or superseded.")
    ],
    authorization: AuthorizationOption = None,
    by: Annotated[
        str | None, typer.Option("--by", help="Successor decision (superseded).")
    ] = None,
    expected_version: Annotated[
        int | None,
        typer.Option("--expected-version", help="Refuse unless at this version."),
    ] = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Confirm, reject or supersede a decision (confirm/reject are user-only)."""
    run_write(
        "decision set",
        lambda view: writes.plan_decision_set(
            view,
            ident,
            status,
            authorization=authorization,
            by=by,
            expected_version=expected_version,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


def _export_context(project: str | None, profile_key: str = "decision_export"):
    from expnote.research.registry import open_project

    context = open_project(project)
    target = context.resolved.project_dir / getattr(context.profile, profile_key)
    return context, WriteContext.for_project(context), target


@decision_app.command("export")
def decision_export(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an export changed elsewhere.")
    ] = False,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Rewrite the JSONL export in the vault from the store (normally automatic)."""

    def action() -> CommandResult:
        context, wctx, target = _export_context(project)
        meta = {"project": context.entry.id}
        if not store_exists(context.entry.id):
            raise ResearchError(
                "NOT_FOUND",
                "this project has no decision store yet",
                exit_code=3,
                remediation="expnote decision new ... --apply",
            )
        with DecisionStore.open(context.entry.id) as store:
            if apply:
                plan, record = sync_export(
                    store, wctx, target, command="decision export", force=force
                )
                data = {
                    "changes": [record] if record else [],
                    "applied": record is not None,
                    "dryRun": False,
                    "rows": store.count(),
                    "sha256": plan.sha256,
                }
                return CommandResult(data, meta=meta)
            plan = plan_export(store, wctx, target, force=force)
            rows = store.count()
        changes = [plan.change.as_dict()] if plan.change else []
        warnings = (
            [{"code": plan.problem.code, "message": plan.problem.message}]
            if plan.problem
            else []
        )
        data = {
            "changes": changes,
            "applied": False,
            "dryRun": bool(changes),
            "confirmCommand": confirm_command() if changes else None,
            "rows": rows,
            "sha256": plan.sha256,
        }
        return CommandResult(
            data, warnings, meta, exit_code=writes.DRY_RUN_EXIT if changes else 0
        )

    run_command("decision export", as_json, action, render_write)


@decision_app.command("restore")
def decision_restore(
    source: Annotated[
        Path, typer.Option("--from", help="A decisions.jsonl export file.")
    ],
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a store that has rows.")
    ] = False,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Rebuild the decision store from an export (refuses a non-empty store)."""

    def action() -> CommandResult:
        context, wctx, target = _export_context(project)
        pid = context.entry.id
        try:
            payload = source.expanduser().read_bytes()
        except OSError as error:
            raise ResearchError(
                "USAGE_PATH_NOT_FOUND", f"cannot read {source}: {error}", exit_code=2
            ) from None
        rows, events = parse_export(payload)
        existing = 0
        if store_exists(pid):
            with DecisionStore.open(pid) as store:
                existing = store.count()
        if existing and not replace:
            raise ResearchError(
                "STORE_NOT_EMPTY",
                f"the decision store already holds {existing} decision(s)",
                exit_code=5,
                remediation="pass --replace to overwrite them",
            )
        data = {
            "rows": len(rows),
            "events": len(events),
            "replacing": existing,
            "applied": False,
            "dryRun": not apply,
            "changes": [],
        }
        meta = {"project": pid}
        if not apply:
            data["confirmCommand"] = confirm_command()
            return CommandResult(data, meta=meta, exit_code=writes.DRY_RUN_EXIT)
        warnings: list[dict] = []
        with DecisionStore.open(pid) as store:
            with store.transaction():
                restore_rows(store, rows, events, replace=replace)
            try:
                _, record = sync_export(store, wctx, target, command="decision restore")
            except ResearchError as error:
                if error.code != "EXPORT_DIVERGED":
                    raise
                warnings.append({"code": error.code, "message": error.message})
            else:
                data["changes"] = [record] if record else []
        data.update(applied=True, dryRun=False)
        return CommandResult(data, warnings, meta)

    run_command("decision restore", as_json, action, render_write)


# ---- plans, exec-plans, results ----------------------------------------------


@plan_app.command("advance")
def plan_advance(
    plan_id: Annotated[str, typer.Argument(metavar="ID")],
    to: Annotated[str, typer.Option("--to", help="The next plan status.")],
    decision: Annotated[
        str | None, typer.Option("--decision", help="Approving/closing decision id.")
    ] = None,
    superseded_by: Annotated[
        str | None, typer.Option("--superseded-by", help="Successor plan id.")
    ] = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Move a plan one step forward; exit 1 GATE_FAILED lists what is unmet."""
    run_write(
        "plan advance",
        lambda view: writes.plan_plan_advance(
            view,
            plan_id,
            to,
            decision=decision,
            superseded_by=superseded_by,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@plan_app.command("close")
def plan_close(
    plan_id: Annotated[str, typer.Argument(metavar="ID")],
    reason: Annotated[
        str, typer.Option("--reason", help="Why (kept as the decision's reason).")
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Close from any open status, skipping the gates."),
    ] = False,
    authorization: AuthorizationOption = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Close a plan on the user's word: records a confirmed decision (user-only)."""
    run_write(
        "plan close",
        lambda view: writes.plan_plan_close(
            view,
            plan_id,
            reason=reason,
            force=force,
            authorization=authorization,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@plan_app.command("reopen")
def plan_reopen(
    plan_id: Annotated[str, typer.Argument(metavar="ID")],
    decision: Annotated[
        str, typer.Option("--decision", help="Confirmed decision that reopens it.")
    ],
    mark_existing: Annotated[
        bool,
        typer.Option(
            "--mark-existing",
            help="Mark a plan that was reopened by hand (approved/running, no "
            "reopened_on); --decision is its approved_decision.",
        ),
    ] = False,
    earlier: Annotated[
        list[str] | None,
        typer.Option(
            "--earlier",
            help="With --mark-existing: place this member in the earlier cycle "
            "(repeatable; needed for members without a date).",
        ),
    ] = None,
    authorization: AuthorizationOption = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Start a new cycle of a closed/confirmed plan (needs a confirmed decision).

    Example: expnote plan reopen <PLAN_ID> --decision <DECISION_ID> --apply
    """
    run_write(
        "plan reopen",
        lambda view: writes.plan_plan_reopen(
            view,
            plan_id,
            decision=decision,
            mark_existing=mark_existing,
            earlier=tuple(earlier or ()),
            authorization=authorization,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@plan_app.command("adopt")
def plan_adopt(
    note: Annotated[
        str,
        typer.Argument(
            metavar="NOTE",
            help="Path (vault- or project-relative) or name of the draft.",
        ),
    ],
    plan_id: Annotated[str, typer.Option("--id", help="Plan id (see plan next-id).")],
    title: Annotated[
        str | None,
        typer.Option("--title", help="Default: the note's title, first H1 or name."),
    ] = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Make a hand-written draft a standard plan: frontmatter, name, links."""
    run_write(
        "plan adopt",
        lambda view: adopt.plan_plan_adopt(
            view,
            note,
            plan_id=plan_id,
            title=title,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@exec_app.command("freeze")
def exec_freeze(
    ident: Annotated[str, typer.Argument(metavar="EXEC")],
    rules_decision: Annotated[
        str | None, typer.Option("--rules-decision", help="Confirmed rules decision.")
    ] = None,
    amend: Annotated[
        str | None, typer.Option("--amend", help="Registered deviation id.")
    ] = None,
    self_approve: Annotated[
        bool,
        typer.Option(
            "--self-approve",
            help="Agent path: record a confirmed 'no new judgement rules' decision "
            "and freeze (needs --no-new-rules).",
        ),
    ] = False,
    no_new_rules: Annotated[
        bool,
        typer.Option(
            "--no-new-rules",
            help="Assert the exec-plan adds no judgement rules beyond the plan.",
        ),
    ] = False,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Store the rules hash and freeze an exec-plan (re-freeze with --amend).

    Two paths: --rules-decision <user-confirmed decision>, or, when the exec-plan
    adds no new judgement rules, --self-approve --no-new-rules (agent).
    """
    run_write(
        "exec freeze",
        lambda view: writes.plan_exec_freeze(
            view,
            ident,
            rules_decision=rules_decision,
            amend=amend,
            self_approve=self_approve,
            no_new_rules=no_new_rules,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@exec_app.command("advance")
def exec_advance(
    ident: Annotated[str, typer.Argument(metavar="EXEC")],
    to: Annotated[str, typer.Option("--to", help="running, done or aborted.")],
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Move a frozen exec-plan to running, done or aborted."""
    run_write(
        "exec advance",
        lambda view: writes.plan_exec_advance(
            view, ident, to, expected_sha256=expected_sha256
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@res_app.command("submit")
def res_submit(
    ident: Annotated[str, typer.Argument(metavar="RES")],
    deviation_impact: Annotated[
        str,
        typer.Option(
            "--deviation-impact",
            help="Your proposal: none, weak, moderate or affects.",
        ),
    ],
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Hand a drafted result to the user (draft -> first-read).

    A running plan moves on to read-out in the same write when its gates pass (one
    active result, every evidence page labelled); otherwise the result is still
    submitted and the unmet gates come back as warnings.
    """
    run_write(
        "res submit",
        lambda view: writes.plan_res_submit(
            view,
            ident,
            deviation_impact=deviation_impact,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@res_app.command("review")
def res_review(
    ident: Annotated[str, typer.Argument(metavar="RES")],
    verdict: Annotated[
        str, typer.Option("--verdict", help="pass (confirm) or return (to draft).")
    ],
    deviation_impact: Annotated[
        str | None,
        typer.Option(
            "--deviation-impact",
            help="none, weak, moderate or affects (default: the submitted one).",
        ),
    ] = None,
    remarks: Annotated[
        str | None,
        typer.Option("--remarks", help="What to revise (required with return)."),
    ] = None,
    authorization: AuthorizationOption = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Review a first-read result for the user: pass or return it (user-only).

    The user normally does this in the GUI. From the CLI it needs --authorization
    with the user's own words (exit 7 without it).
    """
    run_write(
        "res review",
        lambda view: writes.plan_res_review(
            view,
            ident,
            verdict=verdict,
            deviation_impact=deviation_impact,
            remarks=remarks,
            authorization=authorization,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


# ---- evidence ----------------------------------------------------------------


@ev_label_app.command("show")
def ev_label_show(
    ident: Annotated[str, typer.Argument(metavar="EV")],
    project: ProjectOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Show the label table, the remark and the sha256 to pass as the CAS base."""

    def action() -> CommandResult:
        with open_view(project, refresh=not no_refresh) as view:
            return CommandResult(entities.ev_detail(view, ident), meta=view.meta())

    run_command("ev label show", as_json, action)


@ev_label_app.command("set")
def ev_label_set(
    ident: Annotated[str, typer.Argument(metavar="EV")],
    row: Annotated[str, typer.Option("--row", help="Label row key (first cell).")],
    value: Annotated[
        list[str] | None,
        typer.Option("--value", help="COLUMN=TEXT (repeatable)."),
    ] = None,
    remark: Annotated[str | None, typer.Option("--remark")] = None,
    authorization: AuthorizationOption = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Fill label cells for the user (user-only: needs --authorization)."""
    run_write(
        "ev label set",
        lambda view: writes.plan_ev_label_set(
            view,
            ident,
            row=row,
            values=list(value or []),
            remark=remark,
            authorization=authorization,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@ev_label_app.command("commit")
def ev_label_commit(
    ident: Annotated[str, typer.Argument(metavar="EV")],
    authorization: AuthorizationOption = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Mark a fully filled label table labelled (user-only: needs --authorization)."""
    run_write(
        "ev label commit",
        lambda view: writes.plan_ev_label_commit(
            view, ident, authorization=authorization, expected_sha256=expected_sha256
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@ev_app.command("split")
def ev_split(
    ident: Annotated[str, typer.Argument(metavar="EV")],
    endpoint: Annotated[
        str, typer.Option("--endpoint", help="Table copied to the page.")
    ] = "M-1",
    exec_plan: Annotated[
        str | None, typer.Option("--exec", help="Exec-plan id for exec_plan.")
    ] = None,
    labelled_by: Annotated[str | None, typer.Option("--labelled-by")] = None,
    label_authorization: Annotated[str, typer.Option("--label-authorization")] = "",
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Split a legacy evidence card into a figure page and an appendix."""
    run_write(
        "ev split",
        lambda view: split.plan_ev_split(
            view,
            ident,
            endpoint=endpoint,
            exec_ident=exec_plan,
            labelled_by=labelled_by,
            label_authorization=label_authorization,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


# ---- manual TODOs ------------------------------------------------------------

ExpectedVersionOption = Annotated[
    int | None,
    typer.Option(
        "--expected-version",
        min=1,
        help="Refuse (exit 5) unless the TODO is still at this version "
        "(`version` in `todo list --json`).",
    ),
]


@todo_app.command("add")
def todo_add(
    text: Annotated[str, typer.Argument(help="Task text (one line, no [k:: v]).")],
    plan: Annotated[str | None, typer.Option("--plan", help="Plan id.")] = None,
    due: Annotated[str | None, typer.Option("--due", help="YYYY-MM-DD")] = None,
    by: Annotated[str, typer.Option("--by", help="Who adds it: user or agent.")] = (
        "agent"
    ),
    owner: Annotated[
        str | None,
        typer.Option(
            "--owner",
            help="user or agent. Required with --by agent (the default): "
            "agent for your own work, user when the user must act (start the "
            "text with that action). Optional with --by user (default: user).",
        ),
    ] = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Add a manual TODO to the project's TODO store; the new id is in the result.

    Example: expnote todo add "re-run seed 3" --owner agent --plan <PLAN_ID> --apply
    """
    run_write(
        "todo add",
        lambda view: todo_writes.plan_todo_add(
            view, text, plan=plan, due=due, by=by, owner=owner
        ),
        project=project,
        apply=apply,
        no_quiet=False,
        as_json=as_json,
    )


@todo_app.command("edit")
def todo_edit(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    text: Annotated[str | None, typer.Option("--text", help="New task text.")] = None,
    owner: Annotated[
        str | None, typer.Option("--owner", help="Hand the task to user or agent.")
    ] = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    clear_plan: Annotated[bool, typer.Option("--clear-plan")] = False,
    due: Annotated[str | None, typer.Option("--due", help="YYYY-MM-DD")] = None,
    clear_due: Annotated[bool, typer.Option("--clear-due")] = False,
    expected_version: ExpectedVersionOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Reword a manual TODO or change its owner, plan or due date (not if dropped).

    Example: expnote todo edit T-0003 --owner user --expected-version 2 --apply
    """
    run_write(
        "todo edit",
        lambda view: todo_writes.plan_todo_edit(
            view,
            ident,
            text=text,
            owner=owner,
            plan=plan,
            clear_plan=clear_plan,
            due=due,
            clear_due=clear_due,
            expected_version=expected_version,
        ),
        project=project,
        apply=apply,
        no_quiet=False,
        as_json=as_json,
    )


@todo_app.command("done")
def todo_done(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    undo: Annotated[bool, typer.Option("--undo", help="Reopen the task.")] = False,
    expected_version: ExpectedVersionOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Tick (or with --undo reopen) a manual TODO; a dropped one is refused.

    Example: expnote todo done T-0003 --apply
    """
    run_write(
        "todo done",
        lambda view: todo_writes.plan_todo_done(
            view, ident, undo=undo, expected_version=expected_version
        ),
        project=project,
        apply=apply,
        no_quiet=False,
        as_json=as_json,
    )


@todo_app.command("drop")
def todo_drop(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    reason: Annotated[str, typer.Option("--reason", help="Why it is dropped.")],
    expected_version: ExpectedVersionOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Drop a manual TODO nobody will do (final; it stays listed with its reason).

    Example: expnote todo drop T-0003 --reason "superseded by T-0007" --apply
    """
    run_write(
        "todo drop",
        lambda view: todo_writes.plan_todo_drop(
            view, ident, reason=reason, expected_version=expected_version
        ),
        project=project,
        apply=apply,
        no_quiet=False,
        as_json=as_json,
    )


@todo_app.command("migrate")
def todo_migrate(
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Move the manual TODOs of the old TODO file into the TODO store (one-shot).

    Keeps ids, gives id-less lines the next ids, writes the export, copies the file
    to the audit journal and deletes it. Refuses when the store already exists.
    Example: expnote todo migrate --apply
    """

    def action() -> CommandResult:
        with open_view(project) as view:
            return todo_writes.migrate_todos(
                view, apply=apply, confirm_command=confirm_command()
            )

    run_command("todo migrate", as_json, action, render_write)


@todo_app.command("export")
def todo_export_command(
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an export changed elsewhere.")
    ] = False,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Rewrite the JSONL export in the vault from the store (normally automatic)."""

    def action() -> CommandResult:
        context, wctx, target = _export_context(project, "todo_export")
        meta = {"project": context.entry.id}
        if not todo_store.store_exists(context.entry.id):
            raise ResearchError(
                "NOT_FOUND",
                "this project has no TODO store yet",
                exit_code=3,
                remediation="expnote todo add ... --apply (or `todo migrate`)",
            )
        with todo_store.TodoStore.open(context.entry.id) as store:
            if apply:
                plan, record = todo_export.sync_export(
                    store, wctx, target, command="todo export", force=force
                )
                data = {
                    "changes": [record] if record else [],
                    "applied": record is not None,
                    "dryRun": False,
                    "rows": store.count(),
                    "sha256": plan.sha256,
                }
                return CommandResult(data, meta=meta)
            plan = todo_export.plan_export(store, wctx, target, force=force)
            rows = store.count()
        changes = [plan.change.as_dict()] if plan.change else []
        warnings = (
            [{"code": plan.problem.code, "message": plan.problem.message}]
            if plan.problem
            else []
        )
        data = {
            "changes": changes,
            "applied": False,
            "dryRun": bool(changes),
            "confirmCommand": confirm_command() if changes else None,
            "rows": rows,
            "sha256": plan.sha256,
        }
        return CommandResult(
            data, warnings, meta, exit_code=writes.DRY_RUN_EXIT if changes else 0
        )

    run_command("todo export", as_json, action, render_write)


@todo_app.command("restore")
def todo_restore(
    source: Annotated[Path, typer.Option("--from", help="A todos.jsonl export file.")],
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a store that has rows.")
    ] = False,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Rebuild the TODO store from an export (refuses a non-empty store)."""

    def action() -> CommandResult:
        context, wctx, target = _export_context(project, "todo_export")
        pid = context.entry.id
        try:
            payload = source.expanduser().read_bytes()
        except OSError as error:
            raise ResearchError(
                "USAGE_PATH_NOT_FOUND", f"cannot read {source}: {error}", exit_code=2
            ) from None
        rows, events = todo_export.parse_export(payload)
        existing = 0
        if todo_store.store_exists(pid):
            with todo_store.TodoStore.open(pid) as store:
                existing = store.count()
        if existing and not replace:
            raise ResearchError(
                "STORE_NOT_EMPTY",
                f"the TODO store already holds {existing} TODO(s)",
                exit_code=5,
                remediation="pass --replace to overwrite them",
            )
        data = {
            "rows": len(rows),
            "events": len(events),
            "replacing": existing,
            "applied": False,
            "dryRun": not apply,
            "changes": [],
        }
        meta = {"project": pid}
        if not apply:
            data["confirmCommand"] = confirm_command()
            return CommandResult(data, meta=meta, exit_code=writes.DRY_RUN_EXIT)
        warnings: list[dict] = []
        with todo_store.TodoStore.open(pid) as store:
            with store.transaction():
                todo_export.restore_rows(store, rows, events, replace=replace)
            try:
                _, record = todo_export.sync_export(
                    store, wctx, target, command="todo restore"
                )
            except ResearchError as error:
                if error.code != "EXPORT_DIVERGED":
                    raise
                warnings.append({"code": error.code, "message": error.message})
            else:
                data["changes"] = [record] if record else []
        data.update(applied=True, dryRun=False)
        return CommandResult(data, warnings, meta)

    run_command("todo restore", as_json, action, render_write)


# ---- deviations --------------------------------------------------------------


@deviation_app.command("add")
def deviation_add(
    experiment: Annotated[str, typer.Option("--experiment")],
    content: Annotated[str, typer.Option("--content", help="What deviated.")],
    impact: Annotated[str, typer.Option("--impact", help="Effect on validity.")],
    disposition: Annotated[str, typer.Option("--disposition")] = "",
    cards: Annotated[str, typer.Option("--cards", help="Related notes.")] = "",
    found: Annotated[str | None, typer.Option("--found", help="When found.")] = None,
    date: Annotated[str | None, typer.Option("--date", help="YYYY-MM-DD")] = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Append a row with the next id to the deviation register."""
    run_write(
        "deviation add",
        lambda view: writes.plan_deviation_add(
            view,
            experiment=experiment,
            content=content,
            impact=impact,
            disposition=disposition,
            cards=cards,
            found=found,
            date=date,
            expected_sha256=expected_sha256,
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@deviation_app.command("close")
def deviation_close(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    disposition: Annotated[
        str | None, typer.Option("--disposition", help="Replace the disposition text.")
    ] = None,
    expected_sha256: ExpectedOption = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Close one register row."""
    run_write(
        "deviation close",
        lambda view: writes.plan_deviation_close(
            view, ident, disposition=disposition, expected_sha256=expected_sha256
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


# ---- new notes ---------------------------------------------------------------


@new_app.command("plan")
def new_plan(
    plan_id: Annotated[str, typer.Option("--id", help="Plan id.")],
    title: Annotated[str, typer.Option("--title")],
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Create a plan page from the template."""
    run_write(
        "new plan",
        lambda view: newnote.plan_new_plan(view, plan_id=plan_id, title=title),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@new_app.command("exec")
def new_exec(
    plan_id: Annotated[str, typer.Option("--plan", help="Plan id.")],
    stage: Annotated[str, typer.Option("--stage")],
    title: Annotated[str, typer.Option("--title")],
    exec_id: Annotated[
        str | None, typer.Option("--id", help="Default: <plan id>-<stage>.")
    ] = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Create an exec-plan from the template."""
    run_write(
        "new exec",
        lambda view: newnote.plan_new_exec(
            view, plan_id=plan_id, stage=stage, title=title, exec_id=exec_id
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@new_app.command("ev")
def new_ev(
    plan_id: Annotated[str, typer.Option("--plan", help="Plan id.")],
    title: Annotated[str, typer.Option("--title")],
    exec_plan: Annotated[
        str | None, typer.Option("--exec", help="Exec-plan id that produced it.")
    ] = None,
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Create an evidence page and its appendix twin."""
    run_write(
        "new ev",
        lambda view: newnote.plan_new_ev(
            view, plan_id=plan_id, title=title, exec_ident=exec_plan
        ),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@new_app.command("res")
def new_res(
    plan_id: Annotated[str, typer.Option("--plan", help="Plan id.")],
    title: Annotated[str, typer.Option("--title")],
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Create the final result note of a plan, as a draft.

    The id is RES-<PLAN>; in a reopened plan's later cycle it is RES-<PLAN>-cycleN
    (N = the current cycle, 2 after the first reopen). Fill it, then `res submit`.
    """
    run_write(
        "new res",
        lambda view: newnote.plan_new_res(view, plan_id=plan_id, title=title),
        project=project,
        apply=apply,
        no_quiet=no_quiet,
        as_json=as_json,
    )


@new_app.command("profile")
def new_profile(
    project_dir: Annotated[
        Path, typer.Option("--project-dir", help="The project's folder in the vault.")
    ],
    project_id: Annotated[str, typer.Option("--id", help="Project id.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Default: the folder name.")
    ] = None,
    vault_root: Annotated[Path | None, typer.Option("--vault-root")] = None,
    images_dir: Annotated[
        Path | None, typer.Option("--images-dir", help="Recorded in the profile.")
    ] = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """Scaffold the profile note (never overwrites; `project init` does more)."""

    def action() -> CommandResult:
        folder = project_dir.expanduser().absolute()
        root = resolve_vault_root(folder, vault_root)
        existing = find_profile_notes(folder)
        if existing:
            raise ResearchError(
                "TARGET_EXISTS",
                f"{existing[0]} is already a profile note; this command never "
                "overwrites",
                exit_code=5,
            )
        images_rel = None
        if images_dir is not None:
            images = images_dir.expanduser().absolute()
            try:
                images_rel = images.relative_to(root).as_posix()
            except ValueError:
                raise ResearchError(
                    "USAGE_PATH_OUTSIDE_VAULT",
                    f"--images-dir {images} lies outside the vault {root}",
                    exit_code=2,
                ) from None
        context = WriteContext(
            project_id=project_id,
            vault_key=hashlib.sha256(str(root).encode()).hexdigest()[:16],
            vault_root=root,
            actor=os.environ.get("EXPNOTE_ACTOR") or "cli",
        )
        edit = newnote.profile_edit(
            folder,
            project_id=project_id,
            name=name or folder.name,
            images_rel=images_rel,
        )
        changes = plan_changes(context, [edit])
        data = {"created": [str(edit.path)], "project": project_id}
        meta = {"project": project_id}
        if not apply:
            data.update(
                changes=[c.as_dict() for c in changes],
                applied=False,
                dryRun=True,
                confirmCommand=confirm_command(),
            )
            return CommandResult(data, meta=meta, exit_code=writes.DRY_RUN_EXIT)
        result = apply_changes(context, changes, command="new profile")
        data.update(changes=result.applied, applied=True, dryRun=False)
        return CommandResult(data, result.warnings, meta)

    run_command("new profile", as_json, action, render_write)
