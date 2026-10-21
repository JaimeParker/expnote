"""Print oracle-vs-expnote differences without pytest.

PYTHONPATH=. python3 tests/research/parity/parity_diff.py VAULT_COPY [--scenarios]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from minivault import PLAN_IDS, SCENARIOS, make_context, scenario_vault  # noqa: E402
from parity_oracle import PROJECT_REL, compare, load_oracle  # noqa: E402
from parity_real import real_context  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    vault = Path(argv[0]).expanduser()
    oracle = load_oracle(vault)
    differences = 0

    def show(label, result):
        nonlocal differences
        missing, extra, stats = result
        count = len(missing) + len(extra) + len(stats)
        differences += count
        print(f"{label}: {'OK' if not count else f'{count} difference(s)'}")
        for item in missing:
            print(f"  missing in expnote: {item}")
        for item in extra:
            print(f"  extra in expnote:   {item}")
        for item in stats:
            print(f"  stat:               {item}")

    context = real_context(vault)
    plan_ids = [
        None,
        "PLAN-001",
        "PLAN-002",
        "PLAN-002A",
        "PLAN-002B",
        "PLAN-002B-gammafix",
        "PLAN-002C",
        "PLAN-003A",
    ]
    for plan_id in plan_ids:
        show(
            f"real vault {plan_id or '(all)'}",
            compare(oracle, vault, vault / PROJECT_REL, context, plan_id),
        )
    if "--scenarios" in argv:
        for name in sorted(SCENARIOS):
            for plan_id in (None, *PLAN_IDS):
                with tempfile.TemporaryDirectory() as tmp:
                    root, proj = scenario_vault(Path(tmp), name)
                    show(
                        f"scenario {name} {plan_id or '(all)'}",
                        compare(oracle, root, proj, make_context(root, proj), plan_id),
                    )
    print(f"total differences: {differences}")
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
