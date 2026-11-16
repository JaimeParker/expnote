"""Vault-owned result files and safe publication of generated prose."""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

from expnote.research.edits import decode_note, edit_frontmatter, encode_note
from expnote.research.envelope import CommandResult
from expnote.research.errors import ResearchError
from expnote.research.layout import atomic_bytes
from expnote.research.model import parse_note
from expnote.research.newnote import folders_for
from expnote.research.registry import load_registry, open_project, save_registry
from expnote.research.textkit import iter_links, link_stem
from expnote.research.writeback import (
    FileEdit,
    WriteContext,
    _create_no_clobber,
    cas_mismatch,
    write_lock,
)


def result_dir(resolved, profile) -> Path:
    folder = folders_for(profile)["res"] if profile else "results"
    root = (
        resolved.project_dir
        if resolved.entry.results_in_vault
        else resolved.material_dir
    )
    path = root / folder
    if not path.resolve().is_relative_to(root.resolve()):
        raise ResearchError(
            "RESULT_PATH", "result directory is outside its project", exit_code=2
        )
    return path


def _embedded_assets(resolved, text: str) -> list[Path]:
    from expnote.research.index import IMAGE_EXTS

    root = resolved.images_dir.resolve()
    paths = set()
    for embed, reference in iter_links(text):
        if not embed:
            continue
        name = link_stem(reference)
        if Path(name).suffix.lower().lstrip(".") not in IMAGE_EXTS:
            continue
        image = (root / name).resolve()
        if image.is_relative_to(root) and image.is_file():
            paths.add(image)
    return sorted(paths)


def sync_result_images(context, text: str, *, apply: bool) -> list[str]:
    resolved = context.resolved
    directory = result_dir(resolved, context.profile) / "_assets"
    if not directory.resolve().is_relative_to(resolved.project_dir.resolve()):
        raise ResearchError(
            "RESULT_IMAGE_PATH", "images directory is outside the project", exit_code=5
        )
    names = []
    for image in _embedded_assets(resolved, text):
        target = directory / image.name
        raw = image.read_bytes()
        if target.is_symlink() or (target.exists() and target.read_bytes() != raw):
            raise ResearchError("RESULT_IMAGE_CONFLICT", str(target), exit_code=5)
        if apply and not target.exists():
            _create_no_clobber(target, raw)
        names.append(image.name)
    return names


def migrate_results(project: str, apply: bool) -> CommandResult:
    context = open_project(project)
    resolved = context.resolved
    if resolved.research_dir is None:
        raise ResearchError(
            "SPLIT_REQUIRED", "this command requires a split project", exit_code=2
        )
    if context.entry.results_in_vault:
        return CommandResult({"resultsInVault": True, "applied": False})
    source = result_dir(resolved, context.profile)
    folder = source.relative_to(resolved.research_dir)
    target = resolved.project_dir / folder
    if (
        not target.resolve().is_relative_to(resolved.project_dir.resolve())
        or target.exists()
    ):
        raise ResearchError("RESULT_TARGET_EXISTS", str(target), exit_code=5)
    backup = resolved.research_dir / "migration" / "results-in-vault"
    archived = backup / "results"
    if not source.is_dir() or archived.exists():
        raise ResearchError(
            "RESULT_MIGRATION_STATE", f"inspect {source} and {backup}", exit_code=5
        )
    notes = sorted(source.rglob("*.md"))
    image_names = sorted(
        {
            image.name
            for note in notes
            for image in _embedded_assets(
                resolved, note.read_text(encoding="utf-8-sig")
            )
        }
    )
    data = {
        "source": str(source),
        "target": str(target),
        "notes": len(notes),
        "images": image_names,
        "backup": str(backup),
        "applied": False,
    }
    if not apply:
        return CommandResult(data, exit_code=4)
    ctx = WriteContext.for_project(context)
    with write_lock(ctx):
        registry = load_registry()
        if registry.projects.get(project) != context.entry or target.exists():
            raise ResearchError(
                "CONFLICT", "project changed; preview again", exit_code=5
            )
        backup.mkdir(parents=True, exist_ok=True)
        from expnote.research.registry import registry_path

        atomic_bytes(backup / "registry.toml", registry_path().read_bytes())
        staged = target.with_name(f".{target.name}.expnote-stage")
        if staged.exists():
            raise ResearchError(
                "RESULT_MIGRATION_STATE",
                f"inspect staging folder {staged}",
                exit_code=5,
            )
        target_created = False
        try:
            shutil.copytree(source, staged, symlinks=True)
            if any(p.is_symlink() for p in staged.rglob("*")):
                raise ResearchError(
                    "RESULT_MIGRATION_SYMLINK", str(staged), exit_code=5
                )
            current = [p.relative_to(source) for p in source.rglob("*") if p.is_file()]
            if any(
                (source / p).read_bytes() != (staged / p).read_bytes() for p in current
            ):
                raise ResearchError(
                    "CONFLICT", "a result changed during copying", exit_code=5
                )
            for name in image_names:
                _create_no_clobber(
                    staged / "_assets" / name, (resolved.images_dir / name).read_bytes()
                )
            staged.rename(target)
            target_created = True
            source.rename(archived)
            registry.projects[project] = dataclasses.replace(
                context.entry, results_in_vault=True
            )
            save_registry(registry)
        except BaseException:
            if archived.exists() and not source.exists():
                archived.rename(source)
            if target_created:
                target.rename(staged)
            if staged.exists():
                shutil.rmtree(staged)
            raise
    data.update(resultsInVault=True, applied=True)
    return CommandResult(data)


