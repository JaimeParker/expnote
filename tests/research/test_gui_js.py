"""Run the GUI modules under Node against a live server (skipped without Node)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from minivault import EV_PAGE, EV_PATH, write
from test_gui import FIXED_NOW, world  # noqa: F401 - fixture

from expnote.research.web_routes import create_research_app

JS_DIR = Path(__file__).parent / "js"
STATIC = Path(__file__).parents[2] / "expnote/research/static"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not found")


@pytest.fixture
def server(world):  # noqa: F811
    root, proj = world
    # a page still waiting for the user's labels, so the editor renders
    waiting = EV_PAGE.replace("status: labelled", "status: rendered")
    waiting = waiting.replace("labelled_by: user\n", "").replace(
        "| orig | 跌落 |", "| orig |  |"
    )
    write(proj / EV_PATH.replace("演示", "待标注"), waiting)
    write(
        proj / "plans/PLAN-900C--open.md",
        '---\nschema: 3\ntype: plan\nid: PLAN-900C\ntitle: "PLAN-900C"\n'
        "status: draft\n---\n# 开放计划\n",
    )
    app = create_research_app(clock=lambda: FIXED_NOW)
    app.state.research.refresh_interval = 0
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert instance.started, "server did not start"
    port = instance.servers[0].sockets[0].getsockname()[1]
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}")
    instance.should_exit = True
    thread.join(10)


def test_every_page_renders_without_errors(server):
    done = subprocess.run(
        ["node", "--import", "./register.mjs", "smoke.mjs", server.url, "off2on"],
        cwd=JS_DIR,
        env={**os.environ, "RESEARCH_STATIC": str(STATIC), "SEARCH_QUERY": "PLAN"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.stdout, done.stderr
    report = json.loads(done.stdout)
    assert report["failures"] == [] and report["problems"] == [], done.stdout[:2000]
    names = {r["name"] for r in report["results"]}
    assert {"home", "project", "decisions", "todos", "search", "health"} <= names
    assert any(n.startswith("plan PLAN-900A") for n in names)
    assert any(n.startswith("ev ") for n in names)
    assert report["pages"] >= 15
    assert report["checks"] >= 35  # the write-action panels, errors and toasts
    by_name = {r["name"]: r for r in report["results"]}
    # master-detail pages render the selected item in the preview pane
    for name in ("decisions-sel", "ev-sel", "ev-figures", "todos"):
        assert by_name[name]["previews"] == 1, name
    plan = next(r for r in report["results"] if r["name"] == "plan PLAN-900A")
    assert plan["nodes"] > 100 and plan["h1"].startswith("PLAN-900A")
