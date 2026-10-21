"""The research-command envelope: stdout carries data, stderr diagnostics."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable

import typer

from expnote.research.errors import ResearchError

SCHEMA_VERSION = 1


@dataclasses.dataclass
class CommandResult:
    data: dict
    warnings: list[dict] = dataclasses.field(default_factory=list)
    meta: dict = dataclasses.field(default_factory=dict)
    exit_code: int = 0


def success_envelope(command: str, result: CommandResult) -> dict:
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "data": result.data,
        "warnings": result.warnings,
        "meta": result.meta,
    }


def failure_envelope(command: str, error: ResearchError) -> dict:
    return {
        "ok": False,
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "error": {
            "code": error.code,
            "message": error.message,
            "remediation": error.remediation,
            "retryable": error.retryable,
            "details": error.details,
        },
        "warnings": [],
    }


def select_fields(items: list[dict], fields: str | None) -> list[dict]:
    """Keep only the named top-level keys of each item (order of `fields`)."""
    if not fields:
        return items
    names = [f.strip() for f in fields.split(",") if f.strip()]
    if items:
        unknown = [n for n in names if n not in items[0]]
        if unknown:
            raise ResearchError(
                "USAGE_BAD_FIELDS",
                f"unknown field(s) {unknown}; available: {sorted(items[0])}",
                exit_code=2,
            )
    return [{n: item[n] for n in names} for item in items]


def dump_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def run_command(
    command: str,
    as_json: bool,
    action: Callable[[], CommandResult],
    render: Callable[[dict], str] | None = None,
) -> None:
    """Run `action`, print the envelope (JSON) or a human rendering, then exit."""
    try:
        result = action()
    except ResearchError as error:
        if as_json:
            typer.echo(dump_json(failure_envelope(command, error)))
        else:
            typer.echo(f"{error.code}: {error.message}", err=True)
            if error.remediation:
                typer.echo(f"  try: {error.remediation}", err=True)
        raise typer.Exit(error.exit_code) from None
    if as_json:
        typer.echo(dump_json(success_envelope(command, result)))
    else:
        text = render(result.data) if render else None
        typer.echo(
            text
            if text is not None
            else json.dumps(result.data, ensure_ascii=False, indent=2, sort_keys=True)
        )
        for warning in result.warnings:
            typer.echo(f"warning: {warning['code']}: {warning['message']}", err=True)
    if result.exit_code:
        raise typer.Exit(result.exit_code)
