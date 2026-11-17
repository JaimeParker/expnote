"""Publish generated EV, appendices and results without replacing a user's edits."""

from __future__ import annotations

import json
from pathlib import Path

from expnote.research.edits import (
    NEW_FILE_FORMAT,
    decode_note,
    edit_frontmatter,
    encode_note,
)
from expnote.research.errors import ResearchError
from expnote.research.model import parse_note
from expnote.research.newnote import check_name
from expnote.research.storage import equivalent_digest
from expnote.research.storage_media import ImageCopies
from expnote.research.storage_paths import document_dir
from expnote.research.writeback import FileEdit, _create_no_clobber, cas_mismatch

DOCUMENT_KINDS = {"result": "res", "evidence": "ev", "evidence-appendix": "appendix"}


def image_plan(view, path, text, kind):
    resolved = view.context.resolved
    images = ImageCopies(resolved, view.snapshot.attachment_paths.values())
    rewritten = images.rewrite(text, path, resolved.entry.storage(kind))
    return rewritten, images


def apply_image_copies(images, *, apply):
    from expnote.research.storage import record_image_copies
    from expnote.research.storage_paths import file_key
    from expnote.research.workflow import digest

    created = {}
    for path, (source, expected) in images.copies.items():
        raw = source.read_bytes()
        if digest(raw) != expected:
            raise cas_mismatch(source, expected, digest(raw))
        if path.is_symlink() or (path.exists() and path.read_bytes() != raw):
            raise ResearchError("IMAGE_CONFLICT", str(path), exit_code=5)
        if apply and not path.exists():
            _create_no_clobber(path, raw)
            if path.is_relative_to(images.resolved.vault_root):
                created[file_key(images.resolved, path)] = expected
    record_image_copies(images.resolved, created)
    return [path.name for path in images.copies]


def publish_result(view, ident, candidate, expected, apply):
    return publish_document(view, ident, candidate, expected, apply, result=True)


