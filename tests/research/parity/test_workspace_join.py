"""Run joins against a COPY of the real workspace DB (skipped unless both exist).

The real workspace is never opened: its sqlite file is copied into the temporary
expnote data home and the project is bound to the copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from parity_oracle import PROJECT_REL, default_vault
from parity_real import IMAGES_REL, PROFILE_NAME
from readkit import data, invoke

from expnote.research.runs import open_workspace
from expnote.workspace import data_home, default_workspace_dir

REAL_DB = Path.home() / ".local/share/expnote/workspaces/off2on-baseline/expnote.sqlite"
VAULT = default_vault()
if VAULT is None or not REAL_DB.is_file():
    pytest.skip(
        "needs EXPNOTE_PARITY_VAULT and the real workspace", allow_module_level=True
    )


def register(*extra):
    project = VAULT / PROJECT_REL
    result = invoke(
        "project", "register", "--vault-root", VAULT, "--project-dir", project,
        "--images-dir", VAULT / IMAGES_REL, "--profile-note", project / PROFILE_NAME,
        "--id", "off2on", "--json", *extra,
    )  # fmt: skip
    assert result.exit_code == 0, result.output


@pytest.fixture
def workspace_copy():
    target = default_workspace_dir("off2on-baseline")
    target.mkdir(parents=True)
    shutil.copy(REAL_DB, target / "expnote.sqlite")
    (target / "config.toml").write_text(
        f'project = "copy"\nstate_dir = "{target}"\n', encoding="utf-8"
    )
    return target


def test_copy_is_read_not_the_original(workspace_copy):
    conn, path = open_workspace("off2on-baseline")
    conn.close()
    assert path == workspace_copy / "expnote.sqlite"
    assert str(path).startswith(str(data_home()))


def test_bundle_joins_runs_and_docs_from_the_copy(workspace_copy):
    register("--workspace", "off2on-baseline")
    bundle = data(
        "plan", "bundle", "PLAN-003A", "--sections", "runs,analysisDocs,evidence"
    )
    assert bundle["runs"], "PLAN-003A cites runs that the workspace knows"
    first = bundle["runs"][0]
    assert first["workspace"] == "off2on-baseline" and first["citedBy"]
    assert all(r["topic"] for r in bundle["runs"])
    assert isinstance(bundle["analysisDocs"], list)


def test_run_links_leave_compat_check_results_alone(workspace_copy):
    register()
    plain = data("check", "--compat", "vault-py")
    register("--workspace", "off2on-baseline")
    linked = data("check", "--compat", "vault-py")
    assert linked["findings"] == plain["findings"]
    assert linked["summary"] == plain["summary"]
