"""M0: the external backup, its restore test and the pre-migration golden.

Also the facts about what lies outside the project folder: the Markdown projection of
the bound workspaces and the notes outside the project that link into it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from expnote.db import read_config
from expnote.research.errors import ResearchError
from expnote.research.registry import ProjectContext, is_inside
from expnote.research.textkit import fold, iter_links, link_stem
from expnote.workspace import resolve_workspace

ARCHIVE_PREFIXES = ("50 Archive/",)
PROJECTION_KEYS = ("obsidian_root", "root", "notes_dir", "docs_dir", "moc_path")
SKIP_WALK_DIRS = {".git", ".obsidian", ".trash"}
BACKUP_TAR = "backup.tar.gz"


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- projection ---------------------------------------------------------------------


@dataclasses.dataclass
class Projection:
    workspace: str
    config_path: Path
    enabled: bool  # the workspace config still points into this vault
    root: Path | None  # the projection folder inside the vault
    original: dict[str, str]

    def as_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "configPath": str(self.config_path),
            "enabled": self.enabled,
            "root": str(self.root) if self.root else None,
            "original": self.original,
        }


def _projection_root(vault_root: Path, config: dict[str, str]) -> Path | None:
    base = Path(config.get("obsidian_root") or config.get("root") or "")
    dirs = [base / config[k] for k in ("notes_dir", "docs_dir") if config.get(k)]
    if not dirs:
        return None
    common = Path(os.path.commonpath([str(d) for d in dirs]))
    inside = is_inside(str(common), str(vault_root))
    return common if inside and common != vault_root else None


def projections(context: ProjectContext, saved: list[dict]) -> list[Projection]:
    """Projection folders of the bound workspaces (live config, else the saved one)."""
    vault_root = context.resolved.vault_root
    found: list[Projection] = []
    for name in context.entry.workspaces:
        try:
            workspace = resolve_workspace(workspace=name)
            config = read_config(
                workspace.workspace_dir, state_dir=workspace.workspace_dir
            )
            config_path = workspace.workspace_dir / "config.toml"
        except Exception:  # noqa: BLE001 - an unreadable workspace is reported elsewhere
            continue
        root_text = config.get("obsidian_root") or config.get("root")
        if root_text and is_inside(root_text, str(vault_root)):
            root = _projection_root(vault_root, config)
            original = {k: config[k] for k in PROJECTION_KEYS if k in config}
            found.append(Projection(name, config_path, True, root, original))
            continue
        record = next((s for s in saved if s.get("workspace") == name), None)
        root = Path(record["root"]) if record and record.get("root") else None
        found.append(
            Projection(
                name,
                config_path,
                False,
                root,
                dict(record["original"]) if record else {},
            )
        )
    return found


def projected_stems(vault_root: Path, roots: list[Path]) -> dict[str, str]:
    """Folded stem -> vault-relative path of every Markdown file in the projection."""
    stems: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(vault_root).as_posix()
            stems.setdefault(fold(path.stem), rel)
    return stems


def shared_stems(
    vault_root: Path, roots: list[Path], stems: dict[str, str]
) -> set[str]:
    """Projected stems that a note outside the projection also uses (kept as links)."""
    taken: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_WALK_DIRS]
        here = Path(dirpath)
        if any(r == here or r in here.parents for r in roots):
            dirnames[:] = []
            continue
        taken |= {fold(Path(f).stem) for f in filenames if f.endswith(".md")}
    return taken & set(stems)


def outside_notes(
    vault_root: Path,
    project_dir: Path,
    roots: list[Path],
    stems: set[str],
) -> list[dict]:
    """Notes outside the project and the projection that link to projected stems."""
    found = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_WALK_DIRS]
        here = Path(dirpath)
        if any(r == here or r in here.parents for r in [project_dir, *roots]):
            dirnames[:] = []
            continue
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = here / filename
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            hits = [
                link_stem(t)
                for embed, t in iter_links(text)
                if not embed and fold(link_stem(t)) in stems
            ]
            if not hits:
                continue
            rel = path.relative_to(vault_root).as_posix()
            found.append(
                {
                    "path": rel,
                    "links": len(hits),
                    "distinctTargets": len(set(hits)),
                    "archived": rel.startswith(ARCHIVE_PREFIXES),
                }
            )
    return sorted(found, key=lambda r: r["path"])


# ---- backup --------------------------------------------------------------------------


def backup_members(
    vault_root: Path,
    project_dir: Path,
    roots: list[Path],
    outside: list[str],
) -> tuple[dict[str, Path], list[str]]:
    """(arcname -> file path, empty directories) of what the migration may touch."""
    files: dict[str, Path] = {}
    empty_dirs: list[str] = []

    def add_tree(base: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            if not dirnames and not filenames:
                empty_dirs.append(here.relative_to(vault_root).as_posix())
            for filename in filenames:
                path = here / filename
                if path.is_symlink() or not path.is_file():
                    continue
                files[path.relative_to(vault_root).as_posix()] = path

    add_tree(project_dir)
    for root in roots:
        if root.is_dir():
            add_tree(root)
    if (vault_root / ".obsidian").is_dir():
        add_tree(vault_root / ".obsidian")
    templates = vault_root / "templates"
    if templates.is_dir() and not is_inside(str(templates), str(project_dir)):
        add_tree(templates)
    for rel in outside:
        path = vault_root / rel
        if path.is_file():
            files[rel] = path
    return files, sorted(empty_dirs)


def vault_git_head(vault_root: Path) -> str | None:
    if not (vault_root / ".git").exists():
        return None
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", str(vault_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def refuse_inside_vault(out_dir: Path, vault_root: Path) -> None:
    if is_inside(os.path.realpath(out_dir), os.path.realpath(vault_root)):
        raise ResearchError(
            "BACKUP_INSIDE_VAULT",
            f"{out_dir} lies inside the vault {vault_root}; a backup must live "
            "outside it",
            exit_code=2,
            remediation="pass --out DIR with a folder outside the vault",
        )


def write_backup(
    out_dir: Path,
    members: dict[str, Path],
    empty_dirs: list[str],
    meta: dict,
) -> dict:
    """Write tar.gz + manifest, then restore into a temp dir and compare hashes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    tar_path = out_dir / BACKUP_TAR
    with tarfile.open(tar_path, "w:gz") as archive:
        for rel in empty_dirs:
            info = tarfile.TarInfo(rel)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for rel, path in sorted(members.items()):
            archive.add(path, arcname=rel, recursive=False)
            manifest.append(
                {
                    "path": rel,
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    mismatches = restore_test(tar_path, manifest, empty_dirs)
    record = {
        **meta,
        "archive": str(tar_path),
        "archiveSha256": sha256_file(tar_path),
        "archiveBytes": tar_path.stat().st_size,
        "files": len(manifest),
        "emptyDirs": empty_dirs,
        "restoreTested": True,
        "restoreMismatches": mismatches,
        "restoreOk": not mismatches,
    }
    (out_dir / "backup.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    return record


def restore_test(tar_path: Path, manifest: list[dict], empty_dirs: list[str]) -> list:
    problems = []
    with tempfile.TemporaryDirectory(prefix="restore-", dir=tar_path.parent) as tmp:
        root = Path(tmp)
        with tarfile.open(tar_path, "r:gz") as archive:
            archive.extractall(root, filter="data")
        for entry in manifest:
            target = root / entry["path"]
            if not target.is_file():
                problems.append(f"missing after restore: {entry['path']}")
            elif sha256_file(target) != entry["sha256"]:
                problems.append(f"hash differs after restore: {entry['path']}")
        problems += [
            f"empty directory not restored: {rel}"
            for rel in empty_dirs
            if not (root / rel).is_dir()
        ]
    return problems


def read_manifest(backup_dir: Path) -> list[dict]:
    return json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))


def read_member_text(tar_path: Path, names: set[str]) -> dict[str, bytes]:
    """The bytes of the named members (those that exist)."""
    out: dict[str, bytes] = {}
    with tarfile.open(tar_path, "r:gz") as archive:
        for member in archive:
            if member.name in names and member.isfile():
                handle = archive.extractfile(member)
                if handle is not None:
                    out[member.name] = handle.read()
    return out
