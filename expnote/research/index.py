"""Vault index: an SQLite mirror of the vault walk, notes, links and search text.

Disposable and rebuildable (`reindex --full`). It lives under the expnote data
home, never in the vault. One writer at a time (flock); readers open it with
`mode=ro`. `snapshot_from_index` rebuilds a `VaultSnapshot` from it without
reading any note from disk, so `checks.py` is unchanged.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from expnote.research.errors import ResearchError
from expnote.research.extract import (
    aliases_of,
    extract_decision_refs,
    extract_links,
    first_h1,
    nodes_from_quote_map,
    parse_todo_lines,
    quote_map,
)
from expnote.research.model import Note, parse_note
from expnote.research.profile import Profile
from expnote.research.registry import ProjectContext, load_registry
from expnote.research.runs import ids_in_workspaces
from expnote.research.snapshot import (
    VaultSnapshot,
    _alias_index,
    _read_text,
    _registry,
    build_decisions_and_deviations,
)
from expnote.research.textkit import (
    fold,
    glob_to_regex,
    headings,
    is_attachment_name,
    nfc,
    sha256_text,
)
from expnote.workspace import data_home

INDEX_SCHEMA = 1
ENGINE_VERSION = "research-index-1"
IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"})
DEFAULT_CONFLICT_GLOBS = ("*.sync-conflict-*", "*conflicted copy*", "* (conflict *)*")
LOCK_TIMEOUT_SECONDS = 15.0
STAT_WORKERS = 16

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files(
  id INTEGER PRIMARY KEY,
  rel TEXT NOT NULL UNIQUE,
  nrel TEXT NOT NULL,
  kind TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  sha256 TEXT,
  project_id TEXT,
  is_conflict INTEGER NOT NULL DEFAULT 0,
  ignored_reason TEXT
);
CREATE INDEX files_nrel ON files(nrel);
CREATE TABLE notes(
  file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
  stem TEXT NOT NULL,
  cstem TEXT NOT NULL,
  type TEXT,
  entity_id TEXT,
  schema TEXT,
  status TEXT,
  title TEXT NOT NULL,
  plan_link TEXT,
  plan_id TEXT,
  fm_json TEXT NOT NULL,
  fm_quote TEXT NOT NULL,
  fm_error TEXT,
  fm_error_kind TEXT,
  has_fm INTEGER NOT NULL,
  fm_raw TEXT NOT NULL,
  body TEXT NOT NULL,
  body_sha256 TEXT NOT NULL
);
CREATE INDEX notes_cstem ON notes(cstem);
CREATE INDEX notes_type ON notes(type);
CREATE TABLE aliases(
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  alias_cstem TEXT NOT NULL
);
CREATE INDEX aliases_cstem ON aliases(alias_cstem);
CREATE TABLE headings(
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  level INTEGER NOT NULL,
  text TEXT NOT NULL,
  line INTEGER NOT NULL
);
CREATE INDEX headings_file ON headings(file_id);
CREATE TABLE links(
  id INTEGER PRIMARY KEY,
  src_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  raw TEXT NOT NULL,
  target TEXT NOT NULL,
  target_stem TEXT NOT NULL,
  resolved_file_id INTEGER,
  resolution TEXT NOT NULL,
  line INTEGER NOT NULL,
  in_frontmatter INTEGER NOT NULL
);
CREATE INDEX links_src ON links(src_file_id);
CREATE INDEX links_dst ON links(resolved_file_id);
CREATE INDEX links_stem ON links(target_stem);
CREATE TABLE decision_refs(
  src_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  form TEXT NOT NULL,
  line INTEGER NOT NULL
);
CREATE INDEX decision_refs_number ON decision_refs(number);
CREATE INDEX decision_refs_src ON decision_refs(src_file_id);
CREATE TABLE todos(
  src_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  todo_id TEXT NOT NULL,
  synthetic INTEGER NOT NULL,
  state TEXT NOT NULL,
  line INTEGER NOT NULL,
  line_sha TEXT NOT NULL,
  text TEXT NOT NULL,
  fields_json TEXT NOT NULL
);
CREATE INDEX todos_src ON todos(src_file_id);
CREATE VIRTUAL TABLE fts_notes USING fts5(
  stem, title, headings, body, tokenize='trigram'
);
"""


