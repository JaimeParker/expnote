"""Edit, preview and select Research templates through the real local API."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from writekit import edit_profile, registered_vault, run

from expnote.research.model import parse_note
from expnote.research.newnote import BUILTIN_TEMPLATES
from expnote.research.web_routes import create_research_app
from expnote.workspace import config_home

URL = "/api/research/projects/off2on/templates"
LOCAL = "http://localhost:8765"


@pytest.fixture
def world(tmp_path):
    root, project = registered_vault(tmp_path)
    templates = project / "templates"
    templates.mkdir(exist_ok=True)
    note = templates / "project-plan.md"
    note.write_text(
        BUILTIN_TEMPLATES["plan"] + "\n## Project section\n", encoding="utf-8"
    )
    (templates / "Reference.md").write_text("# Project reference\n", encoding="utf-8")
    edit_profile(
        project, "templates:\n", "templates:\n  files:\n    plan: project-plan.md\n"
    )
    app = create_research_app()
    client = TestClient(app, base_url=LOCAL)
    return client, {"X-Expnote-Token": app.state.research.write_token}, root, project


def catalog(world, url=URL):
    response = world[0].get(url)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def template(world, kind, url=URL):
    return next(row for row in catalog(world, url)["templates"] if row["id"] == kind)


def change(world, action, ident, scope, *, url=URL, **values):
    return world[0].post(
        url,
        json={"action": action, "id": ident, "scope": scope, **values},
        headers=world[1],
    )


def apply(world, action, kind, scope, *, url=URL, **values):
    row = template(world, kind, url)
    version = (
        row["selectionVersion"]
        if action == "select"
        else row["files"][scope]["version"]
    )
    response = change(
        world, action, kind, scope, url=url, expectedVersion=version, **values
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def generated(*args):
    result = run("new", *args, "--apply")
    assert result["_exit"] == 0, result
    paths = [Path(row["absPath"]) for row in result["data"]["created"]]
    return [parse_note(p, p.name, p.read_text(encoding="utf-8")) for p in paths]


def test_edits_are_independent_and_cli_uses_the_selected_source(world, tmp_path):
    row = template(world, "plan")
    assert row["source"] == "project" and row["files"]["project"]["saved"]
    assert not row["files"]["global"]["saved"]
    global_draft = BUILTIN_TEMPLATES["plan"] + "\n## Global section\n"
    apply(world, "save", "plan", "global", text=global_draft)
    assert template(world, "plan")["source"] == "project"
    assert (config_home() / "research-templates" / "plan.md").read_text(
        encoding="utf-8"
    ) == global_draft
    text = generated("plan", "--id", "PLAN-911A", "--title", "Project")[0].body
    assert "Project section" in text and "Global section" not in text

    apply(world, "select", "plan", "global")
    project_draft = BUILTIN_TEMPLATES["plan"] + "\n## New project section\n"
    apply(world, "save", "plan", "project", text=project_draft)
    assert template(world, "plan")["source"] == "global"
    text = generated("plan", "--id", "PLAN-912A", "--title", "Global")[0].body
    assert "Global section" in text and "New project section" not in text
    apply(world, "select", "plan", "project")
    text = generated("plan", "--id", "PLAN-913A", "--title", "Project")[0].body
    assert "New project section" in text
    assert template(world, "res")["source"] == "global"

    other = tmp_path / "other-vault"
    (other / ".obsidian").mkdir(parents=True)
    project = other / "Other"
    project.mkdir()
    result = run(
        "project", "init", "--project-dir", project, "--layout", "split", "--apply"
    )
    assert result["_exit"] == 0, result
    url = URL.replace("off2on", "other")
    assert template(world, "plan", url)["files"]["global"]["text"] == global_draft
    initial = template(world, "plan", url)
    assert initial["source"] == "global" and not initial["files"]["project"]["saved"]
    apply(world, "save", "plan", "project", url=url, text=project_draft)
    assert template(world, "plan", url)["source"] == "global"
    apply(world, "select", "plan", "project", url=url)
    assert template(world, "plan", url)["source"] == "project"
    assert not template(world, "ev", url)["files"]["project"]["saved"]


def test_preview_escapes_html_and_invalid_template_keeps_disk_intact(world):
    original = template(world, "exec")["files"]["global"]
    text = BUILTIN_TEMPLATES["exec"] + "\n<script>alert(1)</script>\n"
    rendered = change(world, "preview", "exec", "global", text=text)
    assert rendered.status_code == 200, rendered.text
    data = rendered.json()["data"]
    assert "{{" not in data["text"] and "&lt;script&gt;" in data["html"]
    assert not Path(original["path"]).exists()
    for invalid in (
        text.replace('plan: "[[{{plan}}]]"', 'plan: "[[other-plan]]"'),
        text.replace("type: exec-plan", "type: result"),
        text.replace('id: "{{id}}"', "id: hardcoded"),
        text.replace("{{rules_heading}}", "Rules"),
        text.replace("Risks and resources", "{{oops}}"),
        text.replace("servers: []", "servers: ["),
    ):
        response = change(
            world,
            "save",
            "exec",
            "global",
            text=invalid,
            expectedVersion=original["version"],
        )
        assert response.status_code == 422 and not Path(original["path"]).exists(), (
            response.text
        )


def test_offline_edit_conflicts_and_restore_keeps_previous_actual_bytes(world):
    original = template(world, "plan")["files"]["project"]
    path = Path(original["path"])
    before = path.read_bytes()
    new = original["text"] + "\nA fresh draft.\n"
    apply(world, "save", "plan", "project", text=new)
    response = change(
        world,
        "save",
        "plan",
        "project",
        text=original["text"],
        expectedVersion=original["version"],
    )
    assert response.status_code == 409, response.text
    assert path.read_text(encoding="utf-8") == new
    assert template(world, "plan")["files"]["project"]["canRestore"]
    apply(world, "restore", "plan", "project")
    assert path.read_bytes() == before
    edited = template(world, "plan")["files"]["project"]
    external = before + b"\nEdit from filesystem.\n"
    path.write_bytes(external)
    assert (
        change(
            world,
            "save",
            "plan",
            "project",
            text=new,
            expectedVersion=edited["version"],
        ).status_code
        == 409
    )
    assert path.read_bytes() == external


def test_two_simultaneous_edits_use_one_version(world):
    global_file = template(world, "plan")["files"]["global"]

    def save(marker):
        response = change(
            world,
            "save",
            "plan",
            "global",
            text=global_file["text"] + marker,
            expectedVersion=global_file["version"],
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(save, ("First.\n", "Second.\n")))
    assert sorted(statuses) == [200, 409]
    apply(world, "restore", "plan", "global")
    assert template(world, "plan")["files"]["global"]["text"] == global_file["text"]


def test_references_are_editable_and_local_write_guard_applies(world):
    reference = catalog(world)["references"][0]
    file = reference["files"]["project"]
    path = Path(file["path"])
    payload = {
        "id": reference["id"],
        "scope": "project",
        "action": "save",
        "expectedVersion": file["version"],
        "text": "# An updated reference\n",
    }
    assert world[0].post(URL, json=payload).status_code == 403
    assert (
        world[0]
        .post(URL, json=payload, headers=world[1] | {"Origin": "http://other.test"})
        .status_code
        == 403
    )
    unsafe = payload | {"id": "../profile.md"}
    assert world[0].post(URL, json=unsafe, headers=world[1]).status_code in (404, 422)
    assert path.read_text(encoding="utf-8") == file["text"]
    assert world[0].post(URL, json=payload, headers=world[1]).status_code == 200
    assert path.read_text(encoding="utf-8") == payload["text"]
    new = catalog(world)["references"][0]["files"]["project"]
    restored = change(
        world, "restore", reference["id"], "project", expectedVersion=new["version"]
    )
    assert restored.status_code == 200, restored.text
    assert path.read_text(encoding="utf-8") == file["text"]


def test_migrated_template_identity_is_not_copied_to_new_evidence(world):
    migrated = run("project", "migrate-layout", "off2on", "--apply")
    assert migrated["_exit"] == 0, migrated
    ev = BUILTIN_TEMPLATES["ev"].replace(
        "figures: []", 'figures: []\nrecord_id: "record-ev-template"'
    )
    appendix = BUILTIN_TEMPLATES["appendix"].replace(
        "runs: []", 'runs: []\nrecord_id: "record-appendix-template"'
    )
    for kind, text in (("ev", ev), ("appendix", appendix)):
        apply(world, "save", kind, "project", text=text)
        apply(world, "select", kind, "project")
    first, twin = generated("ev", "--plan", "PLAN-900A", "--title", "One")
    second, second_twin = generated("ev", "--plan", "PLAN-900A", "--title", "Two")
    records = {n.get("record_id") for n in (first, twin, second, second_twin)}
    assert len(records) == 4 and all(records)
    assert not records & {"record-ev-template", "record-appendix-template"}
    assert first.link_field("appendix") == twin.cstem
    assert second_twin.link_field("parent") == second.cstem


def test_failed_replace_preserves_the_template_and_last_restore(world, monkeypatch):
    from expnote.research import templates as storage

    original = template(world, "plan")["files"]["project"]
    path = Path(original["path"])
    before = path.read_bytes()
    first = original["text"] + "\nFirst edit.\n"
    apply(world, "save", "plan", "project", text=first)
    saved = template(world, "plan")["files"]["project"]

    def fail(_source, _destination):
        raise OSError("simulated failure")

    with monkeypatch.context() as stub:
        stub.setattr(storage, "_replace_with_retry", fail)
        response = change(
            world,
            "save",
            "plan",
            "project",
            text=first + "Second edit.\n",
            expectedVersion=saved["version"],
        )
    assert response.status_code == 503, response.text
    assert path.read_text(encoding="utf-8") == first
    apply(world, "restore", "plan", "project")
    assert path.read_bytes() == before


def test_invalid_global_variable_is_rejected_if_cli_cannot_fill_it(world):
    row = template(world, "plan")["files"]["global"]
    response = change(
        world,
        "save",
        "plan",
        "global",
        text=row["text"] + "\nStage {{stage}}\n",
        expectedVersion=row["version"],
    )
    assert response.status_code == 422
    assert "{{stage}}" in response.json()["error"]["message"]
