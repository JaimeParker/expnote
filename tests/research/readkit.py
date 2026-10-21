"""CLI helpers shared by the read-layer tests."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from minivault import IMAGES_REL, PROFILE_FIXTURE
from typer.testing import CliRunner

from expnote.cli import app
from expnote.research.index import index_path

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


def payload(result):
    assert result.stdout, result.output
    return json.loads(result.stdout)


def register(root: Path, proj: Path, *extra: str):
    result = invoke(
        "project", "register", "--vault-root", root, "--project-dir", proj,
        "--images-dir", root / IMAGES_REL, "--profile-note",
        proj / PROFILE_FIXTURE.name, "--json", *extra,
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    return result


def run(*args):
    """Invoke with --json and return the parsed envelope."""
    result = invoke(*args, "--json")
    body = payload(result)
    body["_exit"] = result.exit_code
    return body


def data(*args):
    body = run(*args)
    assert body["ok"] is True and body["_exit"] == 0, body
    return body["data"]


def touch_later(path: Path, text: str) -> None:
    """Write text and make sure mtime_ns differs from the previous version."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        time.sleep(0.01)
        path.write_text(text, encoding="utf-8")


def index_rows(vault_id: str, sql: str, *params):
    conn = sqlite3.connect(index_path(vault_id))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def vault_id(project: str = "off2on") -> str:
    return data("project", "show", project)["project"]["vault_id"]
