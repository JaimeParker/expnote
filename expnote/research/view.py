"""ProjectView: one refreshed, read-only look at a project (used by all reads)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from expnote.research.index import (
    IndexReader,
    ensure_index,
    project_rel,
    snapshot_from_index,
)
from expnote.research.model import Note
from expnote.research.registry import ProjectContext, open_project
from expnote.research.snapshot import VaultSnapshot


@dataclasses.dataclass
class ProjectView:
    context: ProjectContext
    snapshot: VaultSnapshot
    index: IndexReader
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    cache: dict = dataclasses.field(default_factory=dict)

    def __enter__(self) -> ProjectView:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.index.close()

    @property
    def conn(self):
        return self.index.conn

    @property
    def project_id(self) -> str:
        return self.context.entry.id

    @property
    def profile(self):
        return self.context.profile

    @property
    def prefix(self) -> str:
        """Vault-relative project directory."""
        return project_rel(self.context)

    def vault_rel(self, note_or_rel: Note | str) -> str:
        rel = note_or_rel.rel if isinstance(note_or_rel, Note) else note_or_rel
        return f"{self.prefix}/{rel}"

    def abs_path(self, note_or_rel: Note | str) -> Path:
        rel = note_or_rel.rel if isinstance(note_or_rel, Note) else note_or_rel
        return self.context.resolved.project_dir / rel

    def meta(self, **extra) -> dict:
        return {
            "project": self.project_id,
            "index": self.index.stats.meta(),
            "pathNote": self.context.resolved.path_note,
            **extra,
        }

    def checks(self, plan_id: str | None = None):
        """`run_checks` result, computed once per view and scope."""
        from expnote.research.checks import run_checks

        key = ("checks", plan_id)
        if key not in self.cache:
            self.cache[key] = run_checks(self.snapshot, plan_id)
        return self.cache[key]

    def today(self) -> str:
        return self.clock().date().isoformat()

    def file_sha256(self, rel: str) -> str | None:
        """sha256 of a project note's bytes as recorded by the index."""
        row = self.conn.execute(
            "SELECT sha256 FROM files WHERE rel = ?", (self.vault_rel(rel),)
        ).fetchone()
        return row[0] if row else None


def open_view(
    project_id: str | None = None,
    *,
    context: ProjectContext | None = None,
    refresh: bool = True,
    full: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ProjectView:
    """Refresh the index (unless `refresh` is False) and load the snapshot from it."""
    context = context or open_project(project_id)
    index = ensure_index(context, refresh=refresh, full=full)
    try:
        snapshot = snapshot_from_index(context, index.conn)
    except BaseException:
        index.close()
        raise
    view = ProjectView(context, snapshot, index)
    if clock is not None:
        view.clock = clock
    return view
