"""Read-only research commands: reindex, search, plan/decision/exec/ev/res/deviation
lists and shows, plan bundle, todo, next, doctor, guide research."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from expnote.research import adopt, entities
from expnote.research.bundle import (
    DEFAULT_MAX_BYTES,
    SECTIONS,
    build_bundle,
    byte_size,
    fit_sections,
    parse_sections,
    spill_path,
    write_spill,
)
from expnote.research.doctor import doctor_data, run_doctor
from expnote.research.doctor_writes import run_doctor_writes
from expnote.research.envelope import (
    CommandResult,
    run_command,
    select_fields,
    success_envelope,
)
from expnote.research.errors import ResearchError
from expnote.research.guide import RESEARCH_GUIDE, render_research_guide
from expnote.research.home import plan_rows
from expnote.research.index import refresh_index
from expnote.research.registry import open_project
from expnote.research.search import SearchFilters, search_notes
from expnote.research.todos import (
    all_todos,
    filter_todos,
    next_steps,
)
from expnote.research.view import ProjectView, open_view

JsonOption = Annotated[bool, typer.Option("--json", help="Print the JSON envelope.")]
ProjectOption = Annotated[
    str | None, typer.Option("--project", help="Project id (default: active project).")
]
NoRefreshOption = Annotated[
    bool, typer.Option("--no-refresh", help="Read the index as it is.")
]
FieldsOption = Annotated[
    str | None, typer.Option("--fields", help="Comma list of item keys to print.")
]
LimitOption = Annotated[int, typer.Option("--limit", min=1, help="Items per page.")]
CursorOption = Annotated[
    str | None, typer.Option("--cursor", help="Continue from meta.nextCursor.")
]

plan_app = typer.Typer(
    help="Plans: list, show, bundle, advance, close, reopen, adopt, next-id.",
    epilog="Examples: `expnote plan bundle <PLAN_ID> --json` (all context of a plan); "
    "`expnote plan advance <PLAN_ID> --to running --apply`; "
    "`expnote plan reopen <PLAN_ID> --decision <DECISION_ID> --apply`. "
    "Writes are dry runs (exit 4) unless --apply.",
)
decision_app = typer.Typer(
    help="Decisions: list, show, new, set, export, restore.",
    epilog='Example: `expnote decision new --title "<T>" --layer <LAYER> '
    '--decided "<WHAT>" --reason "<WHY>" --plans <PLAN_ID> --apply`; '
    "`expnote decision set <DECISION_ID> --status confirmed --authorization "
    '"<USER_WORDS>" --apply`.',
)
exec_app = typer.Typer(
    help="Exec-plans: list, show, freeze, advance.",
    epilog="Example: `expnote exec freeze <EXEC_ID> --rules-decision <DECISION_ID> "
    "--apply`; `expnote exec advance <EXEC_ID> --to running --apply`.",
)
ev_app = typer.Typer(
    help="Evidence pages: list, split, label (label show, set, commit).",
    epilog="Labels are the user's: `expnote ev label show <EV> --json`.",
)
res_app = typer.Typer(
    help="Results: list, confirm.",
    epilog="Example: `expnote res confirm <RES_ID> --decision <DECISION_ID> "
    "--deviation-impact <none|weak|moderate|affects> --apply`.",
)
deviation_app = typer.Typer(
    help="Deviation register: list, show, add, close.",
    epilog="Example: `expnote deviation list --json`.",
)
todo_app = typer.Typer(
    help="TODOs, derived and manual: list, add, done, adopt.",
    epilog="Example: `expnote todo list --owner agent --json`; `expnote next --json` "
    "orders the agent's steps.",
)


def _one_of(option: str, value: str | None, allowed: tuple[str, ...], optional=False):
    if value is None and optional:
        return
    if value not in allowed:
        raise ResearchError(
            "USAGE_BAD_OPTION",
            f"{option} takes one of {', '.join(allowed)}",
            exit_code=2,
        )


def _table(rows: list[dict], columns: list[str]) -> str:
    return "\n".join(
        "\t".join("" if row.get(c) is None else str(row.get(c)) for c in columns)
        for row in rows
    )


def _list_command(
    command: str,
    key: str,
    columns: list[str],
    build: Callable[[ProjectView], list[dict]],
    *,
    project: str | None,
    as_json: bool,
    no_refresh: bool,
    fields: str | None,
    limit: int,
    cursor: str | None,
    warnings_of: Callable[[ProjectView], list[dict]] | None = None,
) -> None:
    def action() -> CommandResult:
        with open_view(project, refresh=not no_refresh) as view:
            items = build(view)
            page, next_cursor = entities.paginate(items, limit, cursor)
            return CommandResult(
                {key: select_fields(page, fields)},
                warnings=warnings_of(view) if warnings_of else [],
                meta=view.meta(total=len(items), limit=limit, nextCursor=next_cursor),
            )

    run_command(command, as_json, action, lambda data: _table(data[key], columns))


def _show_command(
    command: str,
    build: Callable[[ProjectView], dict],
    *,
    project: str | None,
    as_json: bool,
    no_refresh: bool,
) -> None:
    def action() -> CommandResult:
        with open_view(project, refresh=not no_refresh) as view:
            return CommandResult(build(view), meta=view.meta())

    run_command(command, as_json, action)


# ---- reindex and search -----------------------------------------------------------


def reindex_command(
    project: ProjectOption = None,
    full: Annotated[
        bool, typer.Option("--full", help="Rebuild the index from scratch.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Bring the research index up to date (it never touches the vault)."""

    def action() -> CommandResult:
        context = open_project(project)
        stats = refresh_index(context, full=full)
        data = {
            "files": stats.files,
            "notes": stats.notes,
            "linksResolved": stats.links_resolved,
            "linksUnresolved": stats.links_unresolved,
            "images": stats.images,
            "conflictFiles": stats.conflicts,
            "elapsedMs": stats.elapsed_ms,
            "rebuilt": stats.rebuilt,
            "filesChanged": stats.files_changed,
        }
        meta = {
            "project": context.entry.id,
            "index": stats.meta(),
            "pathNote": context.resolved.path_note,
        }
        return CommandResult(data, warnings=stats.warnings, meta=meta)

    run_command("reindex", as_json, action)