# ---- locations and errors -----------------------------------------------------


def index_dir(vault_id: str) -> Path:
    return data_home() / "research" / "index" / vault_id.replace("/", "_")


def index_path(vault_id: str) -> Path:
    return index_dir(vault_id) / "index.sqlite"


def trigram_supported() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='trigram')")
        conn.close()
        return True
    except sqlite3.Error:
        return False


def _locked_error(path: Path) -> ResearchError:
    return ResearchError(
        "ENV_INDEX_LOCKED",
        f"another process is writing the index ({path})",
        exit_code=6,
        retryable=True,
        remediation="retry in a few seconds",
    )


@contextlib.contextmanager
def _writer_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "index.lock", "a+")  # noqa: SIM115
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise _locked_error(directory) from None
                time.sleep(0.1)
        yield
    finally:
        handle.close()


# ---- walk ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WalkEntry:
    rel: str  # as stored on disk, POSIX
    nrel: str  # NFC
    kind: str  # md | attachment
    size: int
    mtime_ns: int


def walk_vault(vault_root: Path, profile: Profile) -> tuple[list[WalkEntry], list[str]]:
    """Every file outside pruned directories; also the pruned directories.

    Mirrors `snapshot._walk_names`: directories named in `skip_dirs` or starting
    with a dot are pruned, dot-files are kept, `.md` matches case-insensitively and
    symlinked directories are neither listed nor entered (like `os.walk`). The
    stats run on a thread pool because a serial `stat` costs about a millisecond
    per file on a drvfs mount.
    """
    pending: list[tuple[str, os.DirEntry]] = []
    pruned: list[str] = []
    stack = [("", str(vault_root))]
    while stack:
        rel_dir, path = stack.pop()
        with os.scandir(path) as listing:
            for entry in listing:
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                if entry.is_dir():
                    if entry.name in profile.skip_dirs or entry.name.startswith("."):
                        pruned.append(rel)
                    elif not entry.is_symlink():
                        stack.append((rel, entry.path))
                else:
                    pending.append((rel, entry))

    def stat(item: tuple[str, os.DirEntry]) -> WalkEntry | None:
        rel, entry = item
        try:
            info = entry.stat()
        except OSError:
            return None  # broken symlink or vanished file
        kind = "md" if entry.name.lower().endswith(".md") else "attachment"
        return WalkEntry(rel, nfc(rel), kind, info.st_size, info.st_mtime_ns)

    with ThreadPoolExecutor(max_workers=STAT_WORKERS) as pool:
        entries = [e for e in pool.map(stat, pending) if e is not None]
    return entries, sorted(pruned)


