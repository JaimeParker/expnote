"""The split project keeps lifecycle state and files in their configured stores."""

from pathlib import Path

from minivault import EV_PATH, EXEC_PATH, PLAN_PATH, RES_PATH
from writekit import registered_vault, run

from expnote.research.view import open_view
from expnote.research.workflow import read_state


def test_split_init_and_managed_documents(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    project = vault / "Study"
    project.mkdir()
    args = ("project", "init", "--project-dir", project, "--layout", "split")
    preview = run(*args)
    assert preview["_exit"] == 4, preview
    assert not Path(preview["data"]["absProfileNote"]).exists()
    created = run(*args, "--apply")
    assert created["_exit"] == 0, created
    root = Path(created["data"]["absResearchDir"])
    assert (root / "profile.md").is_file()
    made = run("new", "plan", "--id", "PLAN-001", "--title", "Study", "--apply")
    assert made["_exit"] == 0, made
    made = run("new", "ev", "--plan", "PLAN-001", "--title", "Review", "--apply")
    assert made["_exit"] == 0, made
    from expnote.research.doctor import pending_changes

    with open_view("study") as view:
        assert pending_changes(view.context) == 0
        evidence = next(n for n in view.snapshot.notes if n.type == "evidence")
        assert evidence.path.is_relative_to(project)
        assert (project / "evidence" / "_appendices").is_dir()
        assert view.context.entry.storage("res") == "vault"
        assert evidence.get("record_id")


def test_migration_rehomes_records_and_imports_workflow(tmp_path):
    _, project = registered_vault(tmp_path)
    command = ("project", "migrate-layout", "off2on")
    analysis = project / "Analysis.md"
    analysis.write_text(f"Quote [[{Path(EV_PATH).stem}#Figures|source]] here.\n")
    preview = run(*command)
    assert preview["_exit"] == 4, preview
    assert preview["data"]["conflicts"] == []
    assert (project / EXEC_PATH).is_file()
    migrated = run(*command, "--apply")
    assert migrated["_exit"] == 0, migrated
    assert (project / PLAN_PATH).is_file()
    assert not (project / RES_PATH).exists()
    assert "](http://localhost:8765/research/#/p/off2on/record/" in analysis.read_text()
    assert "?anchor=Figures)" in analysis.read_text()
    with open_view("off2on") as view:
        resolved = view.context.resolved
        assert (resolved.research_dir / RES_PATH).is_file()
        plan = next(n for n in view.snapshot.notes if n.type == "plan")
        states, _ = read_state("off2on")
        assert states[plan.get("id")]["fields"]["status"] == plan.get("status")


def test_approval_pins_plan_across_edits(tmp_path):
    from test_write_commands import confirm, new_decision

    _, project = registered_vault(tmp_path)
    out = run("project", "migrate-layout", "off2on", "--apply")
    assert out["_exit"] == 0, out
    draft = next(p for p in (project / "plans").glob("*.md") if "900B" in p.name)
    original = draft.read_text()
    decision = new_decision("Approve the saved version")
    assert decision["_exit"] == 0, decision
    ident = decision["data"]["id"]
    workflow = tmp_path / "workflow.json"
    workflow.write_text(
        '{"deliverables":[{"id":"replication","question":"Does it replicate?",'
        '"criteria":"Apply the PLAN criterion","review":"agent","files":["output"]}]}'
    )
    args = ("plan", "submit", "PLAN-900B", "--decision", ident, "--workflow", workflow)
    preview = run(*args)
    assert preview["_exit"] == 4, preview
    applied = run(*args, "--apply")
    assert applied["_exit"] == 0, applied
    assert run(*args, "--apply")["_exit"] == 0
    draft.write_text(original + "\nA later edit only in Obsidian.\n")
    approved = confirm(ident)
    assert approved["_exit"] == 0, approved
    with open_view("off2on") as view:
        plan = next(n for n in view.snapshot.notes if n.get("id") == "PLAN-900B")
        assert plan.get("status") == "approved"
        assert "A later edit" not in plan.body
    assert "A later edit" in draft.read_text()

    created = run(
        "new",
        "exec",
        "--plan",
        "PLAN-900B",
        "--stage",
        "S1",
        "--title",
        "First stage",
        "--apply",
    )
    assert created["_exit"] == 0, created
    path = Path(created["data"]["created"][0]["absPath"])
    with open_view("off2on") as view:
        heading = f"## {view.profile.heading_rules}"
    path.write_text(
        path.read_text().replace(heading, heading + "\n\nUse the approved criterion.")
    )
    freeze = ("exec", "freeze", "PLAN-900B-S1", "--rules-decision", ident, "--apply")
    frozen = run(*freeze)
    assert frozen["_exit"] == 0, frozen
    states, _ = read_state("off2on")
    original_revision = states["PLAN-900B"]["revision"]
    assert states["PLAN-900B-S1"]["planRevision"] == original_revision

    second = new_decision("Approve the amended criterion")["data"]["id"]
    workflow.write_text(workflow.read_text()[:-1] + ',"impact":{}}')
    assert (
        run(
            "plan",
            "submit",
            "PLAN-900B",
            "--decision",
            second,
            "--workflow",
            workflow,
            "--apply",
        )["_exit"]
        == 0
    )
    confirmed = confirm(second)
    assert confirmed["_exit"] == 0, confirmed
    assert run(*freeze)["_exit"] == 0
    states, _ = read_state("off2on")
    assert states["PLAN-900B"]["revision"] != original_revision
    assert states["PLAN-900B-S1"]["planRevision"] == original_revision


def test_review_updates_sqlite_and_conflicts_when_page_is_stale(tmp_path):
    from urllib.parse import quote

    from test_gui_writes import EV_STEM, P, make_world, post

    world = make_world(tmp_path)
    migrated = run("project", "migrate-layout", "off2on", "--apply")
    assert migrated["_exit"] == 0, migrated
    rel = world.ev.relative_to(world.proj)
    with open_view("off2on") as view:
        path = view.context.resolved.material_dir / rel
    original = path.read_bytes()
    url = f"/ev/{quote(EV_STEM)}"
    shown = world.client.get(P + url).json()["data"]["ev"]
    assert shown["status"] == "rendered"
    stale = shown["sha256"]
    row = {"key": "...", "values": {"Value": "跌落"}}
    saved = post(world, url + "/labels", {"rows": [row], "expectedSha256": stale})
    assert saved.status_code == 200, saved.text
    stale_request = post(
        world,
        url + "/labels",
        {"rows": [{**row, "values": {"Value": "恢复"}}], "expectedSha256": stale},
    )
    assert stale_request.status_code == 409, stale_request.text
    shown = world.client.get(P + url).json()["data"]["ev"]
    done = post(
        world,
        url + "/labels",
        {"rows": [], "expectedSha256": shown["sha256"], "commit": True},
    )
    assert done.status_code == 200, done.text
    assert world.client.get(P + url).json()["data"]["ev"]["status"] == "labelled"
    assert path.read_bytes() != original
    assert "跌落" in path.read_text() and "status: labelled" not in path.read_text()


def test_restore_recovers_vault_bytes_and_legacy_registry(tmp_path):
    _, project = registered_vault(tmp_path)
    original = (project / PLAN_PATH).read_bytes()
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    assert run("project", "restore-layout", "off2on")["_exit"] == 4
    restored = run("project", "restore-layout", "off2on", "--apply")
    assert restored["_exit"] == 0, restored
    assert (project / PLAN_PATH).read_bytes() == original
    assert (project / RES_PATH).is_file()
    with open_view("off2on") as view:
        assert view.context.entry.layout == "vault"


def test_result_review_uses_the_submitted_revision_and_stable_url(tmp_path):
    from test_gui_writes import P, first_read_result, make_world, post

    world = make_world(tmp_path)
    old = first_read_result(world)
    old.write_text(old.read_text() + "\nSubmitted figure interpretation.\n")
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    with open_view("off2on") as view:
        root = view.context.resolved.material_dir
    moved = root / old.relative_to(world.proj)
    renamed = moved.with_name("Updated title.md")
    moved.rename(renamed)
    renamed.write_text(renamed.read_text() + "\nLater analysis draft.\n")

    stable = world.client.get(P + "/record/RES-PLAN-900B").json()["data"]
    assert stable == {"page": "note", "arg": "Updated title"}
    shown = world.client.get(P + "/note/Updated%20title").json()["data"]
    assert "Submitted figure interpretation." in shown["html"]
    assert "Later analysis draft." in shown["html"]
    assert "Later analysis draft." not in shown["revisionHtml"]
    assert "Submitted figure interpretation." in shown["revisionHtml"]
    applied = post(
        world,
        "/res/RES-PLAN-900B/review",
        {"expectedSha256": shown["note"]["sha256"], "verdict": "pass"},
    )
    assert applied.status_code == 200, applied.text
    states, _ = read_state("off2on")
    assert states["RES-PLAN-900B"]["fields"]["status"] == "confirmed"


def test_legacy_card_split_keeps_its_record_id_and_managed_appendix(tmp_path):
    from test_write_commands import split_world

    _, project, card = split_world(registered_vault(tmp_path))
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    with open_view("off2on") as view:
        old = next(n for n in view.snapshot.notes if n.stem == card.stem)
    split = run("ev", "split", card.stem, "--exec", "PLAN-900A-S1", "--apply")
    assert split["_exit"] == 0, split
    with open_view("off2on") as view:
        appendix = view.context.resolved.physical_path(split["data"]["appendixPath"])
        assert appendix.is_file()
        assert appendix.is_relative_to(view.context.resolved.research_dir)
        new = next(n for n in view.snapshot.notes if n.stem == card.stem)
        assert new.get("record_id") == old.get("record_id")
        assert new.get("status") == "labelled"
        assert new.get("appendix")
    assert not (project / "ops" / appendix.name).exists()


def test_adopt_can_turn_a_user_written_draft_into_a_plan(tmp_path):
    _, project = registered_vault(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    note = project / "A research idea.md"
    note.write_text("# An idea\n\nA question written by the user.\n")
    result = run("plan", "adopt", note.name, "--id", "PLAN-901", "--apply")
    assert result["_exit"] == 0, result
    with open_view("off2on") as view:
        adopted = next(n for n in view.snapshot.notes if n.get("id") == "PLAN-901")
        assert adopted.path.is_relative_to(project)
        assert "A question written by the user." in adopted.body
        assert adopted.get("status") == "draft"


def test_shared_images_allow_only_identical_managed_and_vault_copies(tmp_path):
    from minivault import IMAGES_REL

    vault, project = registered_vault(tmp_path)
    image = vault / IMAGES_REL / "o2o_demo.png"
    (project / "Image analysis.md").write_text("![[o2o_demo.png]]\n", encoding="utf-8")
    migrated = run("project", "migrate-layout", "off2on", "--apply")
    assert migrated["_exit"] == 0, migrated
    assert image.is_file()
    with open_view("off2on") as view:
        managed = view.context.resolved.images_dir / image.name
        assert managed.read_bytes() == image.read_bytes()
        assert not any(
            x.code == "NAME.DUPLICATE_ATTACHMENT" for x in view.checks().findings
        )
    image.write_bytes(b"different")
    with open_view("off2on") as view:
        assert any(
            x.code == "NAME.DUPLICATE_ATTACHMENT" for x in view.checks().findings
        )


def test_vault_res_is_current_with_a_separate_review_version(tmp_path):
    from test_gui_writes import P, first_read_result, make_world, post

    world = make_world(tmp_path)
    old = first_read_result(world)
    old.write_text(old.read_text() + "\n![[o2o_demo.png]]\n")
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    preview = run("project", "results-in-vault", "off2on")
    assert preview["_exit"] == 4, preview
    assert not old.exists()
    moved = run("project", "results-in-vault", "off2on", "--apply")
    assert moved["_exit"] == 0, moved
    assert (old.parent.parent / "research-assets/o2o_demo.png").is_file()
    original = old.read_text()
    old.write_text(original + "\nA new finding.\n")
    note_url = f"{P}/note/RES--PLAN-900B%20%E5%88%A4%E8%AF%BB"
    shown = world.client.get(note_url).json()["data"]
    assert "A new finding." in shown["html"]
    assert "A new finding." not in shown["revisionHtml"]
    assert shown["sourceChanged"]
    prior = shown["note"]["revision"]
    confirmed = post(
        world,
        "/res/RES-PLAN-900B/review",
        {
            "expectedSha256": shown["note"]["sha256"],
            "verdict": "pass",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    submitted = run(
        "res", "submit", "RES-PLAN-900B", "--deviation-impact", "weak", "--apply"
    )
    assert submitted["_exit"] == 0, submitted
    assert submitted["data"]["revised"]
    shown = world.client.get(note_url).json()["data"]
    assert prior in shown["history"] and prior != shown["note"]["revision"]
    assert "A new finding." in shown["revisionHtml"]


def test_vault_res_publish_requires_merge_after_author_edits(tmp_path):
    from test_gui_writes import first_read_result, make_world

    world = make_world(tmp_path)
    source = first_read_result(world)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    assert run("project", "results-in-vault", "off2on", "--apply")["_exit"] == 0
    candidate = tmp_path / "generated.md"
    original = source.read_text()
    candidate.write_text(original + "\nGenerated paragraphs.\n")
    args = (
        "res",
        "publish",
        "RES-PLAN-900B",
        "--from-file",
        candidate,
        "--apply",
        "--no-quiet",
    )
    pending = run(*args)
    assert pending["_exit"] == 0 and pending["data"]["pending"], pending
    assert Path(pending["data"]["proposal"]).is_file()
    assert source.read_text() == original
    merged = run(*args, "--expected-sha256", pending["data"]["currentSha256"])
    assert merged["_exit"] == 0 and not merged["data"]["pending"], merged
    candidate.write_text(original + "\nNext generated paragraphs.\n")
    replaced = run(*args)
    assert replaced["_exit"] == 0 and not replaced["data"]["pending"], replaced
    source.write_text(source.read_text() + "\nWritten in Obsidian.\n")
    candidate.write_text(original + "\nA new proposed draft.\n")
    pending = run(*args)
    assert pending["_exit"] == 0 and pending["data"]["pending"], pending
    assert "Written in Obsidian." in source.read_text()
