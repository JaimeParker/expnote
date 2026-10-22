"""Helpers shared by the write-layer tests (aged vault, audit access)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from minivault import build_vault
from readkit import data, invoke, register, run

from expnote.research.writeback import WriteContext, audit_path
from expnote.workspace import data_home

__all__ = [
    "age_tree",
    "audit_records",
    "data",
    "edit_profile",
    "invoke",
    "make_context",
    "registered_vault",
    "run",
    "set_mtime",
]


def set_mtime(path: Path, seconds_ago: float) -> None:
    moment = time.time() - seconds_ago
    os.utime(path, (moment, moment))


def age_tree(root: Path, seconds: float = 3600) -> None:
    """Make every file look untouched for a while, so the quiet window is open."""
    for path in root.rglob("*"):
        if path.is_file():
            set_mtime(path, seconds)


def registered_vault(tmp_path: Path, quiet: int = 0) -> tuple[Path, Path]:
    root, proj = build_vault(tmp_path)
    register(root, proj, "--quiet-window-seconds", str(quiet))
    age_tree(root)
    return root, proj


def edit_profile(proj: Path, old: str, new: str) -> None:
    """Change the registered project's profile note (a hand edit in Obsidian)."""
    path = proj / "expnote \u9879\u76ee\u914d\u7f6e.md"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    set_mtime(path, 3600)


def make_context(root: Path, quiet: int = 0, project_id: str = "wb") -> WriteContext:
    # Guard: the conftest pins the state dirs to tmp; never write the real ones.
    assert "pytest" in str(data_home()), data_home()
    return WriteContext(
        project_id=project_id,
        vault_key="vault-wb",
        vault_root=root,
        quiet_window_seconds=quiet,
    )


def audit_records(project_id: str = "off2on") -> list[dict]:
    path = audit_path(WriteContext(project_id, "k", Path("."), 0))
    assert str(path).startswith(str(data_home()))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
