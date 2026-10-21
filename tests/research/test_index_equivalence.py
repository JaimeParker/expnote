"""The index-backed snapshot must give exactly the findings of the direct scan."""

from __future__ import annotations

import pytest
from minivault import PLAN_IDS, SCENARIOS, build_vault, make_context, scenario_vault

from expnote.research.checks import run_checks
from expnote.research.index import ensure_index, snapshot_from_index
from expnote.research.snapshot import scan_vault


def index_snapshot(context):
    reader = ensure_index(context)
    try:
        return snapshot_from_index(context, reader.conn)
    finally:
        reader.close()


def assert_same(context, plan_id):
    direct = run_checks(scan_vault(context), plan_id)
    indexed = run_checks(index_snapshot(context), plan_id)
    assert [f.key for f in indexed.findings] == [f.key for f in direct.findings]
    assert [f.data for f in indexed.findings] == [f.data for f in direct.findings]
    assert indexed.stats == direct.stats
    assert indexed.scope == direct.scope


def test_base_vault_equivalent(tmp_path):
    root, proj = build_vault(tmp_path)
    for plan_id in (None, *PLAN_IDS):
        assert_same(make_context(root, proj), plan_id)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenarios_equivalent(tmp_path, name):
    root, proj = scenario_vault(tmp_path, name)
    context = make_context(root, proj)
    for plan_id in (None, *PLAN_IDS):
        assert_same(context, plan_id)


def test_conflict_files_are_the_only_deliberate_difference(tmp_path):
    from minivault import write

    root, proj = build_vault(tmp_path)
    write(proj / "别的笔记.sync-conflict-1.md", "x")
    context = make_context(root, proj)
    assert any("sync-conflict" in n.rel for n in scan_vault(context).notes)
    snapshot = index_snapshot(context)
    names = {f.where for f in run_checks(snapshot).findings}
    assert not any("sync-conflict" in n for n in names)
    assert not any("conflict" in key for key in snapshot.md_index)
    assert not any("conflict" in n.rel for n in snapshot.notes)


def test_real_vault_copy_equivalent():
    import os
    from pathlib import Path

    vault = os.environ.get("EXPNOTE_PARITY_VAULT")
    if not vault:
        pytest.skip("set EXPNOTE_PARITY_VAULT to a frozen vault copy")
    from parity_real import real_context

    context = real_context(Path(vault).expanduser())
    for plan_id in (None, "PLAN-001", "PLAN-002A", "PLAN-002C", "PLAN-003A"):
        assert_same(context, plan_id)
