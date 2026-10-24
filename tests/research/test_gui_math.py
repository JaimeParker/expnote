"""renderMath degrades gracefully without KaTeX, under Node (skipped without Node)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).parent / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not found")


def test_render_math_degrades_without_katex(tmp_path):
    env = {**os.environ, "RESEARCH_STATIC": str(tmp_path)}
    done = subprocess.run(
        ["node", "--import", "./register.mjs", "math.mjs"],
        cwd=JS_DIR, capture_output=True, text=True, timeout=60, env=env,
    )
    assert done.stdout, done.stderr
    checks = json.loads(done.stdout)["checks"]
    assert len(checks) >= 6
    assert [c["name"] for c in checks if not c["ok"]] == []
