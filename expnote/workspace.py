from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from expnote.db import read_config


@dataclass(frozen=True)
class WorkspaceContext:
    root: Path
    workspace_dir: Path
    obsidian_root: Path | None
    name: str | None = None


def config_home() -> Path:
    base = os.environ.get("EXPNOTE_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base).expanduser() / "expnote"
    return Path.home() / ".config" / "expnote"


def data_home() -> Path:
    base = os.environ.get("EXPNOTE_DATA_HOME") or os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser() / "expnote"
    return Path.home() / ".local" / "share" / "expnote"


def registry_path() -> Path:
    return config_home() / "config.toml"


def default_workspace_dir(name: str) -> Path:
    return data_home() / "workspaces" / name


def resolve_workspace(
    *,
    workspace: str | None = None,
    workspace_dir: Path | None = None,
    require_obsidian: bool = False,
) -> WorkspaceContext:
    name: str | None = workspace
    if workspace_dir is None:
        if workspace is None:
            name = _active_workspace()
        assert name is not None
        workspace_dir = _registered_workspace_dir(name) or default_workspace_dir(name)
    workspace_dir = workspace_dir.expanduser().resolve()
    config = _read_workspace_config(workspace_dir)
    obsidian_root_text = config.get("obsidian_root") or config.get("root")
    obsidian_root = (
        Path(obsidian_root_text).expanduser().resolve() if obsidian_root_text else None
    )
    if require_obsidian and obsidian_root is None:
        raise typer.BadParameter(
            "This workspace has no Obsidian projection configured. "
            "Run `expnote init --workspace <name> --obsidian-root <vault> ...` first."
        )
    return WorkspaceContext(
        root=obsidian_root or workspace_dir,
        workspace_dir=workspace_dir,
        obsidian_root=obsidian_root,
        name=name,
    )


def write_workspace_config(
    *,
    workspace: str,
    workspace_dir: Path,
    set_active: bool = True,
) -> None:
    config_home().mkdir(parents=True, exist_ok=True)
    existing = _read_registry()
    existing[f"workspace.{workspace}.dir"] = str(workspace_dir.expanduser().resolve())
    if set_active:
        existing["active_workspace"] = workspace
    _write_registry(existing)


def set_active_workspace(workspace: str) -> dict[str, str]:
    existing = _read_registry()
    key = f"workspace.{workspace}.dir"
    if key not in existing:
        raise typer.BadParameter(f"workspace not registered: {workspace}")
    existing["active_workspace"] = workspace
    _write_registry(existing)
    return {"workspace": workspace, "workspace_dir": existing[key]}


def list_workspaces() -> list[dict[str, str | bool]]:
    existing = _read_registry()
    active = existing.get("active_workspace")
    rows: list[dict[str, str | bool]] = []
    prefix = "workspace."
    suffix = ".dir"
    for key, value in sorted(existing.items()):
        if key.startswith(prefix) and key.endswith(suffix):
            name = key.removeprefix(prefix).removesuffix(suffix)
            rows.append(
                {
                    "name": name,
                    "workspace_dir": value,
                    "active": name == active,
                }
            )
    return rows


def _active_workspace() -> str:
    registry = _read_registry()
    active = registry.get("active_workspace")
    if not active:
        raise typer.BadParameter(
            "No active expnote workspace. Run `expnote init --workspace <name>` "
            "or `expnote workspace use <name>` first."
        )
    return active


def _registered_workspace_dir(workspace: str) -> Path | None:
    value = _read_registry().get(f"workspace.{workspace}.dir")
    if value is None:
        return None
    return Path(value).expanduser()


def _read_workspace_config(workspace_dir: Path) -> dict[str, str]:
    try:
        return read_config(workspace_dir, state_dir=workspace_dir)
    except FileNotFoundError:
        return {}


def _read_registry() -> dict[str, str]:
    path = registry_path()
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"')
    return data


def _write_registry(data: dict[str, Any]) -> None:
    registry_path().write_text(
        "\n".join(
            f'{key} = "{_toml_string(str(value))}"'
            for key, value in sorted(data.items())
        )
        + "\n",
        encoding="utf-8",
    )


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
