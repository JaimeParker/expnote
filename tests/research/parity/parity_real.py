"""Context for the frozen real-vault copy (profile fixture lives in its project dir)."""

from __future__ import annotations

from pathlib import Path

from expnote.research.profile import read_profile_note
from expnote.research.registry import ProjectContext, ProjectEntry, ResolvedProject

PROJECT_REL = "10 Projects/New Off2On RL"
IMAGES_REL = "40 Resources/42 Assets/Images/New Off2On RL"
PROFILE_NAME = "expnote 项目配置.md"


def real_context(vault: Path) -> ProjectContext:
    project = vault / PROJECT_REL
    profile_path = project / PROFILE_NAME
    note = read_profile_note(profile_path)
    assert note.profile is not None, note.problems
    images = vault / IMAGES_REL
    entry = ProjectEntry(
        "off2on",
        "New Off2On RL",
        "parity",
        str(vault),
        str(project),
        str(images),
        str(profile_path),
    )
    resolved = ResolvedProject(entry, vault, project, images, profile_path)
    return ProjectContext(resolved, note.profile, note.sha256)
