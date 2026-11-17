"""Preview, apply and recover a split project's EV/RES placement changes."""

from __future__ import annotations

import dataclasses
import json
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

from expnote.research.edits import decode_note, edit_frontmatter, encode_note
from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.index import (
    ConflictMatcher,
    refresh_index,
    walk_project,
    walk_vault,
)
from expnote.research.layout import DEFAULT_URL, atomic_bytes, record_link
from expnote.research.model import parse_note
from expnote.research.registry import load_registry, open_project, save_registry
from expnote.research.storage_media import (
    ImageCopies,
    is_image,
    local_target,
    rewrite_links,
    sha_file,
    wiki_parts,
)
from expnote.research.storage_paths import (
    KINDS,
    LOCATIONS,
    document_dir,
    file_key,
    file_path,
    location_data,
)
from expnote.research.textkit import fold
from expnote.research.workflow import digest, identity
from expnote.research.writeback import (
    WriteContext,
    _append_audit,
    _create_no_clobber,
    write_lock,
)


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def _positions(entry) -> dict:
    return {kind: entry.storage(kind) for kind in KINDS}


def _set_positions(context, positions, *, allowed) -> None:
    registry = load_registry()
    entry = registry.projects[context.entry.id]
    if (
        _positions(entry) not in allowed
        or dataclasses.replace(
            entry,
            evidence_in_vault=context.entry.evidence_in_vault,
            results_in_vault=context.entry.results_in_vault,
        )
        != context.entry
    ):
        raise ResearchError(
            "STORAGE_CHANGED", "The project registration changed.", exit_code=5
        )
    registry.projects[entry.id] = dataclasses.replace(
        entry,
        evidence_in_vault=positions["ev"] == "vault",
        results_in_vault=positions["res"] == "vault",
    )
    save_registry(registry)


def _journal_root(resolved) -> Path:
    return resolved.material_dir / "migration" / "storage"


def _journals(resolved):
    if not resolved.research_dir:
        return
    for path in sorted(_journal_root(resolved).glob("*/journal.json")):
        yield path, json.loads(path.read_bytes())


def _pending(resolved):
    return next(
        ((path, log) for path, log in _journals(resolved) if log["state"] == "pending"),
        None,
    )


def moved_files(resolved, owners=()) -> list[dict]:
    """Only committed, byte-verified storage changes can preserve a prior digest."""
    return [
        {
            (item["source"], item["before"]): (item["target"], item["after"])
            for item in journal["equivalent"]
            if not item.get("owner") or item["owner"] in owners
        }
        for _, journal in _journals(resolved)
        if journal["state"] == "complete"
    ]


def mapped_file(resolved, name, sha, equivalents):
    for migration in equivalents:
        name, sha = migration.get((name, sha), (name, sha))
    return file_path(resolved, name), sha


def equivalent_digest(resolved, path, current, original) -> bool:
    if current == original:
        return True
    equivalents = moved_files(resolved)
    return any(
        mapped_file(resolved, name, before, equivalents[index:]) == (path, current)
        for index, migration in enumerate(equivalents)
        for name, before in migration
        if before == original
    )


