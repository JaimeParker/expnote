"""Guards: no project constants in module code, lazy PyYAML, read-only check."""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from minivault import IMAGES_REL, PROFILE_FIXTURE, build_vault
from typer.testing import CliRunner

import expnote.research
from expnote.cli import app

SOURCE_DIR = Path(expnote.research.__file__).parent
CJK = re.compile("[⺀-鿿　-〿＀-￯]")


@pytest.mark.parametrize("path", sorted(SOURCE_DIR.glob("*.py")), ids=lambda p: p.name)
def test_module_code_holds_no_project_constants(path):
    text = path.read_text(encoding="utf-8")
    assert not CJK.search(text), "CJK text belongs in the profile"
    assert "o2o_" not in text and "O2O" not in text
    assert "PLAN-" not in text
    assert "vault:status" not in text


def test_importing_the_cli_does_not_import_yaml():
    code = "import sys, expnote.cli; sys.exit(1 if 'yaml' in sys.modules else 0)"
    done = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(SOURCE_DIR.parents[1])},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_missing_pyyaml_gives_env_error_envelope(tmp_path, monkeypatch):
    root, proj = build_vault(tmp_path)
    runner = CliRunner()
    args = [
        "project", "register", "--vault-root", str(root), "--project-dir", str(proj),
        "--images-dir", str(root / IMAGES_REL), "--profile-note",
        str(proj / PROFILE_FIXTURE.name), "--json",
    ]  # fmt: skip
    assert runner.invoke(app, args).exit_code == 0
    monkeypatch.setitem(sys.modules, "yaml", None)
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code == 6
    error = json.loads(result.stdout)["error"]
    assert (
        error["code"] == "ENV_MISSING_DEPENDENCY"
        and "expnote[research]" in error["remediation"]
    )
    assert runner.invoke(app, args).exit_code == 6


def tree_digest(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p.relative_to(root)): (
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_check_never_writes_under_the_vault(tmp_path, monkeypatch):
    root, proj = build_vault(tmp_path)
    runner = CliRunner()
    args = [
        "project", "register", "--vault-root", str(root), "--project-dir", str(proj),
        "--images-dir", str(root / IMAGES_REL), "--profile-note",
        str(proj / PROFILE_FIXTURE.name), "--json",
    ]  # fmt: skip
    assert runner.invoke(app, args).exit_code == 0
    before = tree_digest(root)
    vault = str(root)
    real_open, real_io_open = builtins.open, io.open

    def guarded(opener):
        def wrapper(file, mode="r", *a, **kw):
            writing = any(c in mode for c in "wax+")
            if writing and str(file).startswith(vault):
                raise AssertionError(f"write under the vault: {file} ({mode})")
            return opener(file, mode, *a, **kw)

        return wrapper

    monkeypatch.setattr(builtins, "open", guarded(real_open))
    monkeypatch.setattr(io, "open", guarded(real_io_open))
    for name in ("replace", "rename", "remove", "unlink", "rmdir", "mkdir"):
        original = getattr(os, name)

        def forbid(*a, _orig=original, _name=name, **kw):
            if a and str(a[0]).startswith(vault):
                raise AssertionError(f"os.{_name} under the vault: {a}")
            return _orig(*a, **kw)

        monkeypatch.setattr(os, name, forbid)
    with pytest.raises(AssertionError):  # the guard itself works
        (proj / "probe.txt").write_text("x", encoding="utf-8")
    for command in (
        ["check", "--json"],
        ["check", "--plan", "PLAN-900A", "--json"],
        ["project", "show", "off2on", "--json"],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
    assert tree_digest(root) == before
