"""`project init`: vault root detection, scaffolding, reuse, refusals, idempotence."""

from __future__ import annotations

import json
import shutil

from minivault import IMAGES_REL, PROFILE_FIXTURE, build_vault
from writekit import audit_records, edit_profile, invoke, run, set_mtime

from expnote.research import project_init
from expnote.research.profile import read_profile_note
from expnote.research.registry import load_registry, registry_path

PROFILE_NAME = PROFILE_FIXTURE.name


def make_vault(tmp_path, folder="My Demo Project"):
    root = tmp_path / "vault"
    (root / ".obsidian").mkdir(parents=True)
    project = root / "10 Projects" / folder
    project.mkdir(parents=True)
    images = root / "40 Assets" / "Images"
    images.mkdir(parents=True)
    return root, project, images


def init(project, *args, apply=False):
    return run(
        "project",
        "init",
        "--project-dir",
        project,
        *args,
        *(["--apply"] if apply else []),
    )


def tree(root):
    return {
        p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---- vault root detection ----------------------------------------------------


def test_the_nearest_ancestor_with_obsidian_is_the_vault_root(tmp_path):
    outer = tmp_path / "outer"
    (outer / ".obsidian").mkdir(parents=True)
    inner = outer / "nested vault"
    (inner / ".obsidian").mkdir(parents=True)
    project = inner / "a" / "b"
    project.mkdir(parents=True)
    assert project_init.find_vault_root(project) == inner
    assert project_init.find_vault_root(inner) == inner
    assert project_init.find_vault_root(outer) == outer
    assert project_init.find_vault_root(tmp_path) is None


def test_default_id_is_a_slug_of_the_folder_name():
    assert project_init.slugify_id("New Off2On RL") == "new-off2on-rl"
    assert project_init.slugify_id("  --Café_项目 2--  ") == "cafe-2"
    assert project_init.slugify_id("项目") == ""


def test_detection_shows_in_the_dry_run(tmp_path):
    root, project, images = make_vault(tmp_path)
    body = init(project, "--images-dir", images)
    assert body["_exit"] == 4
    data = body["data"]
    assert data["absVaultRoot"] == str(root) and data["project"] == "my-demo-project"
    assert data["name"] == "My Demo Project"


def test_no_vault_above_the_folder_is_exit_2_with_a_remedy(tmp_path):
    folder = tmp_path / "plain" / "folder"
    folder.mkdir(parents=True)
    body = init(folder, "--images-dir", tmp_path)
    assert body["_exit"] == 2 and body["error"]["code"] == "VAULT_ROOT_NOT_FOUND"
    assert "--vault-root" in body["error"]["remediation"]
    assert not registry_path().exists()


def test_an_explicit_vault_root_overrides_detection_but_needs_obsidian(tmp_path):
    root, project, images = make_vault(tmp_path)
    other = tmp_path / "not a vault"
    other.mkdir()
    bad = init(project, "--images-dir", images, "--vault-root", other)
    assert bad["_exit"] == 2 and bad["error"]["code"] == "VAULT_ROOT_NOT_FOUND"
    (other / ".obsidian").mkdir()
    outside = init(project, "--images-dir", images, "--vault-root", other)
    assert outside["error"]["code"] == "USAGE_PATH_OUTSIDE_VAULT"
    ok = init(project, "--images-dir", images, "--vault-root", root)
    assert ok["_exit"] == 4 and ok["data"]["absVaultRoot"] == str(root)


def test_the_project_folder_must_exist(tmp_path):
    root, _, images = make_vault(tmp_path)
    body = init(root / "missing", "--images-dir", images)
    assert body["_exit"] == 2 and body["error"]["code"] == "USAGE_PATH_NOT_FOUND"


# ---- scaffolding a new project -----------------------------------------------


def test_dry_run_shows_the_profile_registry_entry_and_next_commands(tmp_path):
    root, project, images = make_vault(tmp_path)
    before = tree(root)
    result = invoke(
        "project", "init", "--project-dir", project, "--images-dir", images, "--json"
    )
    assert result.exit_code == 4
    data = json.loads(result.stdout)["data"]
    assert data["dryRun"] is True and data["applied"] is False
    assert data["profile"] == {
        "found": False,
        "willCreate": True,
        "projectId": "my-demo-project",
    }
    (change,) = data["changes"]
    assert change["op"] == "create" and change["path"].endswith("/" + PROFILE_NAME)
    assert '+project_id: "my-demo-project"' in change["unifiedDiff"]
    assert '+  dir: "40 Assets/Images"' in change["unifiedDiff"]
    entry = data["registryEntry"]
    assert entry["id"] == "my-demo-project" and entry["project_dir"] == str(project)
    assert entry["images_dir"] == str(images) and entry["vault_root"] == str(root)
    assert data["nextCommands"] == [
        "expnote check --project my-demo-project",
        "expnote web",
    ]
    assert data["confirmCommand"].endswith("--apply")
    assert tree(root) == before and not registry_path().exists()
    assert audit_records("my-demo-project") == []


def test_human_dry_run_lists_the_next_commands(tmp_path):
    root, project, images = make_vault(tmp_path)
    result = invoke("project", "init", "--project-dir", project, "--images-dir", images)
    assert result.exit_code == 4
    for text in (
        "my-demo-project",
        str(root),
        "dry-run: nothing was written",
        "expnote web",
        "expnote check --project my-demo-project",
    ):
        assert text in result.stdout, text


def test_images_dir_is_required_when_the_profile_does_not_name_it(tmp_path):
    root, project, _ = make_vault(tmp_path)
    before = tree(root)
    body = init(project)
    assert body["_exit"] == 2 and body["error"]["code"] == "IMAGES_DIR_REQUIRED"
    assert "--images-dir" in body["error"]["remediation"]
    assert tree(root) == before and not registry_path().exists()


def test_apply_scaffolds_the_profile_registers_and_activates(tmp_path):
    root, project, images = make_vault(tmp_path)
    body = init(project, "--images-dir", images, "--workspace", "w1", apply=True)
    assert body["_exit"] == 0, body
    data = body["data"]
    assert data["applied"] is True and data["profile"]["created"] is True
    profile_path = project / PROFILE_NAME
    assert data["absProfileNote"] == str(profile_path) and profile_path.exists()
    note = read_profile_note(profile_path)
    assert note.parsed and note.profile.project_id == "my-demo-project"
    assert note.profile.decision_id_prefix == "MY-DEMO-PROJECT-DEC"
    assert note.profile.raw["images"]["dir"] == "40 Assets/Images"
    registry = load_registry()
    entry = registry.projects["my-demo-project"]
    assert registry.active_project == "my-demo-project"
    assert entry.project_dir == str(project) and entry.images_dir == str(images)
    assert entry.profile_note == str(profile_path) and entry.workspaces == ["w1"]
    assert entry.vault_root == str(root) and entry.name == "My Demo Project"
    event = audit_records("my-demo-project")[0]["payload"]
    assert event["command"] == "project init" and event["op"] == "create"
    shown = run("project", "show", "my-demo-project")["data"]
    assert shown["profile"]["parsed"] is True and shown["absImagesDir"] == str(images)


def test_the_scaffolded_project_works_end_to_end(tmp_path):
    root, project, images = make_vault(tmp_path)
    init(project, "--images-dir", images, apply=True)
    set_mtime(project / PROFILE_NAME, 3600)
    checked = run("check")
    assert checked["_exit"] == 0 and checked["data"]["summary"]["fail"] == 0
    assert run("doctor")["_exit"] == 0
    made = run("new", "plan", "--id", "P-1", "--title", "First", "--apply")
    assert made["_exit"] == 0
    assert (project / "plans" / "P-1--First.md").exists()
    decided = run(
        "decision",
        "new",
        "--title",
        "Approve P-1",
        "--layer",
        "research",
        "--decided",
        "Approve P-1.",
        "--reason",
        "The user asked for it.",
        "--plans",
        "P-1",
        "--apply",
    )
    assert decided["data"]["id"] == "MY-DEMO-PROJECT-DEC-0001"
    assert run("plan", "list")["data"]["plans"][0]["id"] == "P-1"
    added = run("todo", "add", "first task", "--owner", "agent", "--apply")
    assert added["data"]["id"] == "T-0001"
    assert (project / "ops" / "todos.jsonl").exists()
    assert run("check")["data"]["summary"]["fail"] == 0
    assert run("guide", "research")["_exit"] == 0


def test_profile_note_option_names_where_to_scaffold(tmp_path):
    root, project, images = make_vault(tmp_path)
    target = project / "config" / "profile.md"
    body = init(project, "--images-dir", images, "--profile-note", target, apply=True)
    assert body["_exit"] == 0 and target.exists()
    assert not (project / PROFILE_NAME).exists()
    assert load_registry().projects["my-demo-project"].profile_note == str(target)


def test_new_profile_scaffolds_alone_and_never_overwrites(tmp_path):
    root, project, images = make_vault(tmp_path)
    args = [
        "new",
        "profile",
        "--project-dir",
        project,
        "--id",
        "solo",
        "--name",
        "Solo",
    ]
    dry = invoke(*args, "--json")
    assert dry.exit_code == 4 and not (project / PROFILE_NAME).exists()
    done = run(*args, "--images-dir", images, "--apply")
    assert done["_exit"] == 0
    note = read_profile_note(project / PROFILE_NAME)
    assert note.parsed and note.profile.project_id == "solo"
    assert "# Solo: expnote profile" in (project / PROFILE_NAME).read_text(
        encoding="utf-8"
    )
    before = (project / PROFILE_NAME).read_bytes()
    again = run(*args, "--apply")
    assert again["_exit"] == 5 and again["error"]["code"] == "TARGET_EXISTS"
    assert (project / PROFILE_NAME).read_bytes() == before
    renamed = project / "other-name.md"
    shutil.copy(project / PROFILE_NAME, renamed)
    (project / PROFILE_NAME).unlink()
    assert (
        run(*args, "--apply")["error"]["code"] == "TARGET_EXISTS"
    )  # a profile is there
    assert not registry_path().exists()


# ---- reusing an existing profile ---------------------------------------------


def test_an_existing_profile_is_read_validated_and_reused(tmp_path):
    root, proj = build_vault(tmp_path)
    profile = proj / PROFILE_NAME
    before = profile.read_bytes()
    body = init(proj, "--images-dir", root / IMAGES_REL, apply=True)
    assert body["_exit"] == 0, body
    # no --id: the profile's project_id wins over the folder slug ("demo")
    assert body["data"]["project"] == "off2on"
    assert body["data"]["profile"] == {
        "found": True,
        "willCreate": False,
        "projectId": "off2on",
        "created": False,
    }
    assert profile.read_bytes() == before
    assert audit_records("off2on") == []  # nothing in the vault was written
    assert run("check")["_exit"] == 0


def test_the_profile_project_id_must_equal_the_given_id(tmp_path):
    root, proj = build_vault(tmp_path)
    body = init(proj, "--images-dir", root / IMAGES_REL, "--id", "demo")
    assert body["_exit"] == 2 and body["error"]["code"] == "PROFILE_ID_MISMATCH"
    assert "--id off2on" in body["error"]["remediation"]
    assert not registry_path().exists()


def test_an_invalid_profile_is_reported_not_overwritten(tmp_path):
    root, proj = build_vault(tmp_path)
    profile = proj / PROFILE_NAME
    profile.write_text(
        profile.read_text(encoding="utf-8").replace("headings:", "headings_x:", 1),
        encoding="utf-8",
    )
    before = profile.read_bytes()
    body = init(proj, "--images-dir", root / IMAGES_REL)
    assert body["_exit"] == 6 and body["error"]["code"] == "PROFILE_INVALID"
    assert body["error"]["details"]["problems"]
    assert profile.read_bytes() == before


def test_images_dir_can_come_from_the_profile(tmp_path):
    root, proj = build_vault(tmp_path)
    edit_profile(proj, "images:\n", f'images:\n  dir: "{IMAGES_REL}"\n')
    body = init(proj, apply=True)
    assert body["_exit"] == 0
    assert load_registry().projects["off2on"].images_dir == str(root / IMAGES_REL)


def test_two_profile_notes_need_an_explicit_choice(tmp_path):
    root, proj = build_vault(tmp_path)
    copy = proj / "second profile.md"
    shutil.copy(proj / PROFILE_NAME, copy)
    body = init(proj, "--images-dir", root / IMAGES_REL)
    assert body["_exit"] == 2 and body["error"]["code"] == "AMBIGUOUS_PROFILE"
    chosen = init(
        proj,
        "--images-dir",
        root / IMAGES_REL,
        "--profile-note",
        proj / PROFILE_NAME,
        apply=True,
    )
    assert chosen["_exit"] == 0
    assert load_registry().projects["off2on"].profile_note == str(proj / PROFILE_NAME)


# ---- everything must be inside the vault -------------------------------------


def test_paths_outside_the_vault_are_refused(tmp_path):
    root, project, images = make_vault(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = tree(root)
    body = init(project, "--images-dir", elsewhere)
    assert body["_exit"] == 2 and body["error"]["code"] == "USAGE_PATH_OUTSIDE_VAULT"
    assert "--images-dir" in body["error"]["message"]
    body = init(project, "--images-dir", images, "--profile-note", elsewhere / "p.md")
    assert body["_exit"] == 2 and body["error"]["code"] == "USAGE_PATH_OUTSIDE_VAULT"
    assert "--profile-note" in body["error"]["message"]
    other_project = tmp_path / "other"
    other_project.mkdir()
    body = init(other_project, "--images-dir", images, "--vault-root", root)
    assert body["error"]["code"] == "USAGE_PATH_OUTSIDE_VAULT"
    assert tree(root) == before and not registry_path().exists()


# ---- idempotence and conflicts -----------------------------------------------


def test_running_init_again_is_a_no_op_that_reports_the_binding(tmp_path):
    root, project, images = make_vault(tmp_path)
    first = init(project, "--images-dir", images, apply=True)
    assert first["_exit"] == 0
    snapshot, registry_text = tree(root), registry_path().read_text(encoding="utf-8")
    events = len(audit_records("my-demo-project"))
    for flags in ([], ["--apply"]):
        again = init(project, "--images-dir", images, *flags)
        assert again["_exit"] == 0, again
        data = again["data"]
        assert data["alreadyRegistered"] is True and data["changes"] == []
        assert data["project"] == "my-demo-project" and data["vaultOk"] is True
        assert data["absProjectDir"] == str(project) and data["absImagesDir"] == str(
            images
        )
    assert tree(root) == snapshot
    assert registry_path().read_text(encoding="utf-8") == registry_text
    assert len(audit_records("my-demo-project")) == events
    human = invoke("project", "init", "--project-dir", project)
    assert human.exit_code == 0 and "already registered" in human.stdout


def test_a_bound_folder_cannot_be_re_registered_under_another_id(tmp_path):
    root, project, images = make_vault(tmp_path)
    init(project, "--images-dir", images, apply=True)
    body = init(project, "--id", "different")
    assert body["_exit"] == 2 and body["error"]["code"] == "PROJECT_EXISTS"


def test_a_second_project_keeps_the_first_active_and_ids_stay_unique(tmp_path):
    root, project, images = make_vault(tmp_path)
    init(project, "--images-dir", images, apply=True)
    second = root / "10 Projects" / "Second"
    second.mkdir()
    ok = init(second, "--images-dir", images, apply=True)
    assert ok["_exit"] == 0 and ok["data"]["activeProject"] == "my-demo-project"
    registry = load_registry()
    assert set(registry.projects) == {"my-demo-project", "second"}
    assert (
        registry.projects["second"].vault_id
        == registry.projects["my-demo-project"].vault_id
    )
    third = root / "10 Projects" / "Third"
    third.mkdir()
    taken = init(third, "--images-dir", images, "--id", "second")
    assert taken["_exit"] == 2 and taken["error"]["code"] == "PROJECT_ID_TAKEN"
    assert run("project", "list")["data"]["projects"][0]["active"] is True


def test_bad_ids_and_unslugable_folders_ask_for_an_explicit_id(tmp_path):
    root, _, images = make_vault(tmp_path)
    cjk = root / "10 Projects" / "项目"
    cjk.mkdir()
    body = init(cjk, "--images-dir", images)
    assert body["_exit"] == 2 and "--id" in body["error"]["remediation"]
    bad = init(cjk, "--images-dir", images, "--id", "has space")
    assert bad["error"]["code"] == "USAGE_BAD_ID"
    ok = init(cjk, "--images-dir", images, "--id", "cjk-project", apply=True)
    assert ok["_exit"] == 0 and ok["data"]["name"] == "项目"


def test_a_missing_images_folder_is_only_a_warning(tmp_path):
    root, project, images = make_vault(tmp_path)
    shutil.rmtree(images)
    body = init(project, "--images-dir", images)
    assert body["_exit"] == 4
    assert body["warnings"][0]["code"] == "IMAGES_DIR_MISSING"
    assert body["data"]["imagesDirExists"] is False


# ---- documentation -----------------------------------------------------------


def test_help_and_guide_make_init_the_first_step():
    help_text = invoke("project", "--help").stdout
    assert "project init" in help_text and "First step" in help_text
    assert invoke("project", "init", "--help").exit_code == 0
    guide = invoke("guide", "research").stdout
    assert guide.index("expnote project init") < guide.index("expnote new plan")
    assert "Workflow: first time" in guide
