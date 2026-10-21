"""The dashboard payload of one project (read-only; nothing is cached or written)."""

from __future__ import annotations

from datetime import datetime

from expnote.research import entities
from expnote.research.checks import split_new_codes, summarize
from expnote.research.doctor import doctor_data, run_doctor
from expnote.research.index import linked_workspaces, read_index_stats
from expnote.research.runs import recent_runs
from expnote.research.todos import all_todos, derived_todos, manual_todos
from expnote.research.view import ProjectView

TODO_FIELDS = ("id", "source", "kind", "owner", "severity", "ref", "message", "plan")
RED_STATUSES = ("fail", "warn")
LIFECYCLE = (*entities.PLAN_ORDER, *entities.PLAN_TERMINAL)


def plan_rows(
    view: ProjectView,
    *,
    status: str | None = None,
    legacy: bool | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """The `plan list` rows (shared by the CLI and the GUI)."""
    derived, _ = derived_todos(view, include_runs=False)
    open_by_plan: dict[str, int] = {}
    for item in [*derived, *manual_todos(view)]:
        if item["plan"]:
            open_by_plan[item["plan"]] = open_by_plan.get(item["plan"], 0) + 1
    rows = []
    for page in entities.plan_pages(view, include_archived=include_archived):
        item = entities.plan_item(view, page, open_by_plan.get(str(page.get("id")), 0))
        if status and item["status"] != status:
            continue
        if legacy is not None and item["legacy"] != legacy:
            continue
        rows.append(item)
    return rows


def _plan_groups(view: ProjectView) -> dict:
    rows = plan_rows(view)
    by_status: dict[str, list[dict]] = {}
    for row in rows:
        by_status.setdefault(row["status"] or "unknown", []).append(row)
    ordered = sorted(
        by_status,
        key=lambda s: (LIFECYCLE.index(s) if s in LIFECYCLE else len(LIFECYCLE), s),
    )
    all_pages = entities.notes_of(view, "plan")
    return {
        "total": len(rows),
        "byStatus": {status: len(by_status[status]) for status in ordered},
        "groups": [{"status": s, "plans": by_status[s]} for s in ordered],
        "hidden": {
            "archived": sum(1 for p in all_pages if view.snapshot.is_archived(p)),
            "noPlanId": sum(
                1 for p in all_pages if not entities.has_plan_id_shape(view, p)
            ),
        },
    }


def _decisions_awaiting(view: ProjectView) -> dict:
    proposed = entities.list_decisions(view, status="proposed")
    keep = ("id", "number", "date", "layer", "title", "plans", "path")
    return {
        "awaiting": [{k: row[k] for k in keep} for row in proposed],
        "total": len(view.snapshot.decisions),
    }


def _todo_summary(view: ProjectView, limit: int) -> tuple[dict, list[dict]]:
    items, warnings = all_todos(view, source="all", include_runs=True)
    manual = manual_todos(view)
    slim = [{k: item[k] for k in TODO_FIELDS} for item in items]
    return {
        "items": slim[:limit],
        "total": len(items),
        "user": sum(1 for i in items if i["owner"] == "user"),
        "agent": sum(1 for i in items if i["owner"] != "user"),
        "derived": sum(1 for i in items if i["source"] == "derived"),
        "manual": len(manual),
    }, warnings


def _runs(view: ProjectView, limit: int) -> tuple[dict, list[dict]]:
    names = linked_workspaces(view.context)
    if not names:
        return {"workspaces": [], "recent": []}, []
    recent, problems = recent_runs(names, limit)
    warnings = [
        {"code": "RUNS_UNAVAILABLE", "message": f"workspace {w}: {why}"}
        for w, why in problems.items()
    ]
    return {"workspaces": names, "recent": recent}, warnings


def _index(view: ProjectView) -> dict:
    stats = read_index_stats(view.context.entry.vault_id)
    built_at = stats.get("builtAt")
    age = None
    if built_at:
        try:
            age = int((view.clock() - datetime.fromisoformat(built_at)).total_seconds())
        except ValueError:
            age = None
    return {
        **stats,
        "refreshedNow": view.index.stats.refreshed,
        "filesChanged": view.index.stats.files_changed,
        "ageSeconds": max(age, 0) if age is not None else None,
    }


def _doctor(view: ProjectView) -> dict:
    checks, _ = run_doctor(view.project_id, refresh=False, check_freshness=False)
    data = doctor_data(checks)
    flags = [
        {k: c[k] for k in ("id", "status", "message", "remediation", "count")}
        for c in data["checks"]
        if c["status"] in RED_STATUSES
    ]
    return {"summary": data["summary"], "flags": flags}


def build_home(
    view: ProjectView, *, todo_limit: int = 12, run_limit: int = 8
) -> tuple[dict, list[dict]]:
    """(dashboard data, warnings) for one project."""
    todos, warnings = _todo_summary(view, todo_limit)
    runs, run_warnings = _runs(view, run_limit)
    regular, new = split_new_codes(view.checks().findings)
    entry = view.context.entry
    data = {
        "project": {"id": entry.id, "name": entry.name},
        "plans": _plan_groups(view),
        "decisions": _decisions_awaiting(view),
        "todos": todos,
        "runs": runs,
        "index": _index(view),
        "doctor": _doctor(view),
        "findings": summarize([*regular, *new]),
        "today": view.today(),
        "display": display_strings(view),
    }
    return data, [*warnings, *run_warnings]


def display_strings(view: ProjectView) -> dict:
    """The profile's `[display]` strings (label state names and the like)."""
    section = view.profile.raw.get("display")
    return section if isinstance(section, dict) else {}
