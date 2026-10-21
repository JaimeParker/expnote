"""Project registry (~/.config/expnote/projects.toml) and path revalidation."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import tomllib
import uuid
from pathlib import Path

from expnote.research.errors import ResearchError
from expnote.research.profile import Profile, ProfileNote, read_profile_note
from expnote.workspace import config_home

REGISTRY_FILE = "projects.toml"


def registry_path() -> Path:
    return config_home() / REGISTRY_FILE


@dataclasses.dataclass
class ProjectEntry:
    id: str
    name: str
    vault_id: str
    vault_root: str
    project_dir: str
    images_dir: str
    profile_note: str
    vault_root_alternates: list[str] = dataclasses.field(default_factory=list)
    workspaces: list[str] = dataclasses.field(default_factory=list)
    quiet_window_seconds: int = 10

    PATH_KEYS = ("vault_root", "project_dir", "images_dir", "profile_note")


@dataclasses.dataclass
class Registry:
    active_project: str | None = None
    projects: dict[str, ProjectEntry] = dataclasses.field(default_factory=dict)


def load_registry(path: Path | None = None) -> Registry:
    path = path or registry_path()
    if not path.exists():
        return Registry()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as error:
        raise ResearchError(
            "REGISTRY_INVALID", f"cannot read {path}: {error}", exit_code=6
        ) from None
    registry = Registry(active_project=data.get("active_project"))
    for project_id, raw in (data.get("projects") or {}).items():
        try:
            registry.projects[project_id] = ProjectEntry(
                id=project_id,
                name=raw.get("name", project_id),
                vault_id=raw["vault_id"],
                vault_root=raw["vault_root"],
                project_dir=raw["project_dir"],
                images_dir=raw["images_dir"],
                profile_note=raw["profile_note"],
                vault_root_alternates=list(raw.get("vault_root_alternates", [])),
                workspaces=list(raw.get("workspaces", [])),
                quiet_window_seconds=int(raw.get("quiet_window_seconds", 10)),
            )
        except KeyError as error:
            raise ResearchError(
                "REGISTRY_INVALID",
                f"project {project_id!r} in {path} lacks key {error}",
                exit_code=6,
            ) from None
    return registry


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def dump_registry(registry: Registry) -> str:
    """Deterministic emitter: fixed key order, projects sorted by id."""
    lines = []
    if registry.active_project:
        lines.append(f"active_project = {_toml_string(registry.active_project)}")
        lines.append("")
    for project_id in sorted(registry.projects):
        entry = registry.projects[project_id]
        lines.append(f"[projects.{_toml_string(project_id)}]")
        lines.append(f"name = {_toml_string(entry.name)}")
        lines.append(f"vault_id = {_toml_string(entry.vault_id)}")
        lines.append(f"vault_root = {_toml_string(entry.vault_root)}")
        lines.append(
            f"vault_root_alternates = {_toml_list(entry.vault_root_alternates)}"
        )
        lines.append(f"project_dir = {_toml_string(entry.project_dir)}")
        lines.append(f"images_dir = {_toml_string(entry.images_dir)}")
        lines.append(f"profile_note = {_toml_string(entry.profile_note)}")
        lines.append(f"workspaces = {_toml_list(entry.workspaces)}")
        lines.append(f"quiet_window_seconds = {int(entry.quiet_window_seconds)}")
        lines.append("")
    return "\n".join(lines)


def save_registry(registry: Registry, path: Path | None = None) -> Path:
    path = path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(dump_registry(registry), encoding="utf-8")
    os.replace(temp, path)
    return path


# ---- paths ------------------------------------------------------------------


def _slashes(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def convert_drive_path(path: str) -> str | None:
    """`D:/x` <-> `/mnt/d/x`; None when the path has neither form."""
    windows = re.fullmatch(r"([A-Za-z]):/(.*)", _slashes(path) + "/")
    if windows:
        rest = windows.group(2).rstrip("/")
        return f"/mnt/{windows.group(1).lower()}" + (f"/{rest}" if rest else "")
    mounted = re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", _slashes(path))
    if mounted:
        rest = mounted.group(2) or ""
        return f"{mounted.group(1).upper()}:/{rest}"
    return None


def rebase(path: str, old_root: str, new_root: str) -> str:
    """Replace the `old_root` prefix of `path` with `new_root`."""
    path_s, old_s = _slashes(path), _slashes(old_root)
    if path_s == old_s:
        return _slashes(new_root)
    if path_s.startswith(old_s + "/"):
        return _slashes(new_root) + path_s[len(old_s) :]
    raise ResearchError(
        "USAGE_PATH_OUTSIDE_VAULT",
        f"{path} is not inside {old_root}",
        exit_code=2,
    )


def is_inside(path: str, root: str) -> bool:
    path_s, root_s = _slashes(os.path.abspath(path)), _slashes(os.path.abspath(root))
    return path_s == root_s or path_s.startswith(root_s + "/")


@dataclasses.dataclass
class ResolvedProject:
    entry: ProjectEntry
    vault_root: Path
    project_dir: Path
    images_dir: Path
    profile_note: Path
    path_note: str | None = None


def _candidate_roots(entry: ProjectEntry) -> list[str]:
    roots: list[str] = []
    for root in [entry.vault_root, *entry.vault_root_alternates]:
        for candidate in (root, convert_drive_path(root)):
            if candidate and candidate not in roots:
                roots.append(candidate)
    return roots


def revalidate(entry: ProjectEntry) -> ResolvedProject:
    """Find a vault root that exists and holds the project; first hit wins."""
    tried = []
    for index, root in enumerate(_candidate_roots(entry)):
        project_dir = Path(rebase(entry.project_dir, entry.vault_root, root))
        profile_note = Path(rebase(entry.profile_note, entry.vault_root, root))
        tried.append(root)
        if (
            (Path(root) / ".obsidian").is_dir()
            and project_dir.is_dir()
            and profile_note.is_file()
        ):
            note = (
                None
                if index == 0
                else f"using vault root {root} instead of {entry.vault_root}"
            )
            return ResolvedProject(
                entry,
                Path(root),
                project_dir,
                Path(rebase(entry.images_dir, entry.vault_root, root)),
                profile_note,
                note,
            )
    raise ResearchError(
        "VAULT_UNAVAILABLE",
        f"vault for project {entry.id!r} is not reachable (tried {', '.join(tried)})",
        exit_code=6,
        remediation=f"expnote project rebind {entry.id} --vault-root <path>",
        details={"tried": tried},
    )


@dataclasses.dataclass
class ProjectContext:
    resolved: ResolvedProject
    profile: Profile
    profile_sha256: str

    @property
    def entry(self) -> ProjectEntry:
        return self.resolved.entry


def select_entry(registry: Registry, project_id: str | None) -> ProjectEntry:
    chosen = project_id or registry.active_project
    if not chosen:
        raise ResearchError(
            "PROJECT_NOT_REGISTERED",
            "no project given and no active project is set",
            exit_code=3,
            remediation="expnote project register ... or expnote project use ID",
        )
    entry = registry.projects.get(chosen)
    if entry is None:
        raise ResearchError(
            "PROJECT_NOT_FOUND",
            f"project {chosen!r} is not registered",
            exit_code=3,
            remediation="expnote project list",
            details={"registered": sorted(registry.projects)},
        )
    return entry


def profile_or_raise(note: ProfileNote, expected_id: str | None) -> Profile:
    if not note.found:
        raise ResearchError(
            "PROFILE_NOT_FOUND",
            note.problems[0],
            exit_code=3,
            details={"problems": note.problems},
        )
    if note.profile is None:
        raise ResearchError(
            "PROFILE_INVALID",
            "profile note is invalid: " + "; ".join(note.problems),
            exit_code=6,
            details={"problems": note.problems},
        )
    if expected_id is not None and note.profile.project_id != expected_id:
        raise ResearchError(
            "PROFILE_INVALID",
            f"profile project_id {note.profile.project_id!r} does not match "
            f"registry id {expected_id!r}",
            exit_code=6,
        )
    return note.profile


def open_project(project_id: str | None = None) -> ProjectContext:
    registry = load_registry()
    entry = select_entry(registry, project_id)
    resolved = revalidate(entry)
    note = read_profile_note(resolved.profile_note)
    profile = profile_or_raise(note, entry.id)
    return ProjectContext(resolved, profile, note.sha256 or "")


def new_vault_id(registry: Registry, entry_id: str, vault_root: str) -> str:
    existing = registry.projects.get(entry_id)
    if existing and _slashes(existing.vault_root) == _slashes(vault_root):
        return existing.vault_id
    for other in registry.projects.values():
        if _slashes(other.vault_root) == _slashes(vault_root):
            return other.vault_id
    return str(uuid.uuid4())