def search_command(
    query: Annotated[
        str, typer.Argument(help="Words to find (3+ characters: full text).")
    ] = "",
    project: ProjectOption = None,
    note_type: Annotated[str | None, typer.Option("--type")] = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    linked_to: Annotated[
        str | None, typer.Option("--linked-to", help="Only notes that link to STEM.")
    ] = None,
    backlinks_of: Annotated[
        str | None,
        typer.Option("--backlinks-of", help="List the notes that link to STEM."),
    ] = None,
    images: Annotated[
        bool, typer.Option("--images", help="Search image files by name.")
    ] = False,
    all_vault: Annotated[
        bool, typer.Option("--all-vault", help="Search outside the project too.")
    ] = False,
    fields: FieldsOption = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 50,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Search notes (trigram full text; LIKE for terms shorter than 3 characters)."""

    def action() -> CommandResult:
        filters = SearchFilters(
            note_type, plan, status, linked_to, backlinks_of, images, all_vault
        )
        with open_view(project, refresh=not no_refresh) as view:
            found = search_notes(view, query, filters, limit=limit, cursor=cursor)
            next_cursor = found.pop("_nextCursor")
            total = found.pop("total")
            found["hits"] = select_fields(found["hits"], fields)
            return CommandResult(
                found, meta=view.meta(total=total, limit=limit, nextCursor=next_cursor)
            )

    run_command(
        "search",
        as_json,
        action,
        lambda d: "\n".join(h["snippet"] for h in d["hits"]),
    )


# ---- plans ------------------------------------------------------------------------


@plan_app.command("list")
def plan_list(
    project: ProjectOption = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    legacy: Annotated[
        bool | None, typer.Option("--legacy/--no-legacy", help="Filter by legacy flag.")
    ] = None,
    include_archived: Annotated[
        bool,
        typer.Option("--include-archived", help="Also list archived plan pages."),
    ] = False,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List plans with counts and the status of their final result."""

    def build(view: ProjectView) -> list[dict]:
        return plan_rows(
            view, status=status, legacy=legacy, include_archived=include_archived
        )

    _list_command(
        "plan list",
        "plans",
        ["id", "status", "resStatus", "title"],
        build,
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@plan_app.command("next-id")
def plan_next_id(
    project: ProjectOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """The next free plan id (rule in the output; `id` is null if none fits)."""

    def build(view: ProjectView) -> dict:
        return {"id": adopt.next_plan_id(view), "rule": adopt.NEXT_ID_RULE}

    def action() -> CommandResult:
        with open_view(project, refresh=not no_refresh) as view:
            return CommandResult(build(view), meta=view.meta())

    def render(data: dict) -> str:
        return data["id"] or (
            "no id to suggest: there is no plan to continue from, or the next id "
            "would not fit ids.plan_regex"
        )

    run_command("plan next-id", as_json, action, render)


@plan_app.command("show")
def plan_show(
    plan_id: Annotated[str, typer.Argument(metavar="ID")],
    project: ProjectOption = None,
    include_body: Annotated[bool, typer.Option("--include-body")] = False,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Show one plan: frontmatter, sha256, outline and what gates its next move."""

    def build(view: ProjectView) -> dict:
        page = entities.find_plan(view, plan_id)
        return {
            "plan": entities.plan_core(view, page, include_body),
            "frontmatter": page.fm,
            "gates": entities.plan_gates(view, page),
        }

    _show_command(
        "plan show", build, project=project, as_json=as_json, no_refresh=no_refresh
    )


@plan_app.command("bundle")
def plan_bundle(
    plan_id: Annotated[str, typer.Argument(metavar="ID")],
    project: ProjectOption = None,
    depth: Annotated[int, typer.Option("--depth", min=1, max=2)] = 1,
    include_body: Annotated[bool, typer.Option("--include-body")] = False,
    sections: Annotated[
        str | None,
        typer.Option("--sections", help=f"Comma list from: {', '.join(SECTIONS)}."),
    ] = None,
    max_bytes: Annotated[
        int, typer.Option("--max-bytes", min=1000)
    ] = DEFAULT_MAX_BYTES,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the full bundle to FILE.")
    ] = None,
    fields: Annotated[
        str | None, typer.Option("--fields", help="Comma list of sections to print.")
    ] = None,
    cursor: Annotated[
        str | None, typer.Option("--cursor", help="Continue from meta.nextCursor.")
    ] = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """One call with all materials of a plan, bounded by --max-bytes."""

    def action() -> CommandResult:
        chosen = parse_sections(sections)
        picked = parse_sections(fields, "--fields")
        if chosen and picked:
            chosen = [s for s in chosen if s in picked]
        else:
            chosen = chosen or picked
        with open_view(project, refresh=not no_refresh) as view:
            data, warnings = build_bundle(
                view,
                plan_id,
                depth=depth,
                include_body=include_body,
                sections=chosen,
            )
            full_meta = view.meta(bytes=byte_size(data), maxBytes=max_bytes)
            if out is not None:
                full = success_envelope(
                    "plan bundle", CommandResult(data, warnings, {**full_meta})
                )
                target = write_spill(out.expanduser().absolute(), full)
                meta = {
                    **full_meta,
                    "truncated": False,
                    "spillFile": str(target),
                    "nextCursor": None,
                    "sections": [],
                }
                return CommandResult({}, warnings, meta)
            shown, next_section = fit_sections(data, max_bytes, cursor)
            spill = None
            if next_section is not None:
                full = success_envelope(
                    "plan bundle",
                    CommandResult(
                        data,
                        warnings,
                        {**full_meta, "truncated": False, "sections": list(data)},
                    ),
                )
                spill = str(write_spill(spill_path(plan_id, full), full))
            meta = {
                **full_meta,
                "bytes": byte_size(shown),
                "truncated": next_section is not None,
                "spillFile": spill,
                "nextCursor": next_section,
                "sections": list(shown),
            }
            return CommandResult(shown, warnings, meta)

    run_command("plan bundle", as_json, action)


# ---- decisions, exec-plans, evidence, results, deviations -------------------------


@decision_app.command("list")
def decision_list(
    project: ProjectOption = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    layer: Annotated[str | None, typer.Option("--layer")] = None,
    since: Annotated[str | None, typer.Option("--since", help="YYYY-MM-DD")] = None,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List decisions (decision files and, until migrated, the legacy table)."""
    _list_command(
        "decision list",
        "decisions",
        ["id", "date", "status", "layer", "title"],
        lambda view: entities.list_decisions(
            view, plan=plan, status=status, layer=layer, since=since
        ),
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@decision_app.command("show")
def decision_show(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    project: ProjectOption = None,
    include_body: Annotated[bool, typer.Option("--include-body")] = False,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Show a decision and the notes that cite it."""
    _show_command(
        "decision show",
        lambda view: entities.decision_detail(view, ident, include_body),
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
    )


@exec_app.command("list")
def exec_list(
    project: ProjectOption = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List exec-plans with their freeze state."""

    def build(view: ProjectView) -> list[dict]:
        if plan:
            entities.find_plan(view, plan)
        rows = [
            entities.exec_item(view, n) for n in entities.notes_of(view, "exec-plan")
        ]
        rows.sort(key=lambda r: (str(r["plan"]), str(r["id"]), r["stem"]))
        return [
            r
            for r in rows
            if (not plan or r["plan"] == plan) and (not status or r["status"] == status)
        ]

    _list_command(
        "exec list",
        "execPlans",
        ["id", "plan", "status", "frozen", "stem"],
        build,
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@exec_app.command("show")
def exec_show(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    project: ProjectOption = None,
    include_body: Annotated[bool, typer.Option("--include-body")] = False,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Show an exec-plan (by id or file stem) with its stored and recomputed hash."""
    _show_command(
        "exec show",
        lambda view: entities.exec_detail(view, ident, include_body),
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
    )


@ev_app.command("list")
def ev_list(
    project: ProjectOption = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    label_state: Annotated[
        str | None,
        typer.Option("--label-state", help="filled, partial, empty, missing or na."),
    ] = None,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List evidence pages with their label state."""

    def build(view: ProjectView) -> list[dict]:
        if plan:
            entities.find_plan(view, plan)
        rows = [entities.ev_item(view, n) for n in entities.notes_of(view, "evidence")]
        rows.sort(key=lambda r: (str(r["plan"]), r["stem"]))
        return [
            r
            for r in rows
            if (not plan or r["plan"] == plan)
            and (not status or r["status"] == status)
            and (not label_state or r["labelState"] == label_state)
        ]

    _list_command(
        "ev list",
        "ev",
        ["stem", "plan", "status", "labelState"],
        build,
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@res_app.command("list")
def res_list(
    project: ProjectOption = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List results (final read-outs) with their confirmation state."""

    def build(view: ProjectView) -> list[dict]:
        if plan:
            entities.find_plan(view, plan)
        rows = [
            entities.result_item(view, n) for n in entities.notes_of(view, "result")
        ]
        rows.sort(key=lambda r: (str(r["plan"]), r["stem"]))
        return [
            r
            for r in rows
            if (not plan or r["plan"] == plan) and (not status or r["status"] == status)
        ]

    _list_command(
        "res list",
        "results",
        ["id", "plan", "status", "stem"],
        build,
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@deviation_app.command("list")
def deviation_list(
    project: ProjectOption = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List the deviation register."""
    _list_command(
        "deviation list",
        "deviations",
        ["id", "date", "status", "found", "summary"],
        lambda view: entities.list_deviations(view, plan=plan, status=status),
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
    )


@deviation_app.command("show")
def deviation_show(
    ident: Annotated[str, typer.Argument(metavar="ID")],
    project: ProjectOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Show one deviation row."""
    _show_command(
        "deviation show",
        lambda view: entities.deviation_detail(view, ident),
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
    )


# ---- todos, next, doctor, guide ---------------------------------------------------


@todo_app.command("list")
def todo_list(
    project: ProjectOption = None,
    owner: Annotated[str | None, typer.Option("--owner", help="user or agent.")] = None,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    plan: Annotated[str | None, typer.Option("--plan")] = None,
    source: Annotated[
        str, typer.Option("--source", help="derived, manual or all.")
    ] = "all",
    include_done: Annotated[
        bool, typer.Option("--include-done", help="Also show ticked manual TODOs.")
    ] = False,
    fields: FieldsOption = None,
    limit: LimitOption = 200,
    cursor: CursorOption = None,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """List derived and manual TODOs, the user's first."""
    warnings: list[dict] = []

    def build(view: ProjectView) -> list[dict]:
        _one_of("--source", source, ("derived", "manual", "all"))
        _one_of("--owner", owner, ("user", "agent"), optional=True)
        items, found = all_todos(view, source=source, include_done=include_done)
        warnings.extend(found)
        return filter_todos(items, owner=owner, kind=kind, plan=plan)

    _list_command(
        "todo list",
        "todos",
        ["id", "owner", "severity", "message"],
        build,
        project=project,
        as_json=as_json,
        no_refresh=no_refresh,
        fields=fields,
        limit=limit,
        cursor=cursor,
        warnings_of=lambda view: warnings,
    )


def next_command(
    project: ProjectOption = None,
    owner: Annotated[
        str, typer.Option("--owner", help="Whose steps to list: agent or user.")
    ] = "agent",
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Ordered next steps with the exact command to run.

    Example: expnote next --project <ID> --json (add --owner user for the user's list).
    """

    def action() -> CommandResult:
        _one_of("--owner", owner, ("user", "agent"))
        with open_view(project, refresh=not no_refresh) as view:
            data, warnings = next_steps(view, owner)
            return CommandResult(data, warnings, view.meta())

    def render(data: dict) -> str:
        lines = [
            f"{s['order']}. [{s['kind']}] {s['message']}"
            + (f"\n   $ {s['command']}" if s["command"] else "")
            for s in data["steps"]
        ]
        waiting = data["waiting"]
        lines.append(f"waiting on {waiting['owner']}: {waiting['count']} item(s)")
        return "\n".join(lines)

    run_command("next", as_json, action, render)


def doctor_command(
    project: ProjectOption = None,
    fix_list: Annotated[
        bool, typer.Option("--fix-list", help="List every affected path.")
    ] = False,
    probe_sync: Annotated[
        bool,
        typer.Option(
            "--probe-sync",
            help="Write a dot-prefixed temp file and an ordinary file into the "
            "project folder to test your sync client (needs --apply).",
        ),
    ] = False,
    cleanup: Annotated[
        bool, typer.Option("--cleanup", help="With --probe-sync: remove the probe.")
    ] = False,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Delete stale .expnote.*.tmp files; with --probe-sync, write it.",
        ),
    ] = False,
    no_refresh: NoRefreshOption = False,
    as_json: JsonOption = False,
) -> None:
    """Environment and hygiene checks (never content; see `check`)."""

    def action() -> CommandResult:
        if cleanup and not probe_sync:
            raise ResearchError(
                "USAGE_BAD_OPTION", "--cleanup goes with --probe-sync", exit_code=2
            )
        checks, meta = run_doctor(project, refresh=not no_refresh)
        data = doctor_data(checks, fix_list)
        code = 1 if data["summary"]["fail"] else 0
        if apply or probe_sync:
            writes = run_doctor_writes(
                project, apply=apply, probe_sync=probe_sync, cleanup=cleanup
            )
            data.update(writes.data)
            if writes.exit_code:
                data["dryRun"] = writes.exit_code == 4
                code = writes.exit_code
        return CommandResult(data, meta=meta, exit_code=code)

    def render(data: dict) -> str:
        lines = [
            f"{c['status'].upper():5} {c['id']}: {c['message']}" for c in data["checks"]
        ]
        temps = data.get("staleTemps")
        if temps:
            verb = "removed" if temps["applied"] else "found (add --apply to remove)"
            count = len(temps["removed"] if temps["applied"] else temps["found"])
            lines.append(f"stale temp files {verb}: {count}")
        probe = data.get("probe")
        if probe and "removed" in probe:
            lines.append(f"probe files removed: {len(probe['removed'])}")
        elif probe:
            lines += [
                f"probe temp file:  {probe['tempFile']}",
                f"probe plain file: {probe['plainFile']}",
                probe["instructions"] if probe["applied"] else "dry-run: add --apply",
            ]
        return "\n".join(lines)

    run_command("doctor", as_json, action, render)


def guide_research(as_json: bool) -> None:
    """Print the research agent guide (called by `guide research`)."""
    run_command(
        "guide research",
        as_json,
        lambda: CommandResult(RESEARCH_GUIDE),
        lambda _: render_research_guide(),
    )