class _Inventory:
    def __init__(self, context, targets):
        self.context = context
        self.resolved = context.resolved
        self.targets = targets
        entries, _ = walk_project(context)
        conflict = ConflictMatcher(context.profile)
        self.files = {}
        self.names = defaultdict(list)
        self.notes = {}
        self.ids = {}
        self.moves = {}
        self.conflicts = []
        for entry in sorted(entries, key=lambda item: item.rel):
            path = self.resolved.physical_path(entry.rel)
            allowed_root = (
                self.resolved.material_dir
                if path.is_relative_to(self.resolved.material_dir)
                else self.resolved.vault_root
            )
            if path.is_symlink() or not path.resolve().is_relative_to(
                allowed_root.resolve()
            ):
                continue
            if conflict.matches(entry.rel):
                continue
            self.files[path] = entry
            self.names[fold(path.name)].append(path)
        self.images = ImageCopies(self.resolved, self.files)

    def raw(self, path):
        entry = self.files.get(path)
        if entry and entry.kind == "md":
            return path.read_bytes()
        return None

    def note(self, path, raw=None):
        if path not in self.notes:
            text, _ = decode_note(raw if raw is not None else path.read_bytes())
            resolved = self.resolved
            rel = Path(resolved.logical_path(path)).relative_to(
                resolved.project_dir.relative_to(resolved.vault_root)
            )
            self.notes[path] = parse_note(path, rel.as_posix(), text)
        return self.notes[path]

    def collect(self):
        resolved = self.resolved
        profile = self.context.profile
        for kind in ("ev", "appendix", "res"):
            group = "ev" if kind == "appendix" else kind
            if group not in self.targets:
                continue
            source = document_dir(resolved, profile, kind)
            target = document_dir(resolved, profile, kind, self.targets[group])
            counterpart = (
                document_dir(resolved, profile, "appendix") if kind == "ev" else None
            )
            for path, entry in self.files.items():
                if entry.kind != "md" or not path.is_relative_to(source):
                    continue
                if (
                    counterpart
                    and counterpart.is_relative_to(source)
                    and path.is_relative_to(counterpart)
                ):
                    continue
                note = self.note(path)
                if (
                    kind == "appendix"
                    and resolved.entry.storage(group) == "research"
                    and profile.legacy_type_map.get(note.type, note.type)
                    != "evidence-appendix"
                ):
                    continue
                dest = target / path.relative_to(source)
                ident = identity(self.context.entry.id, note)
                if ident in self.ids:
                    self.conflicts.append(f"Duplicate record ID {ident}: {path}")
                self.ids[ident] = path
                self.moves[path] = (dest, ident, self.targets[group])

    def resolve(self, source, name, *, markdown=False):
        if not name or name.startswith(("/", "\\")):
            return None
        names = [PurePosixPath(name).name]
        if not names[0].lower().endswith(".md") and not markdown:
            names.append(names[0] + ".md")
        matches = [
            path for basename in names for path in self.names.get(fold(basename), [])
        ]
        resolved = self.resolved
        exact = [
            path
            for path in matches
            if path == (source.parent / name).resolve()
            or fold(resolved.logical_path(path).removesuffix(".md"))
            == fold(name.removesuffix(".md"))
        ]
        if exact:
            matches = exact
        elif "/" in name:
            suffix = "/" + fold(name.removeprefix("./").removesuffix(".md"))
            matches = [
                path
                for path in matches
                if fold(resolved.logical_path(path).removesuffix(".md")).endswith(
                    suffix
                )
            ]
        if len(matches) > 1 and any(path in self.moves for path in matches):
            self.conflicts.append(f"{source}: ambiguous reference {name}")
        return matches[0] if len(matches) == 1 else None

    def rewrite(self, source, text):
        source_after = self.moves[source][0] if source in self.moves else source
        resolved = self.resolved
        vault = source_after.is_relative_to(resolved.vault_root)
        project = self.context.entry.id

        def vault_link(path, caption, anchor, pipe):
            target = resolved.logical_path(path).removesuffix(".md")
            section = f"#{anchor}" if anchor else ""
            return f"[[{target}{section}{pipe or '|'}{caption}]]"

        def research_link(ident, caption, anchor):
            url = record_link(project, ident, DEFAULT_URL)
            if anchor:
                url += "?anchor=" + quote(unquote(anchor), safe="")
            label = caption.replace("[", r"\[").replace("]", r"\]")
            return f"[{label}]({url})"

        def convert(match):
            if match["md_embed"] or (
                match["embed"]
                and is_image(wiki_parts(match["target"])[0].split("#", 1)[0])
            ):
                return match[0]
            if match["md_target"] is not None:
                raw = match["md_target"].strip("<>")
                label = match["md_label"]
                try:
                    uri = urlsplit(raw)
                except ValueError:
                    return match[0]
                route, _, query = uri.fragment.partition("?")
                parts = route.strip("/").split("/")
                if (
                    vault
                    and uri.hostname in {None, "localhost", "127.0.0.1", "::1"}
                    and uri.path.rstrip("/") == "/research"
                    and len(parts) == 4
                    and parts[0] == "p"
                    and unquote(parts[1]) == project
                    and parts[2] == "record"
                ):
                    from_path = self.ids.get(unquote(parts[3]))
                    if from_path and self.moves[from_path][2] == "vault":
                        return vault_link(
                            self.moves[from_path][0],
                            label,
                            parse_qs(query).get("anchor", [""])[0],
                            "",
                        )
                name = local_target(raw)
                if name is None:
                    return match[0]
                target = self.resolve(source, name, markdown=True)
                anchor = unquote(uri.fragment)
                pipe = ""
            else:
                raw, pipe, label = wiki_parts(match["target"])
                name, _, anchor = raw.partition("#")
                label = label or name
                target = self.resolve(source, name)
            if target is None or (
                source not in self.moves and target not in self.moves
            ):
                return match[0]
            destination, ident, _ = self.moves.get(target, (target, None, None))
            if destination.is_relative_to(resolved.vault_root):
                if vault:
                    return (
                        vault_link(destination, label, anchor, pipe)
                        if destination.suffix.lower() == ".md"
                        else match[0]
                    )
            elif destination.suffix.lower() == ".md":
                if vault:
                    return research_link(
                        ident or identity(project, self.note(target)), label, anchor
                    )
            elif vault and destination.is_relative_to(resolved.material_dir):
                attachment = quote(resolved.logical_path(destination), safe="")
                base = f"{DEFAULT_URL}/api/research/projects/{quote(project, safe='')}"
                url = f"{base}/attachment/{attachment}"
                return f"[{label}]({url})"
            if source_after != source or destination != target:
                logical = resolved.logical_path(destination).removesuffix(".md")
                return f"[[{logical}{'#' + anchor if anchor else ''}|{label}]]"
            return match[0]

        return rewrite_links(text, convert)