class ConflictMatcher:
    """Sync-conflict file names: core patterns plus the profile's `conflict_globs`."""

    def __init__(self, profile: Profile):
        patterns = [*DEFAULT_CONFLICT_GLOBS, *profile.conflict_globs]
        self._name = [glob_to_regex(g) for g in patterns if "/" not in g]
        self._path = [glob_to_regex(g) for g in patterns if "/" in g]

    def matches(self, rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return any(rx.match(name) for rx in self._name) or any(
            rx.match(rel) for rx in self._path
        )


def project_prefixes(context: ProjectContext) -> list[tuple[str, str]]:
    """(vault-relative project dir, project id) for projects sharing this vault."""
    root = str(context.resolved.vault_root).rstrip("/")
    found = {}
    with contextlib.suppress(ResearchError):
        for entry in load_registry().projects.values():
            if entry.vault_id != context.entry.vault_id:
                continue
            base = entry.vault_root.replace("\\", "/").rstrip("/")
            path = entry.project_dir.replace("\\", "/").rstrip("/")
            if path.startswith(base + "/"):
                found[path[len(base) + 1 :]] = entry.id
    found[project_rel(context)] = context.entry.id
    del root
    return sorted(found.items(), key=lambda item: -len(item[0]))


def project_rel(context: ProjectContext) -> str:
    """Vault-relative project directory (POSIX)."""
    resolved = context.resolved
    try:
        return resolved.project_dir.relative_to(resolved.vault_root).as_posix()
    except ValueError:
        return resolved.project_dir.as_posix()


def _project_of(rel: str, prefixes: list[tuple[str, str]]) -> str | None:
    for prefix, project_id in prefixes:
        if rel.startswith(prefix + "/"):
            return project_id
    return None


# ---- link resolution -------------------------------------------------------------


class Resolver:
    """Vault-wide name resolution, same rules as `VaultSnapshot.resolve`."""

    def __init__(self, files: list[tuple[int, str, str]], aliases, exts):
        self.exts = exts
        self.md: dict[str, list[int]] = {}
        self.attach: dict[str, list[int]] = {}
        self.noext: dict[str, list[int]] = {}
        self.att_rel: dict[str, list[int]] = {}
        self.alias: dict[str, list[int]] = {}
        for fid, nrel, kind in sorted(files, key=lambda f: f[1]):
            name = nrel.rsplit("/", 1)[-1]
            if kind == "md":
                self.md.setdefault(name[:-3].casefold(), []).append(fid)
                self.noext.setdefault(nrel[:-3].casefold(), []).append(fid)
            else:
                self.attach.setdefault(name.casefold(), []).append(fid)
                self.att_rel.setdefault(nrel.casefold(), []).append(fid)
        for fid, alias_cstem in aliases:
            self.alias.setdefault(alias_cstem, []).append(fid)

    def resolve(self, target: str, src_id: int) -> tuple[int | None, str]:
        if not target:
            return src_id, "file"
        key = fold(target)
        if "/" in key:
            for table in (self.noext, self.att_rel):
                for path, ids in table.items():
                    if path == key or path.endswith("/" + key):
                        return ids[0], "file"
            return None, "none"
        if is_attachment_name(key, self.exts):
            ids = self.attach.get(key)
            return (ids[0], "file") if ids else (None, "none")
        ids = self.md.get(key)
        if ids:
            return ids[0], "file"
        ids = self.alias.get(key)
        if ids:
            return ids[0], "alias"
        return None, "none"


# ---- refresh -----------------------------------------------------------------------


@dataclasses.dataclass
class RefreshStats:
    files: int = 0
    notes: int = 0
    added: int = 0
    changed: int = 0
    removed: int = 0
    reparsed: int = 0
    rebuilt: bool = False
    links_resolved: int = 0
    links_unresolved: int = 0
    images: int = 0
    conflicts: int = 0
    elapsed_ms: int = 0
    refreshed: bool = True
    warnings: list[dict] = dataclasses.field(default_factory=list)

    @property
    def files_changed(self) -> int:
        return self.added + self.changed + self.removed

    def meta(self) -> dict:
        return {"refreshed": self.refreshed, "filesChanged": self.files_changed}


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(str(path) + suffix)


def _create_database(path: Path, vault_id: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL)
    _meta_set(conn, "index_schema", str(INDEX_SCHEMA))
    _meta_set(conn, "vault_id", vault_id)
    _meta_set(conn, "engine_version", ENGINE_VERSION)
    conn.commit()
    return conn


def _open_writer(
    path: Path, vault_id: str, full: bool
) -> tuple[sqlite3.Connection, bool]:
    if full:
        _remove_database(path)
    elif path.exists():
        try:
            conn = sqlite3.connect(path)
            if _meta_get(conn, "index_schema") == str(INDEX_SCHEMA):
                conn.execute("PRAGMA foreign_keys=ON")
                return conn, False
            conn.close()
        except sqlite3.DatabaseError:
            pass
        _remove_database(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _create_database(path, vault_id)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn, True


@dataclasses.dataclass
class _Existing:
    id: int
    size: int
    mtime_ns: int
    sha256: str | None
    kind: str
    project_id: str | None
    is_conflict: int


def _drop_note_rows(conn: sqlite3.Connection, file_id: int) -> None:
    for table in ("notes", "aliases", "headings", "links", "decision_refs", "todos"):
        column = (
            "file_id" if table in ("notes", "aliases", "headings") else "src_file_id"
        )
        conn.execute(f"DELETE FROM {table} WHERE {column}=?", (file_id,))
    conn.execute("DELETE FROM fts_notes WHERE rowid=?", (file_id,))


def refresh_index(
    context: ProjectContext, *, full: bool = False, workspaces: list[str] | None = None
) -> RefreshStats:
    """Bring the index up to date with the vault (single writer, atomic)."""
    started = time.perf_counter()
    vault_id = context.entry.vault_id
    directory = index_dir(vault_id)
    with _writer_lock(directory):
        conn, rebuilt = _open_writer(directory / "index.sqlite", vault_id, full)
        try:
            stats = _refresh(conn, context, rebuilt, workspaces)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return stats


def _refresh(
    conn: sqlite3.Connection,
    context: ProjectContext,
    rebuilt: bool,
    workspaces: list[str] | None,
) -> RefreshStats:
    profile = context.profile
    vault_root = context.resolved.vault_root
    stats = RefreshStats(rebuilt=rebuilt)
    conflicts = ConflictMatcher(profile)
    prefixes = project_prefixes(context)
    entries, pruned = walk_vault(vault_root, profile)
    existing = {
        row[0]: _Existing(*row[1:])
        for row in conn.execute(
            "SELECT rel, id, size, mtime_ns, sha256, kind, project_id, is_conflict "
            "FROM files"
        )
    }
    profile_key = f"profile_sha256:{context.entry.id}"
    profile_changed = _meta_get(conn, profile_key) != context.profile_sha256
    walked = {e.rel for e in entries}
    name_set_changed = False
    parsed: dict[int, Note] = {}
    texts: dict[int, str] = {}
    derive_only: set[int] = set()

    for rel, old in existing.items():
        if rel in walked:
            continue
        _drop_note_rows(conn, old.id)
        conn.execute("DELETE FROM files WHERE id=?", (old.id,))
        stats.removed += 1
        name_set_changed = name_set_changed or not old.is_conflict

    contents = _read_many(
        vault_root,
        [
            e.rel
            for e in entries
            if e.kind == "md"
            and not conflicts.matches(e.rel)
            and _may_need_parse(existing.get(e.rel), e)
        ],
    )
    for entry in sorted(entries, key=lambda e: e.rel):
        is_conflict = int(conflicts.matches(entry.rel))
        project_id = _project_of(entry.nrel, prefixes)
        reason = "conflict" if is_conflict else None
        old = existing.get(entry.rel)
        parse = False
        if old is None:
            cursor = conn.execute(
                "INSERT INTO files(rel, nrel, kind, size, mtime_ns, project_id, "
                "is_conflict, ignored_reason) VALUES(?,?,?,?,?,?,?,?)",
                (entry.rel, entry.nrel, entry.kind, entry.size, entry.mtime_ns,
                 project_id, is_conflict, reason),
            )  # fmt: skip
            file_id = cursor.lastrowid
            stats.added += 1
            name_set_changed = name_set_changed or not is_conflict
            parse = entry.kind == "md" and not is_conflict
        else:
            file_id = old.id
            flipped = bool(old.is_conflict) != bool(is_conflict)
            stat_changed = (old.size, old.mtime_ns) != (entry.size, entry.mtime_ns)
            if not (stat_changed or flipped or old.project_id != project_id):
                if profile_changed and entry.kind == "md" and not is_conflict:
                    derive_only.add(file_id)
                continue
            conn.execute(
                "UPDATE files SET size=?, mtime_ns=?, project_id=?, is_conflict=?, "
                "ignored_reason=? WHERE id=?",
                (entry.size, entry.mtime_ns, project_id, is_conflict, reason, file_id),
            )
            if flipped:
                name_set_changed = True
                _drop_note_rows(conn, file_id)
            if entry.kind == "md" and not is_conflict:
                if flipped or old.sha256 is None:
                    parse = True
                elif stat_changed:
                    raw = contents.get(entry.rel)
                    if raw is None:
                        continue
                    if (
                        hashlib.sha256(raw).hexdigest() == old.sha256
                    ):  # touched, not edited
                        if profile_changed:
                            derive_only.add(file_id)
                        continue
                    parse = True
            else:
                stats.changed += 1  # attachment edited, or a conflict-file flip
        if parse:
            if old is not None:
                stats.changed += 1
            if _store_parsed(
                conn, file_id, entry, parsed, texts, contents.get(entry.rel)
            ):
                name_set_changed = True
            stats.reparsed += 1

    _write_parsed_notes(conn, parsed)
    resolver = _build_resolver(conn, profile)
    link_rows = 0
    for file_id, note in parsed.items():
        link_rows += _write_links(conn, file_id, note, texts[file_id], resolver)
    if name_set_changed:
        _reresolve_links(conn, resolver, skip=set(parsed))
    run_warnings = _resolve_run_links(conn, context, workspaces)
    stats.warnings += run_warnings
    conn.execute(
        "UPDATE notes SET plan_id = CASE WHEN type='plan' THEN entity_id ELSE "
        "(SELECT p.entity_id FROM notes p WHERE p.type='plan' "
        "AND p.cstem=notes.plan_link "
        "LIMIT 1) END"
    )
    derive_ids = set(parsed) | derive_only
    if profile_changed:
        derive_ids |= {r[0] for r in conn.execute("SELECT file_id FROM notes")}
    _write_derived(conn, context, derive_ids, parsed)
    _meta_set(conn, profile_key, context.profile_sha256)
    _meta_set(conn, "built_at", datetime.now(UTC).replace(microsecond=0).isoformat())
    _meta_set(conn, "pruned_dirs", json.dumps(pruned, ensure_ascii=False))
    stats.files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    stats.notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    stats.conflicts = conn.execute(
        "SELECT COUNT(*) FROM files WHERE is_conflict=1"
    ).fetchone()[0]
    stats.links_resolved = conn.execute(
        "SELECT COUNT(*) FROM links WHERE resolution != 'none'"
    ).fetchone()[0]
    stats.links_unresolved = conn.execute(
        "SELECT COUNT(*) FROM links WHERE resolution = 'none'"
    ).fetchone()[0]
    exts = tuple(IMAGE_EXTS)
    stats.images = sum(
        1
        for (rel,) in conn.execute(
            "SELECT rel FROM files WHERE kind='attachment' AND is_conflict=0"
        )
        if rel.rsplit(".", 1)[-1].lower() in exts
    )
    del link_rows
    return stats


def _may_need_parse(old: _Existing | None, entry: WalkEntry) -> bool:
    return (
        old is None
        or old.is_conflict
        or (old.size, old.mtime_ns)
        != (
            entry.size,
            entry.mtime_ns,
        )
    )


def _read_many(vault_root: Path, rels: list[str]) -> dict[str, bytes]:
    """File bytes by relative path, read on a thread pool (drvfs reads are slow)."""

    def read(rel: str) -> tuple[str, bytes | None]:
        try:
            return rel, (vault_root / rel).read_bytes()
        except OSError:
            return rel, None

    with ThreadPoolExecutor(max_workers=STAT_WORKERS) as pool:
        return {rel: raw for rel, raw in pool.map(read, rels) if raw is not None}


def _store_parsed(
    conn: sqlite3.Connection,
    file_id: int,
    entry: WalkEntry,
    parsed: dict[int, Note],
    texts: dict[int, str],
    raw: bytes | None,
) -> bool:
    """Parse one file into memory and drop its old rows; True if its aliases changed."""
    raw = raw or b""
    note = parse_note(Path(entry.rel), entry.rel, raw.decode("utf-8", errors="replace"))
    parsed[file_id] = note
    texts[file_id] = note.text
    old_aliases = {
        row[0]
        for row in conn.execute(
            "SELECT alias_cstem FROM aliases WHERE file_id=?", (file_id,)
        )
    }
    conn.execute(
        "UPDATE files SET sha256=? WHERE id=?",
        (hashlib.sha256(raw).hexdigest(), file_id),
    )
    _drop_note_rows(conn, file_id)
    return old_aliases != {fold(a) for a in aliases_of(note)}


def _write_parsed_notes(conn: sqlite3.Connection, parsed: dict[int, Note]) -> None:
    for file_id, note in parsed.items():
        text, body = note.text, note.body
        fm_raw = text[: len(text) - len(body)]
        offset = fm_raw.count("\n")
        heads = headings(body)
        entity_id = note.fm.get("id") if isinstance(note.fm.get("id"), str) else None
        title = note.fm.get("title")
        title = title if isinstance(title, str) and title else None
        title = title or first_h1(body) or note.stem
        status = note.fm.get("status")
        plan_link = note.link_field("plan")
        conn.execute(
            "INSERT INTO notes(file_id, stem, cstem, type, entity_id, schema, status, "
            "title, plan_link, fm_json, fm_quote, fm_error, fm_error_kind, has_fm, "
            "fm_raw, body, body_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (file_id, note.stem, note.cstem, note.type, entity_id, note.schema,
             status if isinstance(status, str) else None, title, plan_link,
             json.dumps(note.fm, ensure_ascii=False),
             json.dumps(quote_map(note.fm_nodes)), note.fm_error,
             note.fm_error_kind, int(note.has_fm), fm_raw, body, sha256_text(body)),
        )  # fmt: skip
        conn.executemany(
            "INSERT INTO aliases(file_id, alias_cstem) VALUES(?,?)",
            [(file_id, fold(a)) for a in aliases_of(note)],
        )
        conn.executemany(
            "INSERT INTO headings(file_id, level, text, line) VALUES(?,?,?,?)",
            [(file_id, level, text_, offset + idx + 1) for idx, level, text_ in heads],
        )
        conn.execute(
            "INSERT INTO fts_notes(rowid, stem, title, headings, body) "
            "VALUES(?,?,?,?,?)",
            (file_id, note.stem, title, "\n".join(h[2] for h in heads), body),
        )


def _build_resolver(conn: sqlite3.Connection, profile: Profile) -> Resolver:
    files = conn.execute(
        "SELECT id, nrel, kind FROM files WHERE is_conflict=0"
    ).fetchall()
    aliases = conn.execute(
        "SELECT a.file_id, a.alias_cstem FROM aliases a JOIN files f ON f.id=a.file_id "
        "WHERE f.is_conflict=0 ORDER BY f.nrel"
    ).fetchall()
    return Resolver(files, aliases, profile.attachment_exts)


def _write_links(
    conn: sqlite3.Connection, file_id: int, note: Note, text: str, resolver: Resolver
) -> int:
    fm_len = len(text) - len(note.body)
    rows = []
    for link in extract_links(text, fm_len):
        resolved, resolution = resolver.resolve(link.target, file_id)
        rows.append(
            (file_id, link.kind, link.raw, link.target, link.target_stem, resolved,
             resolution, link.line, int(link.in_frontmatter))
        )  # fmt: skip
    conn.executemany(
        "INSERT INTO links(src_file_id, kind, raw, target, target_stem, "
        "resolved_file_id, resolution, line, in_frontmatter) VALUES(?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _reresolve_links(
    conn: sqlite3.Connection, resolver: Resolver, skip: set[int]
) -> None:
    updates = []
    for link_id, src, target, old_id, old_resolution in conn.execute(
        "SELECT id, src_file_id, target, resolved_file_id, resolution FROM links"
    ).fetchall():
        if src in skip:
            continue
        resolved, resolution = resolver.resolve(target, src)
        if resolution == "none" and old_resolution in ("run", "doc"):
            continue  # settled by the run pass
        if (resolved, resolution) != (old_id, old_resolution):
            updates.append((resolved, resolution, link_id))
    conn.executemany(
        "UPDATE links SET resolved_file_id=?, resolution=? WHERE id=?", updates
    )


def _resolve_run_links(
    conn: sqlite3.Connection, context: ProjectContext, workspaces: list[str] | None
) -> list[dict]:
    """Mark `[[run_id]]` / `[[doc_id]]` links that only the workspace DB knows."""
    regex = context.profile.run_id_regex
    names = workspaces if workspaces is not None else linked_workspaces(context)
    if not regex or not names:
        return []
    pattern = re.compile(regex)
    candidates = {
        stem
        for (stem,) in conn.execute(
            "SELECT DISTINCT target_stem FROM links "
            "WHERE resolution IN ('none', 'run', 'doc')"
        )
        if pattern.fullmatch(stem)
    }
    if not candidates:
        return []
    found, problems = ids_in_workspaces(names, candidates)
    warnings = [
        {"code": "RUNS_UNAVAILABLE", "message": f"workspace {w}: {why}"}
        for w, why in problems.items()
    ]
    if len(problems) == len(names):
        return warnings  # nothing readable: keep the previous resolution
    for stem in candidates:
        resolution = found.get(stem, "none")
        conn.execute(
            "UPDATE links SET resolution=? WHERE target_stem=? "
            "AND resolved_file_id IS NULL "
            "AND resolution IN ('none', 'run', 'doc')",
            (resolution, stem),
        )
    return warnings


def linked_workspaces(context: ProjectContext) -> list[str]:
    names = list(context.entry.workspaces)
    with contextlib.suppress(ResearchError):
        for entry in load_registry().projects.values():
            if entry.vault_id == context.entry.vault_id:
                names += [w for w in entry.workspaces if w not in names]
    return names


def _write_derived(
    conn: sqlite3.Connection,
    context: ProjectContext,
    file_ids: set[int],
    parsed: dict[int, Note],
) -> None:
    profile = context.profile
    todo_rel = None
    if profile.todo_file:
        todo_rel = nfc(
            (PurePosixPath(project_rel(context)) / profile.todo_file).as_posix()
        )
    for file_id in file_ids:
        row = conn.execute(
            "SELECT f.nrel, n.fm_json, n.fm_raw, n.body FROM notes n "
            "JOIN files f ON f.id = n.file_id WHERE n.file_id=?",
            (file_id,),
        ).fetchone()
        if row is None:
            continue
        nrel, fm_json, fm_raw, body = row
        note = parsed.get(file_id)
        if note is None:
            note = Note(
                Path(nrel),
                nrel,
                fm_raw + body,
                body,
                json.loads(fm_json),
                {},
                None,
                None,
                True,
            )
        conn.execute("DELETE FROM decision_refs WHERE src_file_id=?", (file_id,))
        conn.executemany(
            "INSERT INTO decision_refs(src_file_id, number, form, line) "
            "VALUES(?,?,?,?)",
            [
                (file_id, n, form, line)
                for n, form, line in extract_decision_refs(note, fm_raw + body, profile)
            ],
        )
        if todo_rel is not None and nrel == todo_rel:
            conn.execute("DELETE FROM todos WHERE src_file_id=?", (file_id,))
            conn.executemany(
                "INSERT INTO todos(src_file_id, todo_id, synthetic, state, line, "
                "line_sha, text, fields_json) VALUES(?,?,?,?,?,?,?,?)",
                [
                    (file_id, t.todo_id, int(t.synthetic), t.state, t.line, t.line_sha,
                     t.text, json.dumps(t.fields, ensure_ascii=False))
                    for t in parse_todo_lines(fm_raw + body)
                ],
            )  # fmt: skip


# ---- readers -----------------------------------------------------------------------


def open_reader(vault_id: str) -> sqlite3.Connection:
    path = index_path(vault_id)
    if not path.exists():
        raise ResearchError(
            "INDEX_MISSING",
            "the research index has not been built",
            exit_code=6,
            remediation="expnote reindex",
        )
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        version = _meta_get(conn, "index_schema")
    except sqlite3.DatabaseError:
        version = None
    if version != str(INDEX_SCHEMA):
        conn.close()
        raise ResearchError(
            "INDEX_SCHEMA",
            "the research index has an incompatible schema",
            exit_code=6,
            remediation="expnote reindex --full",
        )
    return conn


def read_index_stats(vault_id: str) -> dict:
    """Counts and build time of the index as it is on disk (no refresh, no writes)."""
    path = str(index_path(vault_id))
    try:
        conn = open_reader(vault_id)
    except ResearchError as error:
        return {"built": False, "path": path, "problem": error.code}
    try:

        def count(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        return {
            "built": True,
            "path": path,
            "builtAt": _meta_get(conn, "built_at"),
            "files": count("SELECT COUNT(*) FROM files"),
            "notes": count("SELECT COUNT(*) FROM notes"),
            "linksResolved": count(
                "SELECT COUNT(*) FROM links WHERE resolution != 'none'"
            ),
            "linksUnresolved": count(
                "SELECT COUNT(*) FROM links WHERE resolution = 'none'"
            ),
            "conflictFiles": count("SELECT COUNT(*) FROM files WHERE is_conflict=1"),
        }
    finally:
        conn.close()


@dataclasses.dataclass
class IndexReader:
    conn: sqlite3.Connection
    stats: RefreshStats

    def close(self) -> None:
        self.conn.close()


def ensure_index(
    context: ProjectContext, *, refresh: bool = True, full: bool = False
) -> IndexReader:
    """Refresh (unless told not to) and open a read-only connection."""
    if refresh or full:
        stats = refresh_index(context, full=full)
    else:
        stats = RefreshStats(refreshed=False)
    return IndexReader(open_reader(context.entry.vault_id), stats)


# ---- snapshot from the index --------------------------------------------------------


def snapshot_from_index(
    context: ProjectContext, conn: sqlite3.Connection
) -> VaultSnapshot:
    """Same facts as `scan_vault`, read from the index (conflict files excluded)."""
    resolved, profile = context.resolved, context.profile
    vault_root, project_dir = resolved.vault_root, resolved.project_dir
    prefix = project_rel(context)
    images_dir = resolved.images_dir
    try:
        images_rel = images_dir.relative_to(vault_root).as_posix()
    except ValueError:
        images_rel = images_dir.as_posix()

    md_index: dict[str, list[str]] = {}
    attach_index: dict[str, list[str]] = {}
    rel_noext: set[str] = set()
    rel_attach: set[str] = set()
    image_names: list[str] = []
    images_prefix = images_rel + "/"
    for rel, nrel, kind in conn.execute(
        "SELECT rel, nrel, kind FROM files WHERE is_conflict=0 ORDER BY id"
    ):
        name = nrel.rsplit("/", 1)[-1]
        if kind == "md":
            md_index.setdefault(name[:-3].casefold(), []).append(nrel)
            rel_noext.add(nrel[:-3].casefold())
        else:
            attach_index.setdefault(name.casefold(), []).append(nrel)
            rel_attach.add(nrel.casefold())
        if rel.startswith(images_prefix) and "/" not in rel[len(images_prefix) :]:
            image_names.append(rel[len(images_prefix) :])

    notes: list[Note] = []
    rows = conn.execute(
        "SELECT f.rel, n.fm_json, n.fm_quote, n.fm_error, n.fm_error_kind, n.has_fm, "
        "n.fm_raw, n.body FROM notes n JOIN files f ON f.id = n.file_id "
        "WHERE substr(f.rel, 1, ?) = ?",
        (len(prefix) + 1, prefix + "/"),
    ).fetchall()
    start = len(prefix) + 1
    rows = [r for r in rows if r["rel"].endswith(".md")]
    rows.sort(key=lambda r: PurePosixPath(r["rel"][start:]).parts)
    for row in rows:
        rel = row["rel"][start:]
        if any(
            p in profile.skip_dirs or p.startswith(".") for p in rel.split("/")[:-1]
        ):
            continue
        notes.append(
            Note(
                path=project_dir / rel,
                rel=rel,
                text=row["fm_raw"] + row["body"],
                body=row["body"],
                fm=json.loads(row["fm_json"]),
                fm_nodes=nodes_from_quote_map(json.loads(row["fm_quote"])),
                fm_error=row["fm_error"],
                fm_error_kind=row["fm_error_kind"],
                has_fm=bool(row["has_fm"]),
            )
        )
    registry = _registry(notes, profile)
    by_rel = {n.rel: n for n in notes}
    table_name = profile.legacy_decision_table
    table_text = None
    if table_name:
        note = by_rel.get(table_name)
        table_text = note.text if note else _read_text(project_dir / table_name)
    deviation_note = by_rel.get(profile.deviation_register)
    deviation_text = (
        deviation_note.text
        if deviation_note
        else _read_text(project_dir / profile.deviation_register)
    )
    decisions, problems, deviations = build_decisions_and_deviations(
        profile, context.entry.id, table_text, deviation_text
    )
    return VaultSnapshot(
        profile=profile,
        vault_root=vault_root,
        project_dir=project_dir,
        images_rel=images_rel,
        notes=notes,
        registry=registry,
        md_index=md_index,
        attach_index=attach_index,
        rel_noext=rel_noext,
        rel_attach=rel_attach,
        alias_index=_alias_index(notes),
        images=sorted(image_names) if images_dir.is_dir() else None,
        decisions=decisions,
        decision_problems=problems,
        deviations=deviations,
    )