def publish_result(
    view, ident: str, candidate: Path, expected: str | None, apply: bool
):
    from expnote.research import entities
    from expnote.research.workflow import FIELDS, digest, put, schema
    from expnote.research.writes import StoreOp, WritePlan

    if not view.context.entry.results_in_vault:
        raise ResearchError(
            "RESULT_LAYOUT_REQUIRED", "enable results-in-vault first", exit_code=2
        )
    note = entities.find_by_id(view, "result", ident)
    ident = str(note.get("id"))
    source = note.path.read_bytes()
    original, format_info = decode_note(source)
    sha = digest(source)
    if expected is not None and expected != sha:
        raise cas_mismatch(note.path, expected, sha)
    proposed = parse_note(
        note.path, note.rel, candidate.read_text(encoding="utf-8-sig")
    )
    current = parse_note(note.path, note.rel, original)
    if (
        proposed.fm_error
        or proposed.type != "result"
        or proposed.get("id") != ident
        or proposed.link_field("plan") != current.link_field("plan")
    ):
        raise ResearchError(
            "RESULT_IDENTITY", "candidate must preserve type, id and plan", exit_code=2
        )
    keys = [
        (key, json.dumps(current.fm[key], ensure_ascii=False))
        for key in ("record_id", "aliases")
        if key in current.fm
    ]
    text = edit_frontmatter(proposed.text, keys, remove=FIELDS)
    generated = digest(encode_note(text, format_info))
    old = view.cache["workflow"].get(ident, {})
    publication = old.get("publication", {})
    pending = expected is None and sha != publication.get("sha") and sha != generated
    data = {
        "id": ident,
        "path": str(note.path),
        "currentSha256": sha,
        "candidateSha256": generated,
        "pending": pending,
    }
    if pending:
        path = view.context.resolved.research_dir / "proposals" / f"{generated}.md"
        data["proposal"] = str(path)
        edits = (
            []
            if path.exists()
            else [FileEdit(path, "create", "result-proposal", new_text=text)]
        )
        update = {**publication, "pending": generated, "base": sha}
    else:
        data["images"] = sync_result_images(view.context, text, apply=False)
        edits = [
            FileEdit(
                note.path,
                "replace",
                "result-body",
                transform=lambda _: text,
                expected_sha256=sha,
            )
        ]
        update = {"sha": generated}

    def transact(store, actor, at):
        schema(store.conn)
        row = store.conn.execute(
            "SELECT state FROM workflow WHERE id=?", (ident,)
        ).fetchone()
        if (json.loads(row[0]) if row else {}) != old or digest(
            note.path.read_bytes()
        ) != sha:
            raise ResearchError(
                "CONFLICT", "result changed; reload before publishing", exit_code=5
            )
        if not pending:
            sync_result_images(view.context, text, apply=apply)
        state = {**old, "publication": update}
        if state != old:
            put(
                store.conn,
                ident,
                state,
                "result-proposed" if pending else "result-published",
                actor,
                at,
            )

    return WritePlan(
        edits,
        data,
        store_op=StoreOp(
            "workflow",
            ident,
            "Stage proposal" if pending else "Publish result",
            transact,
        ),
    )