def publish_document(view, ident, candidate: Path, expected, apply, *, result=False):
    from expnote.research import entities
    from expnote.research.layout import record_id
    from expnote.research.workflow import FIELDS, digest, identity, put, schema
    from expnote.research.writes import StoreOp, WritePlan

    resolved = view.context.resolved
    if not resolved.research_dir:
        raise ResearchError(
            "SPLIT_REQUIRED",
            "Generated publication requires a split project.",
            exit_code=2,
        )
    text = candidate.read_text(encoding="utf-8-sig")
    proposed = parse_note(candidate, candidate.name, text)
    allowed = {"result"} if result else {"evidence", "evidence-appendix"}
    if proposed.fm_error or proposed.type not in allowed:
        raise ResearchError(
            "PUBLICATION_TYPE",
            f"Expected Markdown of type {', '.join(sorted(allowed))}.",
            exit_code=2,
        )
    kind = DOCUMENT_KINDS[proposed.type]
    directory = document_dir(resolved, view.profile, kind)
    try:
        note = entities.find_by_id(view, proposed.type, ident)
    except ResearchError as error:
        if error.code != "NOT_FOUND" or result or ident.startswith("record-"):
            raise
        note = None

    source = note.path if note else directory / f"{check_name(ident)}.md"
    if not source.resolve().is_relative_to(directory.resolve()):
        raise ResearchError("PUBLICATION_PATH", str(source), exit_code=5)
    if not note and (source.exists() or view.snapshot.md_index.get(ident.casefold())):
        raise ResearchError("NAME_COLLISION", str(source), exit_code=5)
    raw = source.read_bytes() if note else None
    sha = digest(raw) if raw is not None else None
    if expected is not None and expected != sha:
        raise cas_mismatch(source, expected, sha or "missing")
    original, fmt = decode_note(raw) if raw is not None else ("", NEW_FILE_FORMAT)
    current = parse_note(source, source.name, original) if note else None
    proposed_plan = proposed.link_field("plan")
    if (
        not current and (not proposed_plan or view.snapshot.note(proposed_plan) is None)
    ) or (
        current
        and (
            proposed.get("id") != current.get("id")
            or proposed_plan != current.link_field("plan")
            or (
                kind == "appendix"
                and proposed.link_field("parent") != current.link_field("parent")
            )
        )
    ):
        raise ResearchError(
            "PUBLICATION_IDENTITY",
            "The candidate must preserve its identity and plan.",
            exit_code=2,
        )
    ident = (
        identity(view.project_id, note)
        if note
        else str(
            proposed.get("id") or record_id(view.project_id, f"{kind}/{source.name}")
        )
    )
    keys = [("record_id", json.dumps(ident))]
    if current and "aliases" in current.fm:
        keys.append(("aliases", json.dumps(current.get("aliases"), ensure_ascii=False)))
    text = edit_frontmatter(text, keys, remove=FIELDS)
    text, images = image_plan(view, source, text, kind)
    generated = digest(encode_note(text, fmt))
    old = view.cache["workflow"].get(ident, {})
    publication = old.get("publication", {})
    prior = publication.get("sha")
    pending = (
        raw is not None
        and expected is None
        and sha != generated
        and not (prior and equivalent_digest(resolved, source, sha, prior))
    )
    data = {
        "id": ident,
        "path": str(source),
        "currentSha256": sha,
        "candidateSha256": generated,
        "pending": pending,
    }
    if pending:
        path = resolved.research_dir / "proposals" / f"{generated}.md"
        data["proposal"] = str(path)
        edits = (
            []
            if path.is_file()
            else [FileEdit(path, "create", "publication-proposal", new_text=text)]
        )
        update = {**publication, "pending": generated, "base": sha}
    else:
        data["images"] = apply_image_copies(images, apply=False)
        edits = [
            FileEdit(
                source,
                "replace",
                "published-body",
                transform=lambda _: text,
                expected_sha256=sha,
            )
            if note
            else FileEdit(source, "create", "published-body", new_text=text)
        ]
        update = {"sha": generated}

    def transact(store, actor, at):
        schema(store.conn)
        row = store.conn.execute(
            "SELECT state FROM workflow WHERE id=?", (ident,)
        ).fetchone()
        try:
            live_sha = digest(source.read_bytes())
        except FileNotFoundError:
            live_sha = None
        if (json.loads(row[0]) if row else {}) != old or live_sha != sha:
            raise ResearchError(
                "CONFLICT", "Document changed; reload before publishing.", exit_code=5
            )
        if not pending:
            apply_image_copies(images, apply=apply)
        state = {**old, "publication": update}
        if state != old:
            put(
                store.conn,
                ident,
                state,
                "document-proposed" if pending else "document-published",
                actor,
                at,
            )

    return WritePlan(
        edits,
        data,
        store_op=StoreOp(
            "workflow",
            ident,
            "Stage proposal" if pending else "Publish document",
            transact,
        ),
    )


def snapshot_images(view, images, source, *, apply):
    from expnote.research.layout import atomic_bytes
    from expnote.research.storage_media import sha_file
    from expnote.research.storage_paths import file_key
    from expnote.research.workflow import digest

    resolved = view.context.resolved
    directory = resolved.research_dir / "revisions" / "assets"
    manifest = {}
    for path, sha in images.referenced(source).items():
        manifest[file_key(resolved, path)] = sha
        if apply:
            raw = path.read_bytes()
            if digest(raw) != sha:
                raise cas_mismatch(path, sha, digest(raw))
            target = directory / sha
            if not target.is_file():
                atomic_bytes(target, raw)
            if sha_file(target) != sha:
                raise ResearchError("REVISION_INVALID", str(target), exit_code=6)
    return manifest


def reviewed_source_changed(view, note, state, *, evidence=False):
    """Compare today's editable body and images with the reviewed source."""
    from expnote.research.model import parse_note
    from expnote.research.workflow import digest, revision_text

    resolved = view.context.resolved
    revision = state.get("reviewedEvidenceRevision" if evidence else "revision")
    if not resolved.research_dir or not revision:
        return False
    raw = note.path.read_bytes()
    approved = state.get("reviewedSourceSha") if evidence else revision
    if approved and not equivalent_digest(resolved, note.path, digest(raw), approved):
        if evidence:
            return True
        current = parse_note(note.path, note.rel, raw.decode("utf-8-sig"))
        submitted = parse_note(
            note.path, note.rel, revision_text(view.project_id, revision)
        )
        if current.body.strip() != submitted.body.strip():
            return True
    archived = state.get("revisionImages", {}).get(revision)
    if archived:
        _, selected = image_plan(
            view, note.path, raw.decode("utf-8-sig"), "ev" if evidence else "res"
        )
        return set(archived.values()) != set(selected.referenced(note.path).values())
    return False
