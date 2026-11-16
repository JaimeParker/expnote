"""Storage layout for Research projects and their vault migration."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.registry import (
    load_registry,
    open_project,
    register_project,
    registry_path,
    save_registry,
)
from expnote.research.writeback import (
    WriteContext,
    _fsync_directory,
    _replace_with_retry,
    _write_temp,
    project_state_dir,
    write_lock,
)

DEFAULT_URL = "http://localhost:8765"
MATERIAL_FOLDERS = ("ops", "protocols", "templates", "evidence", "results")
WIKI_LINK = re.compile(r"(?<!\!)\[\[([^\[\]\n]+)\]\]")


def atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    temp = _write_temp(path, contents, mode)
    _replace_with_retry(temp, path)
    _fsync_directory(path.parent)


def init_split(project, vault, ident, name, workspaces, apply) -> CommandResult:
    from expnote.research.newnote import profile_text

    root = project_state_dir(ident)
    profile = root / "profile.md"
    images = root / "images"
    if ident in load_registry().projects or profile.exists():
        raise ResearchError(
            "PROJECT_EXISTS", f"project {ident!r} already exists", exit_code=2
        )
    data = {
        "project": ident,
        "layout": "split",
        "absProjectDir": str(project),
        "absResearchDir": str(root),
        "absImagesDir": str(images),
        "absProfileNote": str(profile),
        "dryRun": not apply,
        "applied": apply,
    }
    if apply:
        project.mkdir(parents=True, exist_ok=True)
        images.mkdir(parents=True, exist_ok=True)
        atomic_bytes(profile, profile_text(ident, name).encode())
        deviation = (
            "---\ntype: deviation-log\n---\n# Deviation register\n\n"
            "| ID | Date | Experiment | Deviation | Impact on validity | "
            "Disposition | Related notes | Status | Found at |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
        )
        atomic_bytes(root / "ops" / "deviations.md", deviation.encode())
        register_project(
            vault_root=vault,
            project_dir=project,
            images_dir=images,
            profile_note=profile,
            project_id=ident,
            name=name,
            workspaces=workspaces,
            layout="split",
        )
    return CommandResult(data, exit_code=0 if apply else 4)


def record_id(project: str, original: str) -> str:
    return "record-" + uuid5(NAMESPACE_URL, f"expnote/{project}/{original}").hex


def record_link(project: str, ident: str, base_url: str = DEFAULT_URL) -> str:
    project_path = quote(project, safe="")
    ident_path = quote(ident, safe="")
    return f"{base_url.rstrip('/')}/research/#/p/{project_path}/record/{ident_path}"


def migration(
    project: str,
    manifest: Path | None,
    apply: bool,
    expected: str | None = None,
) -> CommandResult:
    from expnote.research.edits import (
        _code_and_comment_mask,
        decode_note,
        edit_frontmatter,
        encode_note,
    )
    from expnote.research.model import parse_note
    from expnote.research.workflow import FIELDS, TYPES, import_workflow

    context = open_project(project)
    if context.entry.layout == "split":
        return CommandResult({"project": project, "layout": "split", "applied": False})
    resolved = context.resolved
    vault, source = resolved.vault_root, resolved.project_dir
    destination = project_state_dir(project)
    backup = destination / "migration" / "split-backup"
    if (backup / "journal.json").is_file():
        raise ResearchError(
            "MIGRATION_INTERRUPTED",
            "restore the interrupted migration before retrying",
            remediation=f"expnote project restore-layout {project} --apply",
            exit_code=5,
        )
    try:
        options = json.loads(manifest.read_text(encoding="utf-8")) if manifest else {}
    except (OSError, ValueError) as error:
        raise ResearchError("MIGRATION_MANIFEST", str(error), exit_code=2) from None
    if not isinstance(options, dict) or not isinstance(options.get("retain", {}), dict):
        raise ResearchError(
            "MIGRATION_MANIFEST", "retain must be a path mapping", exit_code=2
        )
    retain = options.get("retain", {})
    base_url = options.get("baseUrl", DEFAULT_URL)
    uri = urlsplit(base_url)
    if uri.scheme != "http" or uri.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ResearchError(
            "MIGRATION_URL", "use a local Research Web address", exit_code=2
        )

    files: dict[Path, tuple[Path, str]] = {}
    targets: dict[str, list[tuple[Path, str]]] = {}
    records = []
    folders = set(MATERIAL_FOLDERS)
    folders.update(
        value
        for kind, value in context.profile.raw.get("folders", {}).items()
        if kind != "plan" and isinstance(value, str)
    )
    template_dir = context.profile.raw.get("templates", {}).get("dir")
    if template_dir:
        folders.add(template_dir)
    for folder in sorted(folders):
        for old in sorted((source / folder).rglob("*")):
            if (
                not old.is_file()
                or old.is_symlink()
                or not old.resolve().is_relative_to(source)
            ):
                continue
            rel = old.relative_to(source).as_posix()
            keep = retain.get(rel)
            new = (source / keep) if keep else (destination / rel)
            if old == new or not new.resolve().is_relative_to(
                source if keep else destination
            ):
                raise ResearchError("MIGRATION_PATH", f"invalid destination: {new}")
            ident = ""
            if not keep and old.suffix.lower() == ".md":
                note = parse_note(old, rel, old.read_text(encoding="utf-8-sig"))
                ident = str(
                    note.get("id") or note.get("record_id") or record_id(project, rel)
                )
                records.append((rel, ident, note))
            files[old] = (new, ident)
            if ident:
                for alias in {
                    old.stem,
                    rel,
                    old.relative_to(vault).as_posix(),
                    rel.removesuffix(".md"),
                    old.relative_to(vault).as_posix().removesuffix(".md"),
                    *(note.get("aliases") or []),
                }:
                    targets.setdefault(str(alias).casefold(), []).append((old, ident))
    for old in sorted((source / "plans").rglob("*.md")):
        rel = old.relative_to(source).as_posix()
        note = parse_note(old, rel, old.read_text(encoding="utf-8-sig"))
        if note.get("id"):
            records.append((rel, str(note.get("id")), note))
    for rel in (
        context.profile.deviation_register,
        context.profile.decision_export,
        context.profile.todo_export,
    ):
        old = source / rel
        if old.is_file() and old.resolve().is_relative_to(source):
            files.setdefault(old, (destination / rel, ""))
    files[resolved.profile_note] = (destination / "profile.md", "")
    if resolved.images_dir.is_dir():
        for old in sorted(resolved.images_dir.rglob("*")):
            if old.is_file() and not old.is_symlink():
                files[old] = (
                    destination / "images" / old.relative_to(resolved.images_dir),
                    "",
                )

    rewritten = {}
    retained = [
        p
        for p in sorted(vault.rglob("*.md"))
        if not p.is_symlink()
        and p.resolve().is_relative_to(vault)
        and not any(part.startswith(".") for part in p.relative_to(vault).parts[:-1])
        and (p not in files or files[p][0].is_relative_to(vault))
    ]
    image_names = {}
    if resolved.images_dir.is_dir():
        for asset in resolved.images_dir.rglob("*"):
            if asset.is_file():
                image_names.setdefault(asset.name.casefold(), []).append(asset)
    kept_images = set()
    for note in retained:
        for embed in re.findall(
            r"!\[\[([^\]\n|]+)", note.read_text(encoding="utf-8-sig")
        ):
            kept_images.update(image_names.get(Path(embed.strip()).name.casefold(), []))
    conflicts = []
    seen = set()
    for old, (new, _) in files.items():
        key = str(new).casefold()
        if key in seen or (new.exists() and new.read_bytes() != old.read_bytes()):
            conflicts.append(f"destination collision: {new}")
        seen.add(key)
    for note_path in retained:
        text, fmt = decode_note(note_path.read_bytes())

        def rewrite(match: re.Match, note_path: Path = note_path) -> str:
            ref, _, label = match[1].partition("|")
            name, _, heading = ref.partition("#")
            choices = targets.get(name.removesuffix(".md").casefold(), [])
            if len(choices) != 1:
                if choices:
                    conflicts.append(f"ambiguous reference: {note_path}: {name}")
                return match[0]
            href = record_link(project, choices[0][1], base_url)
            if heading:
                href += "?anchor=" + quote(heading, safe="")
            caption = (label or ref).replace("[", r"\[").replace("]", r"\]")
            return f"[{caption}]({href})"

        lines = text.split("\n")
        mask = _code_and_comment_mask(lines)
        output = "\n".join(
            line if mask[index] else WIKI_LINK.sub(rewrite, line)
            for index, line in enumerate(lines)
        )
        if output != text:
            rewritten[note_path] = encode_note(output, fmt)

    for _, _, note in records:
        if note.path in files or note.type != "plan" or not note.has_fm:
            continue
        text, fmt = decode_note(rewritten.get(note.path, note.path.read_bytes()))
        rewritten[note.path] = encode_note(edit_frontmatter(text, remove=FIELDS), fmt)
    prepared = {}
    for old, (new, ident) in files.items():
        raw = rewritten.get(old, old.read_bytes())
        if ident:
            text, fmt = decode_note(raw)
            if not text.startswith("---\n"):
                text = "---\n---\n" + text
            new_note = parse_note(old, old.name, text)
            aliases = new_note.get("aliases") or []
            aliases = [aliases] if isinstance(aliases, str) else list(aliases)
            items = [("record_id", json.dumps(ident))]
            if old.stem not in aliases:
                items.append(
                    ("aliases", json.dumps([*aliases, old.stem], ensure_ascii=False))
                )
            remove = FIELDS if new_note.type in TYPES else frozenset()
            raw = encode_note(edit_frontmatter(text, items, remove=remove), fmt)
        prepared[new] = raw
    for old, output in rewritten.items():
        if old not in files:
            prepared[old] = output

    hashes = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files.keys() | rewritten.keys()
    }
    data = {
        "project": project,
        "layout": "split",
        "dryRun": not apply,
        "moves": [
            {"source": str(p), "target": str(t), "id": i, "sha256": hashes[p]}
            for p, (t, i) in files.items()
        ],
        "rewrite": [str(p) for p in rewritten],
        "states": [
            {"id": i, "status": n.get("status")}
            for _, i, n in records
            if n.get("status")
        ],
        "conflicts": sorted(set(conflicts)),
    }
    data["copyOnly"] = sorted(str(p) for p in kept_images)
    data["planHash"] = hashlib.sha256(
        json.dumps(
            data["moves"]
            + [{"path": str(p), "before": hashes[p]} for p in rewritten]
            + [
                {"path": str(p), "after": hashlib.sha256(raw).hexdigest()}
                for p, raw in sorted(prepared.items())
            ],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if not apply:
        return CommandResult(data, exit_code=4)
    if expected is not None and expected != data["planHash"]:
        raise ResearchError(
            "MIGRATION_CHANGED",
            "preview hash differs; review a fresh preview",
            exit_code=5,
        )
    if conflicts:
        raise ResearchError(
            "MIGRATION_CONFLICT", "fix migration conflicts", details=data, exit_code=5
        )

    ctx = WriteContext.for_project(context)
    with write_lock(ctx):
        if any(
            hashlib.sha256(p.read_bytes()).hexdigest() != sha
            for p, sha in hashes.items()
        ):
            raise ResearchError(
                "MIGRATION_CHANGED", "a source changed during planning", exit_code=5
            )
        atomic_bytes(backup / "registry.toml", registry_path().read_bytes())
        for p in hashes:
            atomic_bytes(backup / "vault" / p.relative_to(vault), p.read_bytes())
        for filename in ("decisions.sqlite", "todos.sqlite"):
            db = destination / filename
            if db.is_file():
                with (
                    sqlite3.connect(db) as original,
                    sqlite3.connect(backup / filename) as copy,
                ):
                    original.backup(copy)
        original_targets = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in prepared
            if p.is_file()
        }
        for name in original_targets:
            target = Path(name)
            if target not in hashes:
                atomic_bytes(
                    backup / "existing" / hashlib.sha256(name.encode()).hexdigest(),
                    target.read_bytes(),
                )
        journal = {
            "vault": str(vault),
            "sources": {str(p): sha for p, sha in hashes.items()},
            "targets": {
                str(p): hashlib.sha256(raw).hexdigest() for p, raw in prepared.items()
            },
            "existing": original_targets,
        }
        atomic_bytes(
            backup / "manifest.json",
            json.dumps(data, ensure_ascii=False, indent=2).encode(),
        )
        atomic_bytes(
            backup / "journal.json", json.dumps(journal, ensure_ascii=False).encode()
        )
        for path, raw in prepared.items():
            atomic_bytes(path, raw)
        if any(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() != sha
            for p, sha in journal["targets"].items()
        ):
            raise ResearchError(
                "MIGRATION_CHANGED", "target checksum mismatch", exit_code=5
            )
        import_workflow(context, records, files, backup / "vault")
        registry = load_registry()
        registry.projects[project] = dataclasses.replace(
            context.entry,
            layout="split",
            images_dir=str(destination / "images"),
            profile_note=str(destination / "profile.md"),
        )
        save_registry(registry)
        for old in files.keys() - kept_images:
            if hashlib.sha256(old.read_bytes()).hexdigest() != hashes[old]:
                raise ResearchError(
                    "MIGRATION_CHANGED",
                    f"source changed during cutover: {old}",
                    exit_code=5,
                )
            old.unlink()
        for folder in sorted(folders):
            for directory in sorted((source / folder).rglob("*"), reverse=True):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
            directory = source / folder
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        journal["stores"] = {
            filename: hashlib.sha256((destination / filename).read_bytes()).hexdigest()
            for filename in ("decisions.sqlite", "todos.sqlite")
            if (destination / filename).is_file()
        }
        atomic_bytes(
            backup / "journal.json", json.dumps(journal, ensure_ascii=False).encode()
        )
    data.update(applied=True, backup=str(backup))
    return CommandResult(data)


def restore_layout(project: str, apply: bool) -> CommandResult:
    root = project_state_dir(project)
    backup = root / "migration" / "split-backup"
    journal_path = backup / "journal.json"
    if not journal_path.is_file():
        raise ResearchError("MIGRATION_NO_BACKUP", "no migration journal", exit_code=3)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    registry = load_registry()
    original = load_registry(backup / "registry.toml").projects[project]
    context = WriteContext(project, original.vault_id, Path(journal["vault"]))
    summary = {"project": project, "backup": str(backup), "dryRun": not apply}
    with write_lock(context) if apply else nullcontext():
        for target, sha in journal["targets"].items():
            path = Path(target)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() not in {
                sha,
                journal["existing"].get(target),
            }:
                raise ResearchError(
                    "MIGRATION_CHANGED", f"target changed: {target}", exit_code=5
                )
        for source, sha in journal["sources"].items():
            path = Path(source)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() not in {
                sha,
                journal["targets"].get(source),
            }:
                raise ResearchError(
                    "MIGRATION_CHANGED", f"source changed: {source}", exit_code=5
                )
        for name, sha in journal.get("stores", {}).items():
            if (root / name).is_file() and hashlib.sha256(
                (root / name).read_bytes()
            ).hexdigest() != sha:
                raise ResearchError(
                    "MIGRATION_CHANGED", f"database changed: {name}", exit_code=5
                )
        if not apply:
            return CommandResult(summary, exit_code=4)
        for name, sha in journal["sources"].items():
            path = Path(name)
            raw = (backup / "vault" / path.relative_to(context.vault_root)).read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ResearchError("MIGRATION_BACKUP_CORRUPT", name, exit_code=6)
            atomic_bytes(path, raw)
        for name in journal["targets"].keys() - journal["sources"].keys():
            path = Path(name)
            if name in journal["existing"]:
                source = backup / "existing" / hashlib.sha256(name.encode()).hexdigest()
                atomic_bytes(path, source.read_bytes())
            else:
                path.unlink(missing_ok=True)
        for name in ("decisions.sqlite", "todos.sqlite"):
            db = backup / name
            if db.is_file():
                with (
                    sqlite3.connect(db) as reader,
                    sqlite3.connect(root / name) as writer,
                ):
                    reader.backup(writer)
            else:
                (root / name).unlink(missing_ok=True)
        registry.projects[project] = original
        save_registry(registry)
        journal_path.unlink()
    summary["applied"] = True
    return CommandResult(summary)
