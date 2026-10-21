"""`doctor`: environment and hygiene checks (never content; see `check` for that)."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime
from pathlib import PurePosixPath

from expnote.db import read_config
from expnote.research.errors import MissingDependency, ResearchError
from expnote.research.frontmatter import import_yaml
from expnote.research.index import (
    INDEX_SCHEMA,
    ensure_index,
    linked_workspaces,
    open_reader,
    project_rel,
    trigram_supported,
    walk_vault,
)
from expnote.research.profile import read_profile_note
from expnote.research.registry import (
    ProjectContext,
    ProjectEntry,
    ResolvedProject,
    load_registry,
    profile_or_raise,
    revalidate,
    select_entry,
)
from expnote.research.runs import open_workspace
from expnote.research.textkit import nfc
from expnote.workspace import resolve_workspace

LIST_LIMIT = 20
STALE_TEMP_SECONDS = 3600
CODE_EXTS = frozenset({"py", "pyc", "pyo", "sh", "js", "ts", "rs", "c", "cpp", "h"})
CODE_DIRS = frozenset({"__pycache__", "node_modules"})
ILLEGAL_NAME_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
TEMP_NAME = re.compile(r"^\..*expnote.*\.tmp$")


@dataclasses.dataclass
class Check:
    id: str
    status: str  # ok | warn | fail | info
    message: str
    remediation: str | None = None
    items: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self, full: bool) -> dict:
        shown = self.items if full else self.items[:LIST_LIMIT]
        out = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "remediation": self.remediation,
            "count": len(self.items),
            "items": shown,
        }
        if len(shown) < len(self.items):
            out["truncated"] = True
        return out


def _ok(check_id: str, message: str) -> Check:
    return Check(check_id, "ok", message)


def _list_check(
    check_id: str,
    items: list[str],
    ok_message: str,
    warn_message: str,
    remediation: str,
    status: str = "warn",
) -> Check:
    if not items:
        return _ok(check_id, ok_message)
    return Check(check_id, status, f"{len(items)} {warn_message}", remediation, items)


def doctor_data(checks: list[Check], full: bool = False) -> dict:
    """The `doctor` data block: every check plus a status summary."""
    summary = {
        s: sum(1 for c in checks if c.status == s)
        for s in ("ok", "warn", "fail", "info")
    }
    return {"checks": [c.as_dict(full) for c in checks], "summary": summary}


def pending_changes(context: ProjectContext) -> int | None:
    """Files that differ from the index by (size, mtime), without writing anything."""
    try:
        conn = open_reader(context.entry.vault_id)
    except ResearchError:
        return None
    try:
        known = {
            rel: (size, mtime)
            for rel, size, mtime in conn.execute(
                "SELECT rel, size, mtime_ns FROM files"
            )
        }
    finally:
        conn.close()
    entries, _ = walk_vault(context.resolved.vault_root, context.profile)
    walked = {e.rel: (e.size, e.mtime_ns) for e in entries}
    changed = sum(1 for rel, stat in walked.items() if known.get(rel) != stat)
    return changed + sum(1 for rel in known if rel not in walked)


def run_doctor(
    project_id: str | None,
    *,
    refresh: bool = True,
    now: datetime | None = None,
    check_freshness: bool = True,
) -> tuple[list[Check], dict]:
    """(checks, meta). Unreachable vaults are a failed check, not an exception."""
    entry = select_entry(load_registry(), project_id)
    checks: list[Check] = []
    meta: dict = {"project": entry.id, "index": None, "pathNote": None}
    try:
        resolved = revalidate(entry)
    except ResearchError as error:
        checks.append(
            Check("vault-reachable", "fail", error.message, error.remediation)
        )
        return checks, meta
    meta["pathNote"] = resolved.path_note
    checks.append(_ok("vault-reachable", f"vault root {resolved.vault_root}"))
    context = _profile_checks(entry, resolved, checks)
    checks += _environment_checks()
    if context is None:
        return checks, meta
    checks += _workspace_checks(context)
    index_check, stats = _index_checks(context, refresh, checks, check_freshness)
    if stats is not None:
        meta["index"] = stats.meta()
    if index_check:
        checks += _hygiene_checks(context, now or datetime.now().astimezone())
    return checks, meta


def _profile_checks(
    entry: ProjectEntry, resolved: ResolvedProject, checks: list[Check]
) -> ProjectContext | None:
    note = read_profile_note(resolved.profile_note)
    if not note.found:
        checks.append(
            Check("profile-note", "fail", note.problems[0], "expnote new profile")
        )
        return None
    try:
        profile = profile_or_raise(note, entry.id)
    except ResearchError as error:
        checks.append(Check("profile-note", "fail", error.message, None, note.problems))
        return None
    checks.append(
        _ok("profile-note", f"profile parsed (project_id {profile.project_id!r})")
    )
    if profile.unknown_keys:
        checks.append(
            Check(
                "profile-keys",
                "warn",
                f"{len(profile.unknown_keys)} profile key(s) the tool does not know",
                "check spelling in the profile note",
                list(profile.unknown_keys),
            )
        )
    return ProjectContext(resolved, profile, note.sha256 or "")


def _environment_checks() -> list[Check]:
    checks = []
    try:
        import_yaml()
        checks.append(_ok("pyyaml", "PyYAML is installed"))
    except MissingDependency as error:
        checks.append(Check("pyyaml", "fail", error.message, error.remediation))
    if trigram_supported():
        checks.append(_ok("fts-trigram", "SQLite FTS5 trigram tokenizer available"))
    else:
        checks.append(
            Check(
                "fts-trigram",
                "warn",
                "ENV_FTS_TRIGRAM_MISSING: search degrades to LIKE",
                "use SQLite >= 3.34",
            )
        )
    return checks


def _workspace_checks(context: ProjectContext) -> list[Check]:
    names = linked_workspaces(context)
    if not names:
        return [Check("workspaces", "info", "no workspace linked to this project")]
    problems = []
    for name in names:
        try:
            open_workspace(name)[0].close()
        except Exception as error:  # noqa: BLE001 - any failure is a finding
            problems.append(f"{name}: {error}")
    return [
        _list_check(
            "workspaces",
            problems,
            f"{len(names)} linked workspace database(s) readable",
            "linked workspace(s) not readable",
            "expnote workspace list; fix the path or re-run import",
        )
    ]


def _index_checks(
    context: ProjectContext,
    refresh: bool,
    checks: list[Check],
    check_freshness: bool = True,
) -> tuple[bool, object | None]:
    from expnote.research.index import _meta_get

    stats = None
    try:
        if refresh:
            reader = ensure_index(context)
            stats = reader.stats
        else:
            reader = ensure_index(context, refresh=False)
            if check_freshness:  # walking the vault is what costs on slow mounts
                pending = pending_changes(context)
                checks.append(
                    _ok("index-fresh", "index is current")
                    if not pending
                    else Check(
                        "index-fresh",
                        "warn",
                        f"{pending} file(s) changed since the last build",
                        "expnote reindex",
                    )
                )
    except ResearchError as error:
        checks.append(Check("index", "fail", error.message, error.remediation))
        return False, None
    try:
        recorded = _meta_get(reader.conn, f"profile_sha256:{context.entry.id}")
        built_at = _meta_get(reader.conn, "built_at")
    finally:
        reader.conn.close()
    checks.append(_ok("index", f"index schema {INDEX_SCHEMA}, built {built_at}"))
    if recorded != context.profile_sha256:
        checks.append(
            Check(
                "profile-sha",
                "warn",
                "profile note changed since the index was built",
                "expnote reindex",
            )
        )
    else:
        checks.append(_ok("profile-sha", "profile sha256 matches the index"))
    if stats is not None:
        checks.append(
            _ok(
                "index-fresh",
                f"refreshed now: {stats.files_changed} file(s) changed, "
                f"{stats.elapsed_ms} ms",
            )
        )
    return True, stats


def _hygiene_checks(context: ProjectContext, now: datetime) -> list[Check]:
    profile = context.profile
    prefix = project_rel(context)
    conn = open_reader(context.entry.vault_id)
    try:
        files = conn.execute(
            "SELECT rel, kind, mtime_ns, is_conflict FROM files ORDER BY nrel"
        ).fetchall()
        pruned = json.loads(
            conn.execute("SELECT value FROM meta WHERE key='pruned_dirs'").fetchone()[0]
        )
        plan_rows = conn.execute(
            "SELECT f.rel, n.entity_id FROM notes n JOIN files f ON f.id = n.file_id "
            "WHERE n.type = 'plan' AND f.is_conflict = 0 "
            "AND substr(f.rel, 1, ?) = ? ORDER BY f.nrel",
            (len(prefix) + 1, prefix + "/"),
        ).fetchall()
        marker_hits: list[str] = []
        for marker in profile.forbidden_markers:
            marker_hits += [
                r[0]
                for r in conn.execute(
                    "SELECT f.rel FROM notes n JOIN files f ON f.id = n.file_id "
                    "WHERE instr(n.fm_raw || n.body, ?) > 0 "
                    "AND COALESCE(n.type, '') != 'project-profile' "
                    "AND substr(f.rel, 1, ?) = ? ORDER BY f.nrel",
                    (marker, len(prefix) + 1, prefix + "/"),
                )
            ]
    finally:
        conn.close()
    templates = profile.template_dirs
    marker_hits = [
        rel
        for rel in marker_hits
        if not templates & set(rel[len(prefix) + 1 :].split("/")[:-1])
    ]
    now_ns = int(now.timestamp() * 1e9)
    checks = []
    checks.append(
        _list_check(
            "sync-conflicts",
            [r["rel"] for r in files if r["is_conflict"]],
            "no sync-conflict files",
            "sync-conflict file(s) (excluded from the index)",
            "merge or delete them in Obsidian",
        )
    )
    checks.append(
        _list_check(
            "stale-temps",
            [
                r["rel"]
                for r in files
                if TEMP_NAME.match(r["rel"].rsplit("/", 1)[-1])
                and now_ns - r["mtime_ns"] > STALE_TEMP_SECONDS * 1e9
            ],
            "no stale expnote temp files",
            "stale expnote temp file(s) older than 1 hour",
            "delete them (a crashed write left them behind)",
        )
    )
    dot_dirs = [
        p
        for p in pruned
        if p.rsplit("/", 1)[-1].startswith(".")
        and p.rsplit("/", 1)[-1] not in profile.skip_dirs
    ]
    checks.append(
        _list_check(
            "dot-directories",
            dot_dirs,
            "no stray dot-directories",
            "dot-director(ies) in the vault (never indexed; may hold credentials)",
            "delete them or move them out of the vault",
        )
    )
    code = [
        r["rel"]
        for r in files
        if r["kind"] == "attachment"
        and r["rel"].rsplit(".", 1)[-1].lower() in CODE_EXTS
    ] + [p for p in pruned if p.rsplit("/", 1)[-1] in CODE_DIRS]
    checks.append(
        _list_check(
            "code-files",
            sorted(code),
            "no code files in the vault",
            "code file(s)/director(ies) in the vault (the vault is storage only)",
            "move code out of the vault, e.g. into the expnote package",
        )
    )
    checks.append(
        _list_check(
            "status-markers",
            marker_hits,
            "no generated status markers",
            "note(s) carry forbidden generated-block markers",
            "remove the generated block by hand (plan bodies are human-owned)",
        )
    )
    checks.append(_projection_check(context, files))
    checks.append(_generated_notes_check(profile, prefix, files))
    checks.append(_untyped_plans_check(profile, prefix, plan_rows))
    illegal = [
        r["rel"]
        for r in files
        if any(
            ILLEGAL_NAME_CHARS.search(part) or part.endswith((".", " "))
            for part in r["rel"].split("/")
        )
    ]
    checks.append(
        _list_check(
            "windows-filenames",
            illegal,
            "no Windows-illegal file names",
            "file name(s) Windows cannot hold",
            "rename them",
        )
    )
    window = context.entry.quiet_window_seconds
    recent = [
        r["rel"]
        for r in files
        if r["kind"] == "md"
        and r["rel"].startswith(prefix + "/")
        and 0 <= now_ns - r["mtime_ns"] < window * 1e9
    ]
    checks.append(
        _list_check(
            "recently-modified",
            recent,
            f"no note modified in the last {window} s",
            f"note(s) modified within the {window} s quiet window (writes would wait)",
            "wait and retry",
            status="info",
        )
    )
    return checks


def _generated_notes_check(profile, prefix: str, files) -> Check:
    if not profile.generated_notes:
        return Check(
            "generated-notes",
            "info",
            "the profile names no generated notes (forbidden.generated_notes)",
        )
    wanted = {nfc(f"{prefix}/{name}") for name in profile.generated_notes}
    return _list_check(
        "generated-notes",
        [r["rel"] for r in files if nfc(r["rel"]) in wanted],
        "no generated notes (e.g. a decision research view) in the vault",
        "generated note(s) in the vault (they go stale; the GUI replaces them)",
        "delete them; browse the same view in the GUI",
    )


def _untyped_plans_check(profile, prefix: str, plan_rows) -> Check:
    if not profile.plan_id_regex:
        return Check(
            "untyped-plans", "info", "the profile has no plan id regex (ids.plan_regex)"
        )
    shape = re.compile(profile.plan_id_regex)
    odd = [
        r["rel"]
        for r in plan_rows
        if not (r["entity_id"] and shape.fullmatch(r["entity_id"]))
        and not profile.template_dirs & set(r["rel"][len(prefix) + 1 :].split("/")[:-1])
    ]
    return _list_check(
        "untyped-plans",
        odd,
        "every note with type: plan has a plan id",
        "note(s) with type: plan but no plan id (left out of `plan list`)",
        "give them a plan id, or change their type",
    )


def _projection_check(context: ProjectContext, files) -> Check:
    found = []
    vault_root = context.resolved.vault_root.resolve()
    for name in linked_workspaces(context):
        try:
            workspace = resolve_workspace(workspace=name)
            config = read_config(
                workspace.workspace_dir, state_dir=workspace.workspace_dir
            )
        except Exception:  # noqa: BLE001 - unreadable workspace is reported elsewhere
            continue
        root = config.get("obsidian_root") or config.get("root")
        if not root or not str(root).startswith(str(vault_root)):
            continue
        base = PurePosixPath(root).relative_to(str(vault_root)).as_posix()
        for key in ("notes_dir", "docs_dir"):
            rel = config.get(key)
            if not rel:
                continue
            folder = (PurePosixPath(base) / rel).as_posix().lstrip("./")
            count = sum(1 for r in files if r["rel"].startswith(folder + "/"))
            if count:
                found.append(f"{folder} ({count} files, workspace {name})")
    return _list_check(
        "projection-folder",
        sorted(set(found)),
        "no expnote projection folder inside the vault",
        "expnote projection folder(s) inside the vault (generated files)",
        "turn the projection off for the bound workspace, then delete the folder",
    )
