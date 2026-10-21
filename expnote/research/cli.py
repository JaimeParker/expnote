"""Typer commands of the research layer: `project ...` and `check`."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Annotated

import typer

from expnote.research.envelope import CommandResult, run_command, select_fields
from expnote.research.errors import ResearchError
from expnote.research.index import read_index_stats
from expnote.research.profile import read_profile_note
from expnote.research.registry import (
    ProjectEntry,
    ResolvedProject,
    is_inside,
    load_registry,
    new_vault_id,
    profile_or_raise,
    rebase,
    registry_path,
    revalidate,
    save_registry,
    select_entry,
)

project_app = typer.Typer(help="Research projects: registry bindings and profile.")

JsonOption = Annotated[bool, typer.Option("--json", help="Print the JSON envelope.")]
ProjectOption = Annotated[
    str | None, typer.Option("--project", help="Project id (default: active project).")
]


def _project_meta(project_id: str, path_note: str | None) -> dict:
    return {"project": project_id, "index": None, "pathNote": path_note}


def _resolved_paths(resolved: ResolvedProject) -> dict:
    return {
        "absVaultRoot": str(resolved.vault_root),
        "absProjectDir": str(resolved.project_dir),
        "absImagesDir": str(resolved.images_dir),
        "absProfileNote": str(resolved.profile_note),
    }


# ---- project register / list / show / use / rebind -----------------------------


@project_app.command("register")
def project_register(
    vault_root: Annotated[
        Path, typer.Option("--vault-root", help="Obsidian vault root.")
    ],
    project_dir: Annotated[Path, typer.Option("--project-dir")],
    images_dir: Annotated[Path, typer.Option("--images-dir")],
    profile_note: Annotated[Path, typer.Option("--profile-note")],
    project_id: Annotated[
        str | None, typer.Option("--id", help="Defaults to the profile's project_id.")
    ] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    workspace: Annotated[
        list[str] | None,
        typer.Option("--workspace", help="Linked workspace (repeatable)."),
    ] = None,
    alternate: Annotated[
        list[str] | None,
        typer.Option("--alternate", help="Alternate vault root (repeatable)."),
    ] = None,
    quiet_window_seconds: Annotated[int, typer.Option("--quiet-window-seconds")] = 10,
    as_json: JsonOption = False,
) -> None:
    """Bind a project to its vault and profile note (the note must exist)."""

    def action() -> CommandResult:
        root = str(vault_root.expanduser().absolute())
        paths = {
            "--project-dir": str(project_dir.expanduser().absolute()),
            "--images-dir": str(images_dir.expanduser().absolute()),
            "--profile-note": str(profile_note.expanduser().absolute()),
        }
        for option, value in paths.items():
            if not is_inside(value, root):
                raise ResearchError(
                    "USAGE_PATH_OUTSIDE_VAULT",
                    f"{option} {value} lies outside --vault-root {root}",
                    exit_code=2,
                )
        note = read_profile_note(Path(paths["--profile-note"]))
        profile = profile_or_raise(note, project_id)
        registry = load_registry()
        entry_id = project_id or profile.project_id
        entry = ProjectEntry(
            id=entry_id,
            name=name or Path(paths["--project-dir"]).name,
            vault_id=new_vault_id(registry, entry_id, root),
            vault_root=root,
            vault_root_alternates=list(alternate or []),
            project_dir=paths["--project-dir"],
            images_dir=paths["--images-dir"],
            profile_note=paths["--profile-note"],
            workspaces=list(workspace or []),
            quiet_window_seconds=quiet_window_seconds,
        )
        resolved = revalidate(entry)
        registry.projects[entry_id] = entry
        if registry.active_project is None:
            registry.active_project = entry_id
        save_registry(registry)
        data = {
            "project": entry_id,
            **_resolved_paths(resolved),
            "profileSha256": note.sha256,
            "workspaces": entry.workspaces,
            "profile": {"found": True, "parsed": True, "projectId": profile.project_id},
        }
        return CommandResult(data, meta=_project_meta(entry_id, resolved.path_note))

    run_command("project register", as_json, action)


@project_app.command("list")
def project_list(as_json: JsonOption = False) -> None:
    """List registered projects."""

    def action() -> CommandResult:
        registry = load_registry()
        projects = []
        for project_id in sorted(registry.projects):
            entry = registry.projects[project_id]
            try:
                revalidate(entry)
                reachable = True
            except ResearchError:
                reachable = False
            projects.append(
                {
                    "id": project_id,
                    "name": entry.name,
                    "active": registry.active_project == project_id,
                    "vaultOk": reachable,
                }
            )
        return CommandResult(
            {"projects": projects}, meta={"registry": str(registry_path())}
        )

    def render(data: dict) -> str:
        return "\n".join(
            f"{'*' if p['active'] else ' '} {p['id']}  {p['name']}"
            f"{'' if p['vaultOk'] else '  (vault unavailable)'}"
            for p in data["projects"]
        )

    run_command("project list", as_json, action, render)


@project_app.command("show")
def project_show(
    project_id: Annotated[str, typer.Argument(metavar="ID")],
    as_json: JsonOption = False,
) -> None:
    """Show a project's registry entry, resolved paths and profile status."""

    def action() -> CommandResult:
        entry = select_entry(load_registry(), project_id)
        resolved = revalidate(entry)
        note = read_profile_note(resolved.profile_note)
        data = {
            "project": dataclasses.asdict(entry),
            **_resolved_paths(resolved),
            "profile": {
                "found": note.found,
                "parsed": note.parsed,
                "projectId": note.profile.project_id if note.profile else None,
                "schema": note.profile.schema if note.profile else None,
                "problems": note.problems,
            },
            "profileSha256": note.sha256,
            "index": read_index_stats(entry.vault_id),
        }
        return CommandResult(data, meta=_project_meta(entry.id, resolved.path_note))

    run_command("project show", as_json, action)


