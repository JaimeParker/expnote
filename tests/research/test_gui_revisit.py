"""The visibilitychange refresh logic, run under Node (skipped without Node)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).parent / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not found")


def test_revisit_refresh_rules():
    done = subprocess.run(
        ["node", "revisit.mjs"], cwd=JS_DIR, capture_output=True, text=True, timeout=60
    )
    assert done.stdout, done.stderr
    checks = json.loads(done.stdout)["checks"]
    assert len(checks) >= 10
    assert [c["name"] for c in checks if not c["ok"]] == []