def record_image_copies(resolved, copies):
    if copies:
        stamp = f"{time.time_ns()}-{uuid4().hex[:8]}"
        log = {
            "state": "complete",
            "createdImages": copies,
            "archivedImages": [],
            "equivalent": [],
        }
        atomic_bytes(_journal_root(resolved) / stamp / "journal.json", _json(log))


def _tracked_assets(resolved):
    tracked = {}
    for _, journal in _journals(resolved):
        if journal["state"] == "complete":
            for key in journal["archivedImages"]:
                tracked.pop(key, None)
            tracked.update(journal["createdImages"])
    return tracked


def _plan(context, targets, *, include_assets: bool):
    inv = _Inventory(context, targets)
    inv.collect()
    resolved = inv.resolved
    inv.tracked = _tracked_assets(resolved) if include_assets else {}
    inv.tracked_names = {fold(PurePosixPath(key).name) for key in inv.tracked}
    changes = {}
    equivalent = []
    moves = []
    kept_image_names = set()
    rewritten = []
    reads = {}

    def stage(path, before, after):
        if path in changes:
            inv.conflicts.append(f"Multiple changes would write {path}")
        changes[path] = (before, after)

    for path, entry in inv.files.items():
        if entry.kind != "md":
            continue
        moving = inv.moves.get(path)
        vault_note = path.is_relative_to(resolved.vault_root)
        # Research materials can link to a moved document too. Keep the same
        # structured relationship fields, and write new destinations in prose.
        before = path.read_bytes()
        text, fmt = decode_note(before)
        revised = inv.rewrite(path, text)
        dest = moving[0] if moving else path
        if moving:
            try:
                revised = inv.images.rewrite(revised, path, moving[2], document=dest)
            except ResearchError as error:
                inv.conflicts.append(error.message)
            note = inv.note(path, before)
            if not (note.get("record_id") or note.get("id")):
                if not note.has_fm:
                    revised = "---\n---\n" + revised
                revised = edit_frontmatter(
                    revised, [("record_id", json.dumps(moving[1]))]
                )
        if include_assets and dest.is_relative_to(resolved.vault_root):
            # Include properties and conservative basename matches so that a
            # weak/ambiguous link can keep a copy, but can never delete one.
            folded = fold(unquote(revised))
            kept_image_names.update(
                name for name in inv.tracked_names if name in folded
            )
        if include_assets and vault_note:
            reads[path] = digest(before)
        after = encode_note(revised, fmt) if revised != text else before
        if moving:
            casefolded = fold(dest.as_posix())
            occupied = any(
                fold(other.as_posix()) == casefolded
                for other in inv.names.get(fold(dest.name), [])
            )
            if (
                occupied
                or dest.exists()
                or dest.is_symlink()
                or not dest.resolve().is_relative_to(
                    (
                        resolved.project_dir
                        if moving[2] == "vault"
                        else resolved.material_dir
                    ).resolve()
                )
            ):
                inv.conflicts.append(
                    f"Destination already exists or is outside the project: {dest}"
                )
            stage(dest, None, after)
            stage(path, digest(before), None)
            moves.append({"source": str(path), "target": str(dest), "id": moving[1]})
        elif after != before:
            stage(path, digest(before), after)
            rewritten.append(str(path))
        if moving or after != before:
            equivalent.append(
                {
                    "source": file_key(resolved, path),
                    "target": file_key(resolved, dest),
                    "before": digest(before),
                    "after": digest(after),
                }
            )

    for doc, assets in inv.images.references.items():
        for source, target in assets.items():
            if source != target:
                sha = inv.images.reads[source]
                equivalent.append(
                    {
                        "source": file_key(resolved, source),
                        "target": file_key(resolved, target),
                        "before": sha,
                        "after": sha,
                        "owner": inv.moves[doc][1],
                    }
                )
    created = {}
    for target, (source, sha) in inv.images.copies.items():
        raw = source.read_bytes()
        if digest(raw) != sha:
            inv.conflicts.append(f"Image changed while planning: {source}")
        stage(target, None, raw)
        if target.is_relative_to(resolved.vault_root):
            created[file_key(resolved, target)] = sha

    archived = []
    if include_assets:
        for key, sha in inv.tracked.items():
            path = file_path(resolved, key)
            if (
                fold(path.name) not in kept_image_names
                and not path.is_symlink()
                and sha_file(path) == sha
            ):
                stage(path, sha, None)
                archived.append(key)
    reads.update(
        {p: sha for p, sha in inv.images.reads.items() if p not in inv.images.copies}
    )
    manifest = {
        "positions": {**_positions(context.entry), **targets},
        "moves": moves,
        "rewrite": sorted(rewritten),
        "copyImages": [
            {"source": str(source), "target": str(target), "sha256": sha}
            for target, (source, sha) in sorted(inv.images.copies.items())
        ],
        "archiveImages": sorted(str(file_path(resolved, key)) for key in archived),
        "missingImages": sorted(inv.images.missing),
        "conflicts": sorted(set(inv.conflicts)),
    }
    tracked = {
        "createdImages": created,
        "archivedImages": archived,
        "equivalent": equivalent,
    }
    changes = dict(sorted(changes.items()))
    manifest["planHash"] = digest(
        _json(
            {
                "manifest": manifest,
                "entry": dataclasses.asdict(context.entry),
                "changes": [
                    (
                        file_key(resolved, p),
                        old,
                        digest(new) if new is not None else None,
                    )
                    for p, (old, new) in changes.items()
                ],
                "inputs": sorted(
                    (file_key(resolved, p), sha) for p, sha in reads.items()
                ),
            }
        )
    )
    return manifest, changes, reads, tracked


