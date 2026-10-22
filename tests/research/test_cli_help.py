"""`--help` is how agents discover commands: it must name every command."""

from __future__ import annotations

import pytest
import typer.main
from readkit import invoke

from expnote.cli import app

ROOT = typer.main.get_command(app)
RESEARCH_GROUPS = (
    "plan", "decision", "exec", "ev", "res", "deviation", "todo", "new", "project",
)  # fmt: skip


def test_top_level_help_points_research_agents_to_the_guide():
    out = " ".join(invoke("--help").output.replace("│", " ").split())
    assert "guide research" in out and "expnote next" in out
    assert "Research layer (vault)" in out and "Run registry" in out
    assert "refused for registered research vaults" in out.replace("\n", " ") or (
        "refused" in out
    )


def test_every_top_level_command_is_still_listed():
    out = invoke("--help").output
    for name in ROOT.commands:
        assert name in out, name


@pytest.mark.parametrize("group", RESEARCH_GROUPS)
def test_group_help_lists_every_subcommand(group):
    command = ROOT.commands[group]
    out = invoke(group, "--help").output
    assert command.commands, group
    for name in command.commands:
        assert name in out, f"{group} {name}"
    description = (command.help or "").replace("\n", " ")
    for name in command.commands:
        assert name in description, f"the {group} help line omits {name}"


def test_validate_has_help_text():
    assert "run-registry workspace" in invoke("validate", "--help").output.replace(
        "\n", " "
    ).replace("│", "")
