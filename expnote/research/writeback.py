"""The single write layer of the research module (design section 8).

Every vault write goes through here: one process-level flock, compare-and-swap on the
sha256 of the file's bytes, minimal addressable edits (the caller supplies a text
transform; this layer owns bytes, BOM and line endings), a dot-prefixed temp file in
the same directory, fsync and `os.replace`, a quiet window against editors that hold
the file open, an audit record plus a pre-image journal under the research state
directory (never in the vault), and an immediate re-index of what changed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import difflib
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from expnote.db import now_iso
from expnote.research.edits import decode_note, encode_note
from expnote.research.errors import ResearchError
from expnote.workspace import data_home

REPLACE_RETRIES = 3
REPLACE_BACKOFF_SECONDS = 0.2
LOCK_TIMEOUT_SECONDS = 15.0
DIFF_LIMIT_CHARS = 6000
JOURNAL_KEEP_DAYS = 30
STALE_TEMP_SECONDS = 3600
AUDIT_TYPE = "research.write"
TEMP_NAME = re.compile(r"^\..*expnote.*\.tmp$")
PROBE_DIR_NAME = "expnote-probe"
LINK_UNSUPPORTED = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}

_now = time.time  # tests replace this to move the clock


# ---- context and value types -------------------------------------------------


@dataclasses.dataclass
class WriteContext:
    """What a write needs; independent of a registered project (init writes first)."""

    project_id: str
    vault_key: str  # names the flock file; the registry's vault_id
    vault_root: Path
    quiet_window_seconds: int = 10
    actor: str = "cli"
    reindex: Callable[[], object] | None = None

    @classmethod
    def for_project(cls, context, actor: str | None = None) -> WriteContext:
        from expnote.research.index import refresh_index

        return cls(
            project_id=context.entry.id,
            vault_key=context.entry.vault_id,
            vault_root=context.resolved.vault_root,
            quiet_window_seconds=context.entry.quiet_window_seconds,
            actor=actor or os.environ.get("EXPNOTE_ACTOR") or "cli",
            reindex=lambda: refresh_index(context),
        )

    @property
    def real_root(self) -> str:
        return os.path.realpath(self.vault_root)


@dataclasses.dataclass(frozen=True)
class FileEdit:
    """One requested edit: a text transform of an existing file, or a new file."""

    path: Path
    op: str  # set-key | edit-row | add-row | edit-task | add-task | create | ...
    address: str  # e.g. "frontmatter:status", "table-row:D-02", "task:T-0007"
    transform: Callable[[str], str] | None = None
    new_text: str | None = None  # set (without transform) to create a new file
    expected_sha256: str | None = None  # CAS base; default: the sha read when planning
    external: bool = False  # a file outside the vault (its audit path is absolute)


@dataclasses.dataclass
class Change:
    path: Path
    rel: str
    op: str
    address: str
    before_sha256: str | None
    after_sha256: str
    before: bytes | None
    after: bytes
    diff: str = ""
    diff_truncated: bool = False

    @property
    def create(self) -> bool:
        return self.before is None

    def as_dict(self) -> dict:
        return {
            "path": self.rel,
            "absPath": str(self.path),
            "op": self.op,
            "address": self.address,
            "beforeSha256": self.before_sha256,
            "afterSha256": self.after_sha256,
            "unifiedDiff": self.diff,
            "diffTruncated": self.diff_truncated,
        }


@dataclasses.dataclass
class ApplyResult:
    applied: list[dict] = dataclasses.field(default_factory=list)
    warnings: list[dict] = dataclasses.field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- state directory: lock, audit, journal -----------------------------------


def research_state_dir() -> Path:
    return data_home() / "research"


def project_state_dir(project_id: str) -> Path:
    return research_state_dir() / "projects" / project_id.replace("/", "_")


def audit_path(ctx: WriteContext) -> Path:
    return project_state_dir(ctx.project_id) / "events.jsonl"


def journal_dir(ctx: WriteContext) -> Path:
    return project_state_dir(ctx.project_id) / "journal"


@contextlib.contextmanager
def write_lock(ctx: WriteContext) -> Iterator[None]:
    directory = research_state_dir() / ctx.vault_key.replace("/", "_")
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "write.lock", "a+")  # noqa: SIM115
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ResearchError(
                        "ENV_WRITE_LOCKED",
                        "another expnote process is writing this vault",
                        exit_code=6,
                        retryable=True,
                        remediation="retry in a few seconds",
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        handle.close()


def read_audit(ctx: WriteContext) -> list[dict]:
    path = audit_path(ctx)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            records.append(json.loads(line))
    return records


def _append_audit(ctx: WriteContext, payload: dict) -> None:
    path = audit_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "type": AUDIT_TYPE,
        "ts": now_iso(),
        "payload": {"actor": ctx.actor, "project": ctx.project_id, **payload},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _own_last_write_sha(ctx: WriteContext, rel: str) -> str | None:
    for record in reversed(read_audit(ctx)):
        payload = record.get("payload", {})
        if record.get("type") == AUDIT_TYPE and payload.get("path") == rel:
            return payload.get("afterSha256")
    return None


def _write_journal(ctx: WriteContext, change: Change) -> Path | None:
    if change.before is None:
        return None
    directory = journal_dir(ctx)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"{stamp}-{change.path.name[:80]}.orig"
    target.write_bytes(change.before)
    return target


def prune_journal(ctx: WriteContext) -> None:
    directory = journal_dir(ctx)
    if not directory.is_dir():
        return
    cutoff = _now() - JOURNAL_KEEP_DAYS * 86400
    for entry in directory.iterdir():
        with contextlib.suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                entry.unlink()


# ---- paths -------------------------------------------------------------------


def vault_rel(ctx: WriteContext, path: Path) -> str:
    """Vault-relative POSIX path; refuses anything that resolves outside the vault."""
    real = os.path.realpath(path)
    root = ctx.real_root
    if real != root and not real.startswith(root + os.sep):
        raise ResearchError(
            "USAGE_PATH_OUTSIDE_VAULT",
            f"{path} lies outside the vault {ctx.vault_root}",
            exit_code=2,
        )
    return Path(os.path.relpath(real, root)).as_posix()


# ---- planning ----------------------------------------------------------------


def _diff(rel: str, before: str, after: str) -> tuple[str, bool]:
    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        lineterm="",
    )
    text = "\n".join(lines)
    if len(text) > DIFF_LIMIT_CHARS:
        return text[:DIFF_LIMIT_CHARS] + "\n... (diff truncated)", True
    return text, False


def _read_target(ctx: WriteContext, path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise ResearchError(
            "NOT_FOUND", f"{path} does not exist", exit_code=3
        ) from None
    except OSError as error:
        raise ResearchError(
            "VAULT_UNAVAILABLE", f"cannot read {path}: {error}", exit_code=6
        ) from None


def cas_mismatch(path: Path, expected: str, current: str) -> ResearchError:
    return ResearchError(
        "CAS_MISMATCH",
        f"{path.name} changed since it was read (expected {expected[:12]}, "
        f"now {current[:12]})",
        exit_code=5,
        remediation="re-read the note (show) and redo the edit on the current text",
        details={
            "path": str(path),
            "expectedSha256": expected,
            "currentSha256": current,
        },
    )


def plan_changes(ctx: WriteContext, edits: list[FileEdit]) -> list[Change]:
    """Resolve edits against the current bytes; no-op edits are dropped."""
    changes: list[Change] = []
    for edit in edits:
        rel = str(edit.path) if edit.external else vault_rel(ctx, edit.path)
        if edit.transform is None:
            if edit.new_text is None:
                raise ValueError("a create edit needs new_text")
            if os.path.lexists(edit.path):
                raise _target_exists(edit.path)
            after = encode_note(edit.new_text)
            diff, cut = _diff(rel, "", edit.new_text)
            changes.append(
                Change(
                    edit.path,
                    rel,
                    edit.op,
                    edit.address,
                    None,
                    sha256_bytes(after),
                    None,
                    after,
                    diff,
                    cut,
                )  # fmt: skip
            )
            continue
        before = _read_target(ctx, edit.path)
        before_sha = sha256_bytes(before)
        if edit.expected_sha256 and edit.expected_sha256 != before_sha:
            raise cas_mismatch(edit.path, edit.expected_sha256, before_sha)
        text, fmt = decode_note(before)
        new_text = edit.transform(text)
        after = encode_note(new_text, fmt)
        if after == before:
            continue
        diff, cut = _diff(rel, text, new_text)
        changes.append(
            Change(
                edit.path,
                rel,
                edit.op,
                edit.address,
                before_sha,
                sha256_bytes(after),
                before,
                after,
                diff,
                cut,
            )  # fmt: skip
        )
    return changes


def _target_exists(path: Path) -> ResearchError:
    return ResearchError(
        "TARGET_EXISTS",
        f"{path} already exists; this command never overwrites",
        exit_code=5,
        details={"path": str(path)},
    )


# ---- applying ----------------------------------------------------------------


def _busy(path: Path) -> ResearchError:
    return ResearchError(
        "FILE_BUSY",
        f"{path.name} is held open by another process (Windows sync client or editor)",
        exit_code=5,
        retryable=True,
        remediation="retry in a few seconds",
        details={"path": str(path)},
    )


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt < REPLACE_RETRIES:
                time.sleep(REPLACE_BACKOFF_SECONDS)
    with contextlib.suppress(OSError):
        os.unlink(source)
    raise _busy(target)


def _temp_path(target: Path) -> Path:
    stem = target.name[:60]
    return target.with_name(f".{stem}.expnote.{os.getpid()}.{secrets.token_hex(4)}.tmp")


def _write_temp(target: Path, data: bytes, mode: int | None) -> Path:
    temp = _temp_path(target)
    with open(temp, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(temp, mode)
    return temp


def _fsync_directory(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _create_no_clobber(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _write_temp(target, data, None)
    try:
        os.link(temp, target)
    except FileExistsError:
        os.unlink(temp)
        raise _target_exists(target) from None
    except OSError:
        # No hard links here (some drvfs/9p mounts): exists-check, then replace,
        # both under the write lock.
        if os.path.lexists(target):
            os.unlink(temp)
            raise _target_exists(target) from None
        _replace_with_retry(temp, target)
    else:
        os.unlink(temp)
    _fsync_directory(target.parent)


def _check_quiet(ctx: WriteContext, change: Change, current_sha: str, no_quiet: bool):
    window = ctx.quiet_window_seconds
    if no_quiet or window <= 0:
        return
    age = _now() - change.path.stat().st_mtime
    if 0 <= age < window and _own_last_write_sha(ctx, change.rel) != current_sha:
        wait_ms = math.ceil((window - age) * 1000)
        raise ResearchError(
            "RECENTLY_MODIFIED",
            f"{change.path.name} was modified {age:.1f} s ago (quiet window "
            f"{window} s); an editor may still be saving it",
            exit_code=5,
            retryable=True,
            remediation=f"retry in {wait_ms / 1000:.0f} s",
            details={"retryAfterMs": wait_ms, "path": change.rel},
        )


def _apply_one(
    ctx: WriteContext,
    change: Change,
    *,
    command: str,
    authorization: str | None,
    no_quiet: bool,
) -> dict | None:
    if change.create:
        _create_no_clobber(change.path, change.after)
        journal = None
    else:
        current = _read_target(ctx, change.path)
        current_sha = sha256_bytes(current)
        if current_sha == change.after_sha256:
            return None  # the bytes are already there: no mtime bump, no event
        if current_sha != change.before_sha256:
            raise cas_mismatch(change.path, change.before_sha256 or "", current_sha)
        _check_quiet(ctx, change, current_sha, no_quiet)
        journal = _write_journal(ctx, change)
        mode = change.path.stat().st_mode & 0o7777
        temp = _write_temp(change.path, change.after, mode)
        # An unexpected error here behaves like a crash: the original is intact and
        # the stale temp is left for `doctor` to remove.
        _replace_with_retry(temp, change.path)
        _fsync_directory(change.path.parent)
    payload = {
        "command": command,
        "path": change.rel,
        "op": change.op,
        "address": change.address,
        "beforeSha256": change.before_sha256,
        "afterSha256": change.after_sha256,
        "authorization": authorization,
        "noQuiet": bool(no_quiet),
        "journal": str(journal) if journal else None,
    }
    _append_audit(ctx, payload)
    return {**change.as_dict(), "journal": payload["journal"]}


def _reindex(ctx: WriteContext, result: ApplyResult) -> None:
    if ctx.reindex is None:
        return
    try:
        ctx.reindex()
    except ResearchError as error:
        result.warnings.append({"code": "REINDEX_FAILED", "message": error.message})


def apply_changes(
    ctx: WriteContext,
    changes: list[Change],
    *,
    command: str,
    authorization: str | None = None,
    no_quiet: bool = False,
) -> ApplyResult:
    """Apply planned changes in one burst; a failure reports which files changed."""
    result = ApplyResult()
    try:
        with write_lock(ctx):
            for change in changes:
                try:
                    record = _apply_one(
                        ctx,
                        change,
                        command=command,
                        authorization=authorization,
                        no_quiet=no_quiet,
                    )
                except ResearchError as error:
                    error.details["applied"] = [a["path"] for a in result.applied]
                    error.details["failedPath"] = change.rel
                    raise
                if record is not None:
                    result.applied.append(record)
            prune_journal(ctx)
    finally:
        if result.applied:
            _reindex(ctx, result)
    return result


# ---- undo --------------------------------------------------------------------


def undo_write(
    ctx: WriteContext, journal: Path | str, *, no_quiet: bool = True
) -> ApplyResult:
    """Restore a journal pre-image; refuses if the file moved on since that write."""
    journal = Path(journal)
    record = next(
        (
            r["payload"]
            for r in reversed(read_audit(ctx))
            if r.get("type") == AUDIT_TYPE
            and r["payload"].get("journal") == str(journal)
        ),
        None,
    )
    if record is None:
        raise ResearchError(
            "NOT_FOUND",
            f"no audit record names the journal file {journal}",
            exit_code=3,
        )
    target = ctx.vault_root / record["path"]
    current = _read_target(ctx, target)
    current_sha = sha256_bytes(current)
    if current_sha != record["afterSha256"]:
        raise cas_mismatch(target, record["afterSha256"], current_sha)
    original = journal.read_bytes()
    if sha256_bytes(original) != record["beforeSha256"]:
        raise ResearchError(
            "JOURNAL_CORRUPT",
            f"{journal.name} does not hash to the recorded pre-image",
            exit_code=6,
        )
    change = Change(
        target,
        record["path"],
        "restore",
        f"journal:{journal.name}",
        current_sha,
        record["beforeSha256"],
        current,
        original,
    )
    return apply_changes(ctx, [change], command="undo", no_quiet=no_quiet)


# ---- deleting (stale temps, probe cleanup) -----------------------------------


def delete_paths(ctx: WriteContext, paths: list[Path], *, command: str) -> list[str]:
    """Delete files (and report vault-relative paths) under the lock, audited."""
    removed: list[str] = []
    with write_lock(ctx):
        for path in paths:
            rel = vault_rel(ctx, path)
            try:
                digest = sha256_bytes(path.read_bytes())
                os.unlink(path)
            except FileNotFoundError:
                continue
            except PermissionError:
                raise _busy(path) from None
            _append_audit(
                ctx,
                {
                    "command": command,
                    "path": rel,
                    "op": "delete",
                    "address": "file",
                    "beforeSha256": digest,
                    "afterSha256": None,
                    "authorization": None,
                    "noQuiet": False,
                    "journal": None,
                },
            )
            removed.append(rel)
    return removed


def remove_empty_directory(ctx: WriteContext, directory: Path) -> bool:
    vault_rel(ctx, directory)
    with write_lock(ctx), contextlib.suppress(OSError):
        directory.rmdir()
        return True
    return False


def stale_temp_files(
    root: Path, skip_dirs: frozenset[str] = frozenset(), now: float | None = None
) -> list[Path]:
    """Leftover `.expnote.*.tmp` files older than an hour (the probe is exempt).

    Walks the directories the index walks: those named in `skip_dirs` and every
    dot-directory are pruned, so `doctor` deletes only what it listed.
    """
    now = _now() if now is None else now
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in skip_dirs and d != PROBE_DIR_NAME and not d.startswith(".")
        ]
        for name in filenames:
            if not TEMP_NAME.match(name):
                continue
            path = Path(dirpath) / name
            with contextlib.suppress(OSError):
                if now - path.stat().st_mtime > STALE_TEMP_SECONDS:
                    found.append(path)
    return sorted(found)


# ---- sync probe --------------------------------------------------------------


def probe_paths(project_dir: Path) -> dict:
    """Existing probe files, if any (names carry a random suffix)."""
    directory = project_dir / PROBE_DIR_NAME
    found: dict = {"dir": directory, "temp": None, "plain": None}
    if directory.is_dir():
        for entry in sorted(directory.iterdir()):
            if TEMP_NAME.match(entry.name):
                found["temp"] = entry
            elif entry.name.startswith("expnote-probe-"):
                found["plain"] = entry
    return found


def plan_probe(ctx: WriteContext, project_dir: Path) -> tuple[list[Change], dict]:
    """Changes that create the two probe files (the existing ones are reused)."""
    existing = probe_paths(project_dir)
    suffix = secrets.token_hex(3)
    stamp = now_iso()
    paths = {
        "temp": existing["temp"] or existing["dir"] / f".expnote.probe.{suffix}.tmp",
        "plain": existing["plain"] or existing["dir"] / f"expnote-probe-{suffix}.txt",
    }
    edits = [
        FileEdit(
            paths[kind],
            "create",
            "probe",
            new_text=f"expnote sync probe ({kind}) created {stamp}\n",
        )
        for kind in ("temp", "plain")
        if not paths[kind].exists()
    ]
    return plan_changes(ctx, edits), {"dir": existing["dir"], **paths}


# ---- journaled deletes and renames (migration) -------------------------------


def _journal_copy(ctx: WriteContext, rel: str, data: bytes) -> Path:
    directory = journal_dir(ctx)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    tag = sha256_bytes(rel.encode("utf-8"))[:8]
    target = directory / f"{stamp}-{tag}-{Path(rel).name[:60]}.orig"
    target.write_bytes(data)
    return target


def journaled_delete(
    ctx: WriteContext,
    paths: list[Path],
    *,
    command: str,
    expected_sha256: dict[str, str] | None = None,
) -> list[dict]:
    """Delete files after copying each pre-image to the journal; audited.

    `expected_sha256` (vault-relative path -> sha) is a CAS: a file whose bytes differ
    is refused before anything is deleted.
    """
    expected_sha256 = expected_sha256 or {}
    planned: list[tuple[Path, str, bytes, str]] = []
    for path in paths:
        rel = vault_rel(ctx, path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
        digest = sha256_bytes(data)
        wanted = expected_sha256.get(rel)
        if wanted and wanted != digest:
            raise cas_mismatch(path, wanted, digest)
        planned.append((path, rel, data, digest))
    removed: list[dict] = []
    with write_lock(ctx):
        for path, rel, data, digest in planned:
            journal = _journal_copy(ctx, rel, data)
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            except PermissionError:
                raise _busy(path) from None
            payload = {
                "command": command,
                "path": rel,
                "op": "delete",
                "address": "file",
                "beforeSha256": digest,
                "afterSha256": None,
                "authorization": None,
                "noQuiet": True,
                "journal": str(journal),
            }
            _append_audit(ctx, payload)
            removed.append({"path": rel, "journal": str(journal)})
    return removed


def rename_path(ctx: WriteContext, source: Path, target: Path, *, command: str) -> dict:
    """Rename a note without changing its bytes (no clobber); audited."""
    source_rel = vault_rel(ctx, source)
    target_rel = vault_rel(ctx, target)
    data = _read_target(ctx, source)
    digest = sha256_bytes(data)
    with write_lock(ctx):
        if os.path.lexists(target):
            raise _target_exists(target)
        journal = _journal_copy(ctx, source_rel, data)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(source, target)
        except PermissionError:
            raise _busy(source) from None
        _fsync_directory(target.parent)
        payload = {
            "command": command,
            "path": target_rel,
            "op": "rename",
            "address": f"from:{source_rel}",
            "beforeSha256": digest,
            "afterSha256": digest,
            "authorization": None,
            "noQuiet": True,
            "journal": str(journal),
        }
        _append_audit(ctx, payload)
    return {"path": target_rel, "from": source_rel, "journal": str(journal)}


# ---- tool-owned derived files (the decision export) --------------------------------


def plan_owned_file(
    ctx: WriteContext,
    path: Path,
    data: bytes,
    address: str = "decision-export",
) -> Change | None:
    """The change that makes a tool-owned file hold `data`; None when it already does.

    No CAS and no quiet window: the tool alone writes this file. The caller decides
    whether a file it did not write itself may be replaced.
    """
    rel = vault_rel(ctx, path)
    try:
        before = path.read_bytes()
    except FileNotFoundError:
        before = None
    except OSError as error:
        raise ResearchError(
            "VAULT_UNAVAILABLE", f"cannot read {path}: {error}", exit_code=6
        ) from None
    if before == data:
        return None
    old = before.decode("utf-8", errors="replace") if before is not None else ""
    diff, cut = _diff(rel, old, data.decode("utf-8", errors="replace"))
    return Change(
        path,
        rel,
        "export",
        address,
        sha256_bytes(before) if before is not None else None,
        sha256_bytes(data),
        before,
        data,
        diff,
        cut,
    )


def write_owned_file(ctx: WriteContext, change: Change, *, command: str) -> dict:
    """Replace (or create) the file with `change.after`; audited, no pre-image."""
    with write_lock(ctx):
        change.path.parent.mkdir(parents=True, exist_ok=True)
        temp = _write_temp(change.path, change.after, None)
        _replace_with_retry(temp, change.path)
        _fsync_directory(change.path.parent)
        _append_audit(
            ctx,
            {
                "command": command,
                "path": change.rel,
                "op": change.op,
                "address": change.address,
                "beforeSha256": change.before_sha256,
                "afterSha256": change.after_sha256,
                "authorization": None,
                "noQuiet": True,
                "journal": None,
            },
        )
    return {**change.as_dict(), "journal": None}
