"""The vault-writing parts of `doctor`: stale temp cleanup and the sync probe.

Both go through `writeback` and are dry-run unless `--apply` (design 6.5, 10). The
probe answers one question empirically: does the user's sync client upload a
dot-prefixed temp file the way it uploads an ordinary one?
"""

from __future__ import annotations

import dataclasses

from expnote.research.errors import ResearchError
from expnote.research.registry import open_project
from expnote.research.writeback import (
    PROBE_DIR_NAME,
    WriteContext,
    apply_changes,
    delete_paths,
    plan_probe,
    probe_paths,
    remove_empty_directory,
    stale_temp_files,
)

DRY_RUN_EXIT = 4
SYNC_QUESTION = (
    "Check your WebDAV/sync client: did it upload the dot-prefixed temp file, "
    "the ordinary file, both or neither? Then remove them with "
    "`expnote doctor --probe-sync --cleanup --apply`."
)


@dataclasses.dataclass
class DoctorWrites:
    data: dict
    exit_code: int = 0


def run_doctor_writes(
    project: str | None, *, apply: bool, probe_sync: bool, cleanup: bool
) -> DoctorWrites:
    if cleanup and not probe_sync:
        raise ResearchError(
            "USAGE_BAD_OPTION", "--cleanup goes with --probe-sync", exit_code=2
        )
    context = open_project(project)
    ctx = WriteContext.for_project(context)
    root = context.resolved.vault_root
    data: dict = {}
    exit_code = 0
    stale = stale_temp_files(root, context.profile.skip_dirs)
    if apply or stale:
        removed = delete_paths(ctx, stale, command="doctor") if apply else []
        data["staleTemps"] = {
            "found": [str(p.relative_to(root).as_posix()) for p in stale],
            "removed": removed,
            "applied": apply,
        }
    if probe_sync:
        probe, code = _probe(ctx, context, apply=apply, cleanup=cleanup)
        data["probe"] = probe
        exit_code = code
    return DoctorWrites(data, exit_code)


def _rel(root, path) -> str | None:
    return None if path is None else path.relative_to(root).as_posix()


def _probe(ctx: WriteContext, context, *, apply: bool, cleanup: bool):
    project_dir = context.resolved.project_dir
    root = context.resolved.vault_root
    existing = probe_paths(project_dir)
    present = [p for p in (existing["temp"], existing["plain"]) if p is not None]
    if cleanup:
        removed: list[str] = []
        if apply and present:
            removed = delete_paths(ctx, present, command="doctor --probe-sync")
            remove_empty_directory(ctx, existing["dir"])
        probe = {
            "dir": _rel(root, existing["dir"]),
            "files": [_rel(root, p) for p in present],
            "removed": removed,
            "applied": apply,
        }
        return probe, (DRY_RUN_EXIT if present and not apply else 0)
    changes, paths = plan_probe(ctx, project_dir)
    probe = {
        "dir": _rel(root, paths["dir"]),
        "tempFile": _rel(root, paths["temp"]),
        "plainFile": _rel(root, paths["plain"]),
        "absTempFile": str(paths["temp"]),
        "absPlainFile": str(paths["plain"]),
        "alreadyPresent": not changes,
        "applied": False,
        "instructions": SYNC_QUESTION,
        "dirName": PROBE_DIR_NAME,
    }
    if not apply:
        probe["changes"] = [c.as_dict() for c in changes]
        return probe, (DRY_RUN_EXIT if changes else 0)
    result = apply_changes(ctx, changes, command="doctor --probe-sync")
    probe["applied"] = True
    probe["created"] = [a["path"] for a in result.applied]
    return probe, 0
