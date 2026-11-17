"""Moving EV/RES preserves references, authorship and review history."""

from pathlib import Path

from minivault import EV_PATH, RES_PATH
from writekit import registered_vault, run

from expnote.research.view import open_view
from expnote.research.workflow import identity


def _move(*options):
    args = ("project", "storage", "off2on", *options)
    planned = run(*args)
    assert planned["_exit"] == 4, planned
    assert not planned["data"]["conflicts"], planned
    moved = run(*args, "--expect", planned["data"]["planHash"], "--apply")
    assert moved["_exit"] == 0, moved
    return moved["data"]


def test_storage_round_trip_keeps_identity_and_links(tmp_path):
    _, project = registered_vault(tmp_path)
    analysis = project / "Analysis test.md"
    ev_stem = Path(EV_PATH).stem
    analysis.write_text(f"Read [[{ev_stem}#Figures|the evidence]].\n", encoding="utf-8")
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    paths = run("project", "show", "off2on")["data"]
    original = Path(paths["absEvidenceDir"]) / Path(EV_PATH).name
    with open_view("off2on") as view:
        ident = identity("off2on", view.snapshot.note(ev_stem.casefold()))
    assert f"/record/{ident}?anchor=Figures" in analysis.read_text()
    _move("--ev", "vault")
    assert (project / EV_PATH).is_file() and not original.exists()
    assert "#Figures|the evidence]]" in analysis.read_text()
    assert not (project / RES_PATH).exists()
    _move("--ev", "research", "--res", "vault")
    assert original.is_file() and (project / RES_PATH).is_file()
    assert f"/record/{ident}?anchor=Figures" in analysis.read_text()
    with open_view("off2on") as view:
        assert identity("off2on", view.snapshot.note(ev_stem.casefold())) == ident


def test_storage_refuses_changed_preview(tmp_path):
    _, project = registered_vault(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    args = ("project", "storage", "off2on", "--ev", "vault")
    plan = run(*args)["data"]
    source = Path(run("project", "show", "off2on")["data"]["absEvidenceDir"])
    note = source / Path(EV_PATH).name
    note.write_bytes(note.read_bytes() + b"\nChanged by a sync client.\n")
    out = run(*args, "--expect", plan["planHash"], "--apply")
    assert out["_exit"] == 5, out
    assert note.is_file() and not (project / EV_PATH).exists()


def test_storage_restore_after_interruption(tmp_path, monkeypatch):
    import pytest

    from expnote.research import storage

    _, project = registered_vault(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    planned = storage.migrate_storage("off2on", ev="vault")
    source = Path(planned.data["moves"][0]["source"])
    original = source.read_bytes()

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as runtime:
        runtime.setattr(storage, "_set_positions", interrupt)
        with pytest.raises(KeyboardInterrupt):
            storage.migrate_storage(
                "off2on", ev="vault", apply=True, expected=planned.data["planHash"]
            )
    assert (project / EV_PATH).is_file()
    assert run("project", "storage", "off2on")["data"]["interrupted"]
    assert run("project", "restore-storage", "off2on", "--apply")["_exit"] == 0
    assert source.read_bytes() == original
    assert not (project / EV_PATH).exists()
    _move("--ev", "vault")


def test_web_storage_preview_is_protected_and_switches_images(tmp_path):
    import re
    from html import unescape
    from urllib.parse import quote

    from test_gui_writes import P, make_world, post

    world = make_world(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    before = world.client.get(f"{P}/storage").json()["data"]
    assert before["evidenceLocation"] == "research"
    unauthorized = world.client.post(f"{P}/storage", json={"ev": "vault"})
    assert unauthorized.status_code == 403
    preview = post(world, "/storage", {"ev": "vault", "dryRun": True})
    assert preview.status_code == 200, preview.text
    plan = preview.json()["data"]
    applied = post(
        world,
        "/storage",
        {"ev": "vault", "dryRun": False, "expectedPlanHash": plan["planHash"]},
    )
    assert applied.status_code == 200, applied.text
    data = applied.json()["data"]
    assert data["evidenceLocation"] == "vault"
    assert data["resultsLocation"] == "research"
    stem = quote(Path(EV_PATH).stem, safe="")
    shown = world.client.get(f"{P}/ev/{stem}?refresh=true").json()["data"]["ev"]
    embeds = re.findall(r'<img[^>]*src="([^"]+)"', shown["html"])
    assert embeds
    for href in embeds + shown["figureUrls"]:
        assert href and world.client.get(unescape(href)).status_code == 200
    source = Path(data["absVaultImagesDir"]) / "o2o_demo.png"
    assert source.is_file()
    known_image = source.read_bytes()
    (source.parent / "authored.png").write_bytes(known_image)
    moved = _move("--ev", "research")
    assert str(source) in moved["archiveImages"]
    assert not source.exists()
    assert (source.parent / "authored.png").read_bytes() == known_image


def test_publish_evidence_preserves_edit_after_storage_change(tmp_path):
    _, project = registered_vault(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    stem = Path(EV_PATH).stem
    path = Path(run("project", "show", "off2on")["data"]["absEvidenceDir"]) / (
        stem + ".md"
    )
    source = path.read_text()
    candidate = tmp_path / "generated.md"
    candidate.write_text(source + "\nGenerated from saved runs.\n")
    cmd = ("ev", "publish", stem, "--from-file", candidate, "--apply", "--no-quiet")
    pending = run(*cmd)
    assert pending["_exit"] == 0 and pending["data"]["pending"], pending
    assert path.read_text() == source
    resolved = run(*cmd, "--expected-sha256", pending["data"]["currentSha256"])
    assert resolved["_exit"] == 0 and not resolved["data"]["pending"], resolved
    _move("--ev", "vault")
    path = project / EV_PATH
    candidate.write_text(source + "\nAnother saved output.\n")
    refreshed = run(*cmd)
    assert refreshed["_exit"] == 0 and not refreshed["data"]["pending"], refreshed
    path.write_text(path.read_text() + "\nWritten in Obsidian.\n")
    candidate.write_text(source + "\nA new generation.\n")
    pending = run(*cmd)
    assert pending["_exit"] == 0 and pending["data"]["pending"], pending
    assert "Written in Obsidian." in path.read_text()
    assert "A new generation." in Path(pending["data"]["proposal"]).read_text()


def test_migrated_ev_exposes_formal_labels_recorded_only_in_sqlite(tmp_path):
    from urllib.parse import quote

    from test_gui_writes import EV_STEM, P, make_world

    from expnote.research.decision_store import DecisionStore
    from expnote.research.workflow import put

    world = make_world(tmp_path)
    assert run("project", "migrate-layout", "off2on", "--apply")["_exit"] == 0
    with open_view("off2on") as view:
        note = view.snapshot.note(EV_STEM.casefold())
        ident = identity("off2on", note)
        previous = view.cache["workflow"][ident]
    with DecisionStore.open("off2on") as store, store.transaction():
        put(
            store.conn,
            ident,
            {
                **previous,
                "fields": {**previous["fields"], "status": "labelled"},
                "sections": {
                    "labels": "## Look-through labels\n\nPreviously accepted reading."
                },
            },
            "legacy-review",
            "user",
            "2026-01-01",
        )
    _move("--ev", "vault")

    ev = world.client.get(f"{P}/ev/{quote(EV_STEM)}").json()["data"]["ev"]
    assert ev["status"] == "labelled"
    assert "Previously accepted reading." in ev["recordedLabelsHtml"]
    assert "Previously accepted reading." not in ev["html"]
    assert "Previously accepted reading." not in world.ev.read_text()
