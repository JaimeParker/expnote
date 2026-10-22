"""Shared CLI plumbing of the write commands: confirm command and human rendering."""

from __future__ import annotations

import shlex

SKIPPED_PARAMS = frozenset({"apply", "as_json"})


def _current_context():
    # Recent Typer vendors click; older releases use the installed package.
    try:
        from typer._click.globals import get_current_context
    except ImportError:
        from click import get_current_context
    return get_current_context()


def confirm_command() -> str:
    """The running command line again, with --apply (for the dry-run output)."""
    ctx = _current_context()
    parts = ["expnote", *ctx.command_path.split()[1:]]
    for param in ctx.command.params:
        name = param.name
        if name in SKIPPED_PARAMS or name is None:
            continue
        value = ctx.params.get(name)
        kind = getattr(param, "param_type_name", "")
        if kind == "argument":
            if value is not None:
                parts.append(shlex.quote(str(value)))
            continue
        if kind != "option" or value is None:
            continue
        if param.is_flag:
            if value != param.default:
                flag = param.opts[0] if value else (param.secondary_opts or [None])[0]
                if flag:
                    parts.append(flag)
            continue
        if value == param.default or value in ((), []):
            continue
        for item in value if isinstance(value, (list, tuple)) else [value]:
            parts += [param.opts[0], shlex.quote(str(item))]
    parts.append("--apply")
    return " ".join(parts)


def render_write(data: dict) -> str:
    lines: list[str] = []
    for change in data.get("changes") or []:
        lines.append(f"{change['op']}  {change['path']}  [{change['address']}]")
        if change.get("unifiedDiff"):
            lines.append(change["unifiedDiff"])
    if data.get("dryRun"):
        lines.append("dry-run: nothing was written. To apply, run:")
        lines.append(f"  {data.get('confirmCommand')}")
    elif data.get("applied"):
        lines.append(f"applied {len(data['changes'])} change(s)")
    else:
        lines.append("no changes needed")
    return "\n".join(lines)


def render_init(data: dict) -> str:
    """Human summary of `project init`: what is bound, then the planned changes."""
    lines = [
        f"project   {data['project']}"
        + (" (already registered)" if data.get("alreadyRegistered") else ""),
        f"vault     {data['absVaultRoot']}",
        f"folder    {data['absProjectDir']}",
        f"images    {data['absImagesDir']}",
        f"profile   {data['absProfileNote']}",
    ]
    entry = data.get("registryEntry")
    if entry:
        lines.append(
            f"registry  {data['registryFile']} (active: {data['activeProject']})"
        )
    body = render_write(data)
    if data.get("alreadyRegistered"):
        body = "nothing to do: this folder is already bound to the project above"
    lines += ["", body, "", "next:"]
    lines += [f"  {command}" for command in data.get("nextCommands", [])]
    return "\n".join(lines)
