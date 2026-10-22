"""`project init`: the first step for a vault project (design section 3, onboarding).

Given only the project's folder inside an Obsidian vault, find the vault root, read
or scaffold the profile note, resolve the images directory and register the
project. Dry-run by default; re-running on a registered project changes nothing.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.newnote import profile_edit
from expnote.research.profile import PROFILE_TYPE, ProfileNote, read_profile_note
from expnote.research.registry import (
    Registry,
    is_inside,
    load_registry,
    new_vault_id,
    register_project,
    registry_path,
    revalidate,
)
from expnote.research.writeback import (
    WriteContext,
    apply_changes,
    plan_changes,
)

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PROFILE_HEAD_RE = re.compile(r"^type:\s*[\"']?" + re.escape(PROFILE_TYPE), re.M)
DRY_RUN_EXIT = 4


def same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.realpath(left) == os.path.realpath(right)


def find_vault_root(project_dir: Path) -> Path | None:
    """The nearest ancestor (or the folder itself) that holds `.obsidian/`."""
    for candidate in [project_dir, *project_dir.parents]:
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def slugify_id(folder_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", folder_name)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")


def find_profile_notes(project_dir: Path) -> list[Path]:
    """`type: project-profile` notes directly inside the project folder."""
    found = []
    for path in sorted(project_dir.glob("*.md")):
        try:
            head = path.read_text(encoding="utf-8")[:4000]
        except (OSError, UnicodeDecodeError):
            continue
        front = head.split("\n---", 2)[0] if head.startswith("---") else ""
        if PROFILE_HEAD_RE.search(front):
            found.append(path)
    return found


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def resolve_vault_root(project_dir: Path, vault_root: Path | None) -> Path:
    if not project_dir.is_dir():
        raise ResearchError(
            "USAGE_PATH_NOT_FOUND",
            f"--project-dir {project_dir} is not a folder",
            exit_code=2,
            remediation="create the project folder in Obsidian first",
        )
    if vault_root is not None:
        root = _absolute(vault_root)
        if not (root / ".obsidian").is_dir():
            raise ResearchError(
                "VAULT_ROOT_NOT_FOUND",
                f"{root} has no .obsidian/ folder, so it is not an Obsidian vault",
                exit_code=2,
                remediation="pass the folder you open in Obsidian as --vault-root",
            )
    else:
        root = find_vault_root(project_dir)
        if root is None:
            raise ResearchError(
                "VAULT_ROOT_NOT_FOUND",
                f"no folder above {project_dir} contains .obsidian/",
                exit_code=2,
                remediation=(
                    "pass --vault-root PATH: the folder you open as a vault in "
                    "Obsidian (it contains .obsidian/)"
                ),
            )
    if not is_inside(str(project_dir), str(root)):
        raise ResearchError(
            "USAGE_PATH_OUTSIDE_VAULT",
            f"--project-dir {project_dir} lies outside the vault root {root}",
            exit_code=2,
        )
    return root


def _check_inside(option: str, path: Path, root: Path) -> None:
    if not is_inside(str(path), str(root)):
        raise ResearchError(
            "USAGE_PATH_OUTSIDE_VAULT",
            f"{option} {path} lies outside the vault root {root}",
            exit_code=2,
        )


def choose_profile_note(project_dir: Path, requested: Path | None) -> Path | None:
    """The existing profile note to reuse, None when one must be scaffolded."""
    if requested is not None:
        path = _absolute(requested)
        return path if path.is_file() else None
    found = find_profile_notes(project_dir)
    if len(found) > 1:
        raise ResearchError(
            "AMBIGUOUS_PROFILE",
            f"{len(found)} project-profile notes in {project_dir}",
            exit_code=2,
            remediation="pass --profile-note PATH to choose one",
            details={"candidates": [str(p) for p in found]},
        )
    return found[0] if found else None


def _read_valid_profile(path: Path) -> ProfileNote:
    note = read_profile_note(path)
    if not note.parsed:
        raise ResearchError(
            "PROFILE_INVALID",
            f"profile note {path.name} is invalid: " + "; ".join(note.problems),
            exit_code=6,
            remediation="fix the profile note in Obsidian, then run project init again",
            details={"problems": note.problems},
        )
    return note


def images_from_profile(note: ProfileNote, root: Path) -> Path | None:
    section = note.profile.raw.get("images") if note.profile else None
    value = section.get("dir") if isinstance(section, dict) else None
    return root / value if isinstance(value, str) and value else None


def _next_commands(project_id: str) -> list[str]:
    return [f"expnote check --project {project_id}", "expnote web"]


def _binding(entry, registry: Registry) -> dict:
    try:
        resolved = revalidate(entry)
        paths = {
            "absVaultRoot": str(resolved.vault_root),
            "absProjectDir": str(resolved.project_dir),
            "absImagesDir": str(resolved.images_dir),
            "absProfileNote": str(resolved.profile_note),
            "vaultOk": True,
        }
    except ResearchError:
        paths = {
            "absVaultRoot": entry.vault_root,
            "absProjectDir": entry.project_dir,
            "absImagesDir": entry.images_dir,
            "absProfileNote": entry.profile_note,
            "vaultOk": False,
        }
    return {
        "project": entry.id,
        "name": entry.name,
        "alreadyRegistered": True,
        **paths,
        "workspaces": entry.workspaces,
        "activeProject": registry.active_project,
        "registryFile": str(registry_path()),
        "changes": [],
        "applied": False,
        "dryRun": False,
        "nextCommands": _next_commands(entry.id),
    }


def project_init(
    *,
    project_dir: Path,
    project_id: str | None,
    name: str | None,
    vault_root: Path | None,
    images_dir: Path | None,
    profile_note: Path | None,
    workspaces: list[str],
    apply: bool,
    confirm_command: str | None = None,
) -> CommandResult:
    project_dir = _absolute(project_dir)
    registry = load_registry()
    bound = next(
        (
            e
            for e in registry.projects.values()
            if same_path(e.project_dir, project_dir)
        ),
        None,
    )
    if bound is not None:
        if project_id and project_id != bound.id:
            raise ResearchError(
                "PROJECT_EXISTS",
                f"{project_dir} is already registered as {bound.id!r}, not "
                f"{project_id!r}",
                exit_code=2,
                remediation="expnote project list",
            )
        return CommandResult(_binding(bound, registry), meta={"project": bound.id})

    root = resolve_vault_root(project_dir, vault_root)
    existing = choose_profile_note(project_dir, profile_note)
    note = _read_valid_profile(existing) if existing is not None else None
    if project_id is not None and not ID_RE.fullmatch(project_id):
        raise ResearchError(
            "USAGE_BAD_ID",
            f"--id {project_id!r} may use letters, digits, '.', '_' and '-'",
            exit_code=2,
        )
    entry_id = project_id or (
        note.profile.project_id if note and note.profile else None
    )
    if entry_id is None:
        entry_id = slugify_id(project_dir.name)
        if not entry_id:
            raise ResearchError(
                "USAGE_MISSING_OPTION",
                f"cannot derive a project id from the folder name {project_dir.name!r}",
                exit_code=2,
                remediation="pass --id ID (letters, digits, '-')",
            )
    if note and note.profile and note.profile.project_id != entry_id:
        raise ResearchError(
            "PROFILE_ID_MISMATCH",
            f"the profile note says project_id {note.profile.project_id!r}, "
            f"not {entry_id!r}",
            exit_code=2,
            remediation=f"re-run with --id {note.profile.project_id}",
        )
    if entry_id in registry.projects:
        raise ResearchError(
            "PROJECT_ID_TAKEN",
            f"project id {entry_id!r} is registered for another folder "
            f"({registry.projects[entry_id].project_dir})",
            exit_code=2,
            remediation="choose another --id, or `expnote project rebind`",
        )
    display_name = name or project_dir.name

    images = _absolute(images_dir) if images_dir is not None else None
    if images is None and note is not None:
        images = images_from_profile(note, root)
    if images is None:
        raise ResearchError(
            "IMAGES_DIR_REQUIRED",
            "this project's images folder is unknown",
            exit_code=2,
            remediation=(
                "pass --images-dir PATH: the vault folder that holds the figures "
                "(inside the vault)"
            ),
        )
    _check_inside("--images-dir", images, root)
    scaffold_path = _absolute(profile_note) if profile_note is not None else None
    if scaffold_path is not None:
        _check_inside("--profile-note", scaffold_path, root)

    vault_id = new_vault_id(registry, entry_id, str(root))
    context = WriteContext(
        project_id=entry_id,
        vault_key=vault_id,
        vault_root=root,
        actor=os.environ.get("EXPNOTE_ACTOR") or "cli",
    )
    changes = []
    if existing is None:
        edit = profile_edit(
            project_dir,
            project_id=entry_id,
            name=display_name,
            images_rel=images.relative_to(root).as_posix()
            if is_inside(str(images), str(root))
            else None,
            path=scaffold_path,
        )
        changes = plan_changes(context, [edit])
        target_note = edit.path
    else:
        target_note = existing

    data = {
        "project": entry_id,
        "name": display_name,
        "alreadyRegistered": False,
        "absVaultRoot": str(root),
        "absProjectDir": str(project_dir),
        "absImagesDir": str(images),
        "absProfileNote": str(target_note),
        "imagesDirExists": images.is_dir(),
        "profile": {
            "found": existing is not None,
            "willCreate": existing is None,
            "projectId": entry_id,
        },
        "registryEntry": {
            "id": entry_id,
            "name": display_name,
            "vault_id": vault_id,
            "vault_root": str(root),
            "project_dir": str(project_dir),
            "images_dir": str(images),
            "profile_note": str(target_note),
            "workspaces": workspaces,
        },
        "registryFile": str(registry_path()),
        "activeProject": registry.active_project or entry_id,
        "nextCommands": _next_commands(entry_id),
    }
    warnings = []
    if not images.is_dir():
        warnings.append(
            {
                "code": "IMAGES_DIR_MISSING",
                "message": f"{images} does not exist yet; create it first",
            }
        )
    meta = {"project": entry_id}
    if not apply:
        data.update(
            changes=[c.as_dict() for c in changes],
            applied=False,
            dryRun=True,
            confirmCommand=confirm_command,
        )
        return CommandResult(data, warnings, meta, exit_code=DRY_RUN_EXIT)

    applied = apply_changes(context, changes, command="project init")
    warnings += applied.warnings
    registered, resolved = register_project(
        vault_root=root,
        project_dir=project_dir,
        images_dir=images,
        profile_note=target_note,
        project_id=entry_id,
        name=display_name,
        workspaces=workspaces,
        vault_id=vault_id,
    )
    data.update(
        changes=applied.applied,
        applied=True,
        dryRun=False,
        profileSha256=registered["profileSha256"],
        activeProject=load_registry().active_project,
    )
    data["profile"]["willCreate"] = False
    data["profile"]["created"] = existing is None
    meta["pathNote"] = resolved.path_note
    return CommandResult(data, warnings, meta)
