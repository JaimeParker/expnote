"""Differential tests against vault.py (skipped unless EXPNOTE_PARITY_VAULT is set).

EXPNOTE_PARITY_VAULT points at a frozen read-only copy of the Off2On vault that
also holds the profile fixture in its project directory.
"""

from __future__ import annotations

import pytest
from minivault import PLAN_IDS, SCENARIOS, make_context, scenario_vault
from parity_oracle import (
    PROJECT_REL,
    compare,
    default_vault,
    load_oracle,
)

VAULT = default_vault()
if VAULT is None:
    pytest.skip(
        "set EXPNOTE_PARITY_VAULT to a frozen vault copy", allow_module_level=True
    )


@pytest.fixture(scope="module")
def oracle():
    return load_oracle(VAULT)


def _report(missing, extra, stats):
    return f"missing in expnote: {missing}\nextra in expnote: {extra}\nstats: {stats}"


@pytest.mark.parametrize(
    "plan_id", [None, "PLAN-002C", "PLAN-003A", "PLAN-002A", "PLAN-001"]
)
def test_real_vault_parity(oracle, plan_id):
    from parity_real import real_context

    context = real_context(VAULT)
    missing, extra, stats = compare(
        oracle, VAULT, VAULT / PROJECT_REL, context, plan_id
    )
    assert not missing and not extra and not stats, _report(missing, extra, stats)


@pytest.mark.parametrize("plan_id", [None, *PLAN_IDS])
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_parity(oracle, tmp_path, name, plan_id):
    root, proj = scenario_vault(tmp_path, name)
    missing, extra, stats = compare(
        oracle, root, proj, make_context(root, proj), plan_id
    )
    assert not missing and not extra and not stats, _report(missing, extra, stats)