def _check(path: Path, allowed) -> None:
    if path.is_symlink() or sha_file(path) not in allowed:
        raise ResearchError(
            "STORAGE_CHANGED", f"{path} changed. Preview again.", exit_code=5
        )


def _record_file(log_path, index) -> Path:
    return log_path.parent / "files" / str(index)


def _restore(context, log_path, journal):
    resolved = context.resolved
    paths = []
    for i, row in enumerate(journal["files"]):
        path = file_path(resolved, row["path"])
        _check(path, {row["before"], row["after"]})
        backup = _record_file(log_path, i)
        if row["before"] is not None and sha_file(backup) != row["before"]:
            raise ResearchError(
                "STORAGE_BACKUP", f"Damaged backup: {backup}", exit_code=6
            )
        paths.append((path, row["before"], backup))
    for path, before, backup in paths:
        if sha_file(path) == before:
            continue
        if before is None:
            path.unlink(missing_ok=True)
        elif path.exists():
            atomic_bytes(path, backup.read_bytes())
        else:
            _create_no_clobber(path, backup.read_bytes())
    _set_positions(
        context, journal["before"], allowed=[journal["before"], journal["after"]]
    )
    refresh_index(open_project(context.entry.id))
    journal["state"] = "restored"
    atomic_bytes(log_path, _json(journal))


