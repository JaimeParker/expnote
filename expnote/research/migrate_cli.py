"""`expnote migrate ...`: the P4 vault migration, one subcommand per stage.

Every command is a dry run unless `--apply` is passed; `plan`, `verify` and the
dry runs write nothing into the vault (plan writes its review files under the
research state directory).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from expnote.research import migrate_backup as backup
from expnote.research.cliutil import confirm_command, render_write
from expnote.research.envelope import CommandResult, run_command
from expnote.research.errors import ResearchError
from expnote.research.migrate import (
    STEP_NAMES,
    Migration,
    now_stamp,
    parse_step,
    run_step,
)
from expnote.research.migrate_plan import build_plan
from expnote.research.migrate_profile import OFF2ON_PROFILE_TEXT
from expnote.research.migrate_verify import (
    golden_snapshot,
    render_gates,
    run_cleanup,
    run_verify,
)
from expnote.research.newnote import profile_text
from expnote.research.profile import parse_profile_text, read_profile_note
from expnote.research.registry import (
    ProjectContext,
    load_registry,
    open_project,
    revalidate,
    select_entry,
)
from expnote.research.writeback import (
    FileEdit,
    WriteContext,
    apply_changes,
    plan_changes,
    research_state_dir,
)

DRY_RUN_EXIT = 4
migrate_app = typer.Typer(
    help="One-shot vault migration to schema 3 (design section 11). Dry-run unless "
    "--apply. Order: backup, profile, plan, apply --step 1..10, verify, cleanup."
)
JsonOption = Annotated[bool, typer.Option("--json", help="Print the JSON envelope.")]
ProjectOption = Annotated[
    str | None, typer.Option("--project", help="Project id (default: active project).")
]
ApplyOption = Annotated[
    bool, typer.Option("--apply", help="Write; default is a dry run.")
]
NoQuietOption = Annotated[
    bool,
    typer.Option("--no-quiet", help="Skip the quiet window (audited); you are away."),
]


def _meta(project_id: str) -> dict:
    return {"project": project_id}


def _context_lenient(project: str | None) -> ProjectContext:
    """The registered project; an unreadable profile falls back to the Off2On one."""
    try:
        return open_project(project)
    except ResearchError as error:
        if error.code not in ("PROFILE_INVALID", "PROFILE_NOT_FOUND"):
            raise
        entry = select_entry(load_registry(), project)
        resolved = revalidate(entry)
        note = parse_profile_text(OFF2ON_PROFILE_TEXT)
        return ProjectContext(resolved, note.profile, note.sha256 or "")


# ---- backup (M0) ---------------------------------------------------------------------


@migrate_app.command("backup")
def backup_command(
    project: ProjectOption = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Folder for the backup (outside the vault)."),
    ] = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """M0: tar.gz of everything the migration may touch, restore test, golden."""

    def action() -> CommandResult:
        context = _context_lenient(project)
        resolved = context.resolved
        vault_root, project_dir = resolved.vault_root, resolved.project_dir
        project_id = context.entry.id
        out_dir = (
            out.expanduser().absolute()
            if out
            else research_state_dir() / "backups" / project_id / backup.utc_stamp()
        )
        backup.refuse_inside_vault(out_dir, vault_root)
        m = Migration(context, WriteContext.for_project(context, "migration"))
        roots = [r for r in m.projection_roots() if r.is_dir()]
        stems = backup.projected_stems(vault_root, roots)
        shared = backup.shared_stems(vault_root, roots, stems)
        outside_all = backup.outside_notes(
            vault_root, project_dir, roots, set(stems) - shared
        )
        outside = [o["path"] for o in outside_all if not o["archived"]]
        members, empty_dirs = backup.backup_members(
            vault_root, project_dir, roots, outside
        )
        size = sum(p.stat().st_size for p in members.values())
        data = {
            "dir": str(out_dir),
            "files": len(members),
            "emptyDirs": len(empty_dirs),
            "bytes": size,
            "projectionRoots": [str(r) for r in roots],
            "outsideNotes": outside,
            "applied": False,
            "dryRun": not apply,
        }
        if not apply:
            data["confirmCommand"] = confirm_command()
            return CommandResult(data, meta=_meta(project_id), exit_code=DRY_RUN_EXIT)
        golden = golden_snapshot(context)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "golden.json").write_text(
            json.dumps(golden, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        record = backup.write_backup(
            out_dir,
            members,
            empty_dirs,
            {
                "dir": str(out_dir),
                "project": project_id,
                "createdAt": now_stamp(),
                "vaultRoot": str(vault_root),
                "projectDir": str(project_dir),
                "gitHead": backup.vault_git_head(vault_root),
                "outsideNotes": outside,
                "outsideArchivedLeftAlone": len(outside_all) - len(outside),
                "projectionRoots": [str(r) for r in roots],
            },
        )
        m.remember_projection()
        m.write_json("backup.json", record)
        data.update(
            applied=True,
            dryRun=False,
            archive=record["archive"],
            restoreOk=record["restoreOk"],
            restoreMismatches=record["restoreMismatches"],
            gitHead=record["gitHead"],
            golden=golden["summary"],
        )
        if not record["restoreOk"]:
            raise ResearchError(
                "BACKUP_RESTORE_FAILED",
                "the restore test found differences; do not migrate",
                exit_code=1,
                details={"mismatches": record["restoreMismatches"][:10]},
            )
        return CommandResult(data, meta=_meta(project_id))

    def render(d: dict) -> str:
        lines = [
            f"backup dir  {d['dir']}",
            f"members     {d['files']} files ({d['bytes']} bytes), "
            f"{d['emptyDirs']} empty dirs",
            f"projection  {', '.join(d['projectionRoots']) or '(none)'}",
            f"outside     {', '.join(d['outsideNotes']) or '(none)'}",
        ]
        if d["applied"]:
            lines.append(
                f"archive     {d['archive']} (restore ok: {d['restoreOk']}, "
                f"git HEAD {d['gitHead']}); golden {d['golden']}"
            )
        else:
            lines.append(
                f"dry-run: nothing written. To apply:\n  {d['confirmCommand']}"
            )
        return "\n".join(lines)

    run_command("migrate backup", as_json, action, render)


# ---- profile (M1) --------------------------------------------------------------------


@migrate_app.command("profile")
def profile_command(
    project: ProjectOption = None,
    replace: Annotated[
        bool,
        typer.Option(
            "--replace",
            help="Also replace a hand-edited profile (the generic scaffold is "
            "replaced without it).",
        ),
    ] = False,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """M1: write the Off2On profile note; the user approves it before `plan`."""

    def action() -> CommandResult:
        entry = select_entry(load_registry(), project)
        resolved = revalidate(entry)
        note_path = resolved.profile_note
        desired = parse_profile_text(OFF2ON_PROFILE_TEXT)
        if desired.profile is None or desired.profile.project_id != entry.id:
            raise ResearchError(
                "PROFILE_ID_MISMATCH",
                f"the Off2On profile has project_id "
                f"{desired.profile.project_id if desired.profile else '?'!r}, the "
                f"project is registered as {entry.id!r}",
                exit_code=2,
                remediation="register the project with --id off2on",
            )
        existing = (
            note_path.read_text(encoding="utf-8") if note_path.is_file() else None
        )
        images_rel = os.path.relpath(resolved.images_dir, resolved.vault_root)
        scaffold = profile_text(entry.id, entry.name, Path(images_rel).as_posix())
        context = WriteContext(
            project_id=entry.id,
            vault_key=entry.vault_id,
            vault_root=resolved.vault_root,
            quiet_window_seconds=entry.quiet_window_seconds,
            actor="migration",
        )
        if existing == OFF2ON_PROFILE_TEXT:
            changes = []
        elif existing is None:
            changes = plan_changes(
                context,
                [
                    FileEdit(
                        note_path,
                        "create",
                        "profile:off2on",
                        new_text=OFF2ON_PROFILE_TEXT,
                    )
                ],
            )
        elif existing == scaffold or replace:
            changes = plan_changes(
                context,
                [
                    FileEdit(
                        note_path,
                        "migrate",
                        "profile:off2on",
                        transform=lambda _text: OFF2ON_PROFILE_TEXT,
                    )
                ],
            )
        else:
            raise ResearchError(
                "PROFILE_DIFFERS",
                f"{note_path.name} is neither the generic scaffold nor the Off2On "
                "profile; it may be hand-edited",
                exit_code=5,
                remediation="review it, then re-run with --replace",
            )
        data = {
            "profileNote": str(note_path),
            "changes": [c.as_dict() for c in changes],
            "applied": False,
            "dryRun": not apply,
            "upToDate": not changes,
        }
        if not apply:
            if changes:
                data["confirmCommand"] = confirm_command()
            return CommandResult(
                data, meta=_meta(entry.id), exit_code=DRY_RUN_EXIT if changes else 0
            )
        result = apply_changes(
            context, changes, command="migrate profile", no_quiet=no_quiet
        )
        parsed = read_profile_note(note_path)
        if not parsed.parsed:
            raise ResearchError(
                "PROFILE_INVALID",
                "the written profile does not parse: " + "; ".join(parsed.problems),
                exit_code=6,
            )
        data.update(changes=result.applied, applied=True, dryRun=False)
        return CommandResult(data, result.warnings, _meta(entry.id))

    run_command("migrate profile", as_json, action, render_write)


# ---- plan ----------------------------------------------------------------------------


@migrate_app.command("plan")
def plan_command(project: ProjectOption = None, as_json: JsonOption = False) -> None:
    """Plan every mechanical step and write the review tables (no vault writes)."""

    def action() -> CommandResult:
        m = Migration.open(project)
        plan = build_plan(m)
        return CommandResult(plan, meta=_meta(m.project_id))

    def render(d: dict) -> str:
        lines = [f"plan        {d['planFile']}", f"diff        {d['diff']}", ""]
        for step in d["steps"]:
            lines.append(
                f"step {step['step']:>2} {step['name']:<11} "
                f"{len(step['changes']):>4} change(s) "
                f"{len(step['deletes'])} delete(s) {len(step['renames'])} rename(s)  "
                f"{step['title']}"
            )
            lines += [f"          problem: {p}" for p in step["problems"]]
        lines.append("")
        for key, path in d["tableFiles"].items():
            lines.append(f"table ({key}) {d['tables'][key]['rows']:>4} rows  {path}")
        lines += [f"proposal    {p}" for p in d["proposals"]]
        return "\n".join(lines)

    run_command("migrate plan", as_json, action, render)


# ---- apply ---------------------------------------------------------------------------


def _require_backup(m: Migration) -> None:
    record = m.read_json("backup.json")
    if not record or not record.get("restoreOk"):
        raise ResearchError(
            "MIGRATION_NO_BACKUP",
            "no tested backup is recorded for this project",
            exit_code=1,
            remediation="expnote migrate backup --apply",
        )


@migrate_app.command("apply")
def apply_command(
    step: Annotated[
        str,
        typer.Option(
            "--step",
            help="1-10, a name (" + ", ".join(STEP_NAMES.values()) + ") or all.",
        ),
    ],
    project: ProjectOption = None,
    apply: ApplyOption = False,
    no_quiet: NoQuietOption = False,
    as_json: JsonOption = False,
) -> None:
    """Apply one migration step (idempotent; one burst through the write layer)."""

    def action() -> CommandResult:
        m = Migration.open(project, no_quiet=no_quiet)
        _require_backup(m)
        steps = parse_step(step)
        results, warnings, any_change = [], [], False
        for number in steps:
            plan, applied = run_step(m, number, apply=apply)
            entry = {**plan, "applied": applied is not None}
            if applied is not None:
                entry["result"] = {
                    k: v for k, v in applied.items() if k != "warnings"
                }
                warnings += applied["warnings"]
                any_change |= bool(
                    applied["applied"]
                    or applied["renamed"]
                    or applied["deleted"]
                    or applied["imported"]
                )
            else:
                any_change |= bool(
                    plan["changes"]
                    or plan["deletes"]
                    or plan["renames"]
                    or plan["imports"]
                )
            results.append(entry)
        data = {"steps": results, "dryRun": not apply}
        if not apply:
            data["confirmCommand"] = confirm_command()
            return CommandResult(
                data,
                warnings,
                _meta(m.project_id),
                exit_code=DRY_RUN_EXIT if any_change else 0,
            )
        return CommandResult(data, warnings, _meta(m.project_id))

    def render(d: dict) -> str:
        lines = []
        for r in d["steps"]:
            res = r.get("result") or {}
            counts = (
                f"applied {len(res.get('applied', []))} edit(s), "
                f"{len(res.get('renamed', []))} rename(s), "
                f"{len(res.get('deleted', []))} delete(s), "
                f"{res.get('imported', 0)} import(s)"
                if r["applied"]
                else f"would change {len(r['changes'])} file(s), "
                f"{len(r['deletes'])} delete(s), {len(r['renames'])} rename(s), "
                f"{len(r['imports'])} import(s)"
            )
            lines.append(
                f"step {r['step']:>2} {r['name']:<11} {counts}  {r['summary']}"
            )
            lines += [f"      note: {n}" for n in r["notes"][:6]]
            lines += [f"      PROBLEM: {p}" for p in r["problems"][:6]]
        if d["dryRun"]:
            lines.append(
                f"dry-run: nothing written. To apply:\n  {d['confirmCommand']}"
            )
        return "\n".join(lines)

    run_command("migrate apply", as_json, action, render)


# ---- verify --------------------------------------------------------------------------


@migrate_app.command("verify")
def verify_command(
    project: ProjectOption = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Count FAILs that wait for a judgement (final RES) too."
        ),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """M4: the machine-checkable gates of design 11.3, as a pass/fail table."""

    def action() -> CommandResult:
        m = Migration.open(project)
        gates = run_verify(m, strict=strict)
        failed = any(g.status == "FAIL" for g in gates)
        data = {"gates": [g.as_dict() for g in gates], "passed": not failed}
        m.write_json("verify.json", {**data, "at": now_stamp()})
        return CommandResult(
            data, meta=_meta(m.project_id), exit_code=1 if failed else 0
        )

    def render(d: dict) -> str:
        from expnote.research.migrate_verify import Gate

        return render_gates([Gate(**g) for g in d["gates"]])

    run_command("migrate verify", as_json, action, render)


# ---- cleanup -------------------------------------------------------------------------


@migrate_app.command("cleanup")
def cleanup_command(
    project: ProjectOption = None,
    apply: ApplyOption = False,
    as_json: JsonOption = False,
) -> None:
    """M5: delete the projection folder, previews, empty dot-dirs and vault code."""

    def action() -> CommandResult:
        m = Migration.open(project)
        data = run_cleanup(m, apply=apply)
        if not apply:
            data["confirmCommand"] = confirm_command()
            return CommandResult(data, meta=_meta(m.project_id), exit_code=DRY_RUN_EXIT)
        return CommandResult(data, meta=_meta(m.project_id))

    def render(d: dict) -> str:
        lines = [
            f"files       {len(d['files'])} to delete",
            f"directories {len(d['directories'])} to remove",
        ]
        lines += [f"kept        {u['path']} ({u['why']})" for u in d["unknownKept"]]
        if d["applied"]:
            lines.append(
                f"deleted {d['deletedFiles']} file(s), removed "
                f"{d['removedDirectories']} director(ies); pre-images are in the "
                "journal"
            )
        else:
            lines.append(
                f"dry-run: nothing deleted. To apply:\n  {d['confirmCommand']}"
            )
        return "\n".join(lines)

    run_command("migrate cleanup", as_json, action, render)