@project_app.command("use")
def project_use(
    project_id: Annotated[str, typer.Argument(metavar="ID")],
    as_json: JsonOption = False,
) -> None:
    """Make a project the default for `--project`."""

    def action() -> CommandResult:
        registry = load_registry()
        select_entry(registry, project_id)
        registry.active_project = project_id
        save_registry(registry)
        return CommandResult(
            {"activeProject": project_id}, meta=_project_meta(project_id, None)
        )

    run_command("project use", as_json, action)


@project_app.command("rebind")
def project_rebind(
    project_id: Annotated[str, typer.Argument(metavar="ID")],
    vault_root: Annotated[Path, typer.Option("--vault-root")],
    as_json: JsonOption = False,
) -> None:
    """Move a project to a new vault root (drive letter or mount change)."""

    def action() -> CommandResult:
        registry = load_registry()
        entry = select_entry(registry, project_id)
        new_root = str(vault_root.expanduser().absolute())
        rebound = dataclasses.replace(
            entry,
            vault_root=new_root,
            project_dir=rebase(entry.project_dir, entry.vault_root, new_root),
            images_dir=rebase(entry.images_dir, entry.vault_root, new_root),
            profile_note=rebase(entry.profile_note, entry.vault_root, new_root),
        )
        resolved = revalidate(rebound)
        profile_or_raise(read_profile_note(resolved.profile_note), entry.id)
        registry.projects[project_id] = rebound
        save_registry(registry)
        data = {
            "project": project_id,
            "previousVaultRoot": entry.vault_root,
            **_resolved_paths(resolved),
        }
        return CommandResult(data, meta=_project_meta(project_id, None))

    run_command("project rebind", as_json, action)


# ---- check ---------------------------------------------------------------------------


def _parse_levels(value: str | None) -> set[str] | None:
    if not value:
        return None
    levels = {part.strip().upper() for part in value.split(",") if part.strip()}
    unknown = levels - {"FAIL", "WARN", "INFO"}
    if unknown:
        raise ResearchError(
            "USAGE_BAD_LEVEL",
            f"unknown level(s) {sorted(unknown)}; use fail,warn,info",
            exit_code=2,
        )
    return levels


def check_data(
    result,
    *,
    levels: set[str] | None = None,
    codes: set[str] | None = None,
    compat: str | None = None,
    fields: str | None = None,
) -> tuple[dict, bool]:
    """(`check` data, whether any FAIL remains) for a `run_checks` result."""
    from expnote.research.checks import split_new_codes, summarize

    regular, new = split_new_codes(result.findings)
    if compat == "vault-py":
        new = []
    wanted = codes or set()

    def visible(findings):
        return [
            f.as_dict()
            for f in findings
            if (levels is None or f.level in levels)
            and (not wanted or f.code in wanted)
        ]

    data = {
        "summary": summarize(regular),
        "findings": select_fields(visible(regular), fields),
        "newCodes": select_fields(visible(new), fields),
        "newCodesSummary": summarize(new),
        "stats": result.stats,
        "scope": {
            **result.scope,
            "compat": compat,
            "filtered": bool(levels or wanted),
        },
    }
    failed = data["summary"]["fail"] + data["newCodesSummary"]["fail"] > 0
    return data, failed


def check_command(
    project: ProjectOption = None,
    plan: Annotated[
        str | None, typer.Option("--plan", help="Check one plan and its members.")
    ] = None,
    level: Annotated[
        str | None,
        typer.Option("--level", help="Show only these levels: fail,warn,info."),
    ] = None,
    code: Annotated[
        list[str] | None,
        typer.Option("--code", help="Show only this code (repeatable)."),
    ] = None,
    compat: Annotated[
        str | None,
        typer.Option("--compat", help="'vault-py': hide codes vault.py never had."),
    ] = None,
    fields: Annotated[
        str | None,
        typer.Option("--fields", help="Comma list of finding keys to print."),
    ] = None,
    no_refresh: Annotated[
        bool, typer.Option("--no-refresh", help="Read the index as it is.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Check the research notes of a project (read-only; never writes)."""

    def action() -> CommandResult:
        from expnote.research.checks import run_checks
        from expnote.research.view import open_view

        if compat not in (None, "vault-py"):
            raise ResearchError(
                "USAGE_BAD_COMPAT", "--compat accepts only 'vault-py'", exit_code=2
            )
        levels = _parse_levels(level)
        with open_view(project, refresh=not no_refresh) as view:
            result = run_checks(view.snapshot, plan)
            meta = view.meta()
        data, failed = check_data(
            result, levels=levels, codes=set(code or []), compat=compat, fields=fields
        )
        return CommandResult(data, meta=meta, exit_code=1 if failed else 0)

    def render(data: dict) -> str:
        lines = [
            f"{f['level']} {f['code']} {f['where']}: {f['message']}"
            for f in [*data["findings"], *data["newCodes"]]
            if "level" in f
        ]
        s = data["summary"]
        lines.append(f"FAIL {s['fail']}  WARN {s['warn']}  INFO {s['info']}")
        return "\n".join(lines)

    run_command("check", as_json, action, render)