def restore_storage(project, apply, *, actor="cli") -> CommandResult:
    context = open_project(project)
    ctx = WriteContext.for_project(context, actor=actor)
    with write_lock(ctx):
        found = _pending(context.resolved)
        if not found:
            raise ResearchError(
                "STORAGE_NO_RECOVERY", "No interrupted migration.", exit_code=3
            )
        path, log = found
        data = {
            "backup": str(path.parent),
            "positions": log["before"],
            "applied": apply,
        }
        if apply:
            try:
                _restore(context, path, log)
            except OSError as error:
                raise ResearchError("STORAGE_IO", str(error), exit_code=6) from None
            _append_audit(ctx, {"command": "project restore-storage", **data})
        return CommandResult(data, exit_code=0 if apply else 4)


def require_storage_ready(context) -> None:
    if found := _pending(context.resolved):
        raise ResearchError(
            "STORAGE_INTERRUPTED",
            f"Interrupted storage migration at {found[0].parent}.",
            remediation=f"expnote project restore-storage {context.entry.id} --apply",
            exit_code=5,
        )


def _verify_vault_references(context, changes, reads):
    """A sync client can add a reference while large images are being copied."""
    resolved = context.resolved
    root = resolved.vault_root
    notes = {
        path: sha
        for path, sha in reads.items()
        if path.is_relative_to(root) and path.suffix.casefold() == ".md"
    }
    for path, (_, text) in changes.items():
        if (
            text is not None
            and path.is_relative_to(root)
            and path.suffix.casefold() == ".md"
        ):
            notes[path] = digest(text)
    entries, _ = walk_vault(root, context.profile)
    conflict = ConflictMatcher(context.profile)
    current = {
        root / entry.rel
        for entry in entries
        if entry.kind == "md"
        and not (root / entry.rel).is_symlink()
        and not conflict.matches(entry.rel)
    }
    if current != notes.keys():
        raise ResearchError(
            "STORAGE_CHANGED", "Vault notes changed. Preview again.", exit_code=5
        )
    for path, sha in notes.items():
        _check(path, {sha})


def _apply(context, manifest, changes, reads, tracked, ctx):
    from expnote.research.writeback import _fsync_directory

    resolved = context.resolved
    before = _positions(context.entry)
    after = manifest["positions"]
    stamp = f"{time.time_ns()}-{uuid4().hex[:8]}"
    log_path = _journal_root(resolved) / stamp / "journal.json"
    for path, sha in reads.items():
        _check(path, {sha})
    journal = {
        "state": "pending",
        "before": before,
        "after": after,
        **tracked,
        "files": [],
    }
    for i, (path, (old, new)) in enumerate(changes.items()):
        _check(path, {old})
        journal["files"].append(
            {
                "path": file_key(resolved, path),
                "before": old,
                "after": digest(new) if new is not None else None,
            }
        )
        if old is not None:
            raw = path.read_bytes()
            if digest(raw) != old:
                _check(path, {old})
                raise ResearchError("STORAGE_CHANGED", str(path), exit_code=5)
            atomic_bytes(_record_file(log_path, i), raw)
    from expnote.research.registry import registry_path

    atomic_bytes(log_path.parent / "registry.toml", registry_path().read_bytes())
    atomic_bytes(log_path.parent / "preview.json", _json(manifest))
    atomic_bytes(log_path, _json(journal))
    try:
        for path, (old, new) in changes.items():
            if new is None:
                continue
            _check(path, {old})
            if old is None:
                _create_no_clobber(path, new)
            else:
                atomic_bytes(path, new)
            _check(path, {digest(new)})
        if manifest["archiveImages"]:
            _verify_vault_references(context, changes, reads)
        for path, (old, new) in changes.items():
            if new is None:
                _check(path, {old})
                path.unlink()
                _fsync_directory(path.parent)
        _set_positions(context, after, allowed=[before])
        refresh_index(open_project(context.entry.id))
        journal["state"] = "complete"
        atomic_bytes(log_path, _json(journal))
    except (OSError, ResearchError) as error:
        try:
            _restore(context, log_path, journal)
        except (OSError, ResearchError) as recovery:
            raise ResearchError(
                "STORAGE_INTERRUPTED",
                f"{error} Recovery needs attention: {recovery}",
                remediation="Run project restore-storage before retrying.",
                exit_code=5,
                details={"backup": str(log_path.parent)},
            ) from None
        if isinstance(error, OSError):
            raise ResearchError("STORAGE_IO", str(error), exit_code=6) from None
        raise
    _append_audit(
        ctx,
        {
            "command": "project storage",
            "before": before,
            "after": after,
            "planHash": manifest["planHash"],
            "backup": str(log_path.parent),
        },
    )
    return str(log_path.parent)


def migrate_storage(
    project,
    ev=None,
    res=None,
    *,
    apply=False,
    expected=None,
    actor="cli",
    expected_required=True,
):
    context = open_project(project)
    requested = {
        kind: value for kind, value in (("ev", ev), ("res", res)) if value is not None
    }
    if any(value not in LOCATIONS for value in requested.values()):
        raise ResearchError(
            "STORAGE_LOCATION", "Choose vault or research.", exit_code=2
        )
    if requested and context.resolved.research_dir is None:
        raise ResearchError(
            "SPLIT_REQUIRED",
            "Migrate this project to the split layout first.",
            remediation=f"expnote project migrate-layout {project}",
            exit_code=2,
        )
    if not requested:
        pending = _pending(context.resolved)
        return CommandResult(
            {
                **location_data(context.resolved, context.profile),
                "interrupted": str(pending[0].parent) if pending else None,
            }
        )
    ctx = WriteContext.for_project(context, actor=actor)

    def run():
        current = open_project(project)
        require_storage_ready(current)
        shown = location_data(current.resolved, current.profile)
        targets = {
            kind: loc
            for kind, loc in requested.items()
            if current.entry.storage(kind) != loc
        }
        if not targets:
            return CommandResult(
                {**shown, "positions": _positions(current.entry), "applied": False}
            )
        if apply and expected_required and not expected:
            raise ResearchError(
                "STORAGE_EXPECTED", "Confirm a preview using --expect.", exit_code=2
            )
        manifest, changes, reads, tracked = _plan(
            current, targets, include_assets="research" in targets.values()
        )
        if not apply:
            return CommandResult({**shown, **manifest, "applied": False}, exit_code=4)
        if expected and manifest["planHash"] != expected:
            raise ResearchError(
                "STORAGE_CHANGED",
                "Files or project settings changed. Preview again.",
                exit_code=5,
            )
        if manifest["conflicts"]:
            raise ResearchError(
                "STORAGE_CONFLICT",
                "Resolve the preview conflicts.",
                exit_code=5,
                details=manifest,
            )
        backup = _apply(current, manifest, changes, reads, tracked, ctx)
        updated = open_project(project)
        return CommandResult(
            {
                **location_data(updated.resolved, updated.profile),
                **manifest,
                "backup": backup,
                "applied": True,
            }
        )

    try:
        if not apply:
            return run()
        with write_lock(ctx):
            return run()
    except (OSError, UnicodeError) as error:
        raise ResearchError("STORAGE_IO", str(error), exit_code=6) from None
