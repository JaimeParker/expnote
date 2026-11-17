"""Version-bound execution, evidence and result gates for Research plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from expnote.research import entities
from expnote.research.errors import ResearchError
from expnote.research.index import linked_workspaces
from expnote.research.layout import atomic_bytes
from expnote.research.runs import join_runs
from expnote.research.workflow import digest, identity, persist_revision
from expnote.research.writes import WritePlan, _gate, gate_failed

OPEN = {"approved", "running", "read-out", "confirmed"}


def state_of(view, note):
    return view.cache.get("workflow", {}).get(identity(view.project_id, note), {})


def flow_of(view, page):
    return state_of(view, page).get("lifecycle") if page else None


def strict(view, page):
    return (
        page is not None
        and view.context.entry.layout == "split"
        and (page.get("status") == "draft" or flow_of(view, page) is not None)
    )


def require(ok, code, message):
    if not ok:
        raise gate_failed(message, [_gate("FLOW." + code, message)])


def approved(view, page):
    flow = flow_of(view, page)
    require(
        flow and page.get("status") in OPEN,
        "APPROVAL",
        "Approve this PLAN version first.",
    )
    return flow


def _bad(message):
    raise ResearchError("WORKFLOW_INVALID", message, exit_code=2)


def _object(value, name):
    if not isinstance(value, dict):
        _bad(f"{name} must be an object")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        _bad(f"{name} needs text")
    return value.strip()


def _keys(value, allowed, name):
    extra = value.keys() - allowed
    if extra:
        _bad(f"Unknown {name} fields: {', '.join(sorted(extra))}")


def _names(value, name, *, empty=False):
    if not isinstance(value, list) or (not value and not empty):
        _bad(f"{name} must be a list of names")
    names = [_text(item, name) for item in value]
    if len(names) != len(set(names)):
        _bad(f"{name} contains duplicates")
    return names


def _choices(value, allowed, name):
    mapping = _object(value, name)
    for ident, action in mapping.items():
        if not isinstance(action, str) or action not in allowed:
            _bad(f"{name}.{ident} must be one of {', '.join(sorted(allowed))}")
    return mapping


def read_json(path: Path):
    try:
        return _object(json.loads(path.read_text(encoding="utf-8-sig")), str(path))
    except (OSError, ValueError) as error:
        _bad(str(error))


def _contract(spec):
    _keys(spec, {"deliverables", "impact", "reopen"}, "workflow")
    if "reopen" in spec and not isinstance(spec["reopen"], bool):
        _bad("reopen must be true or false")
    items = spec.get("deliverables")
    if not isinstance(items, list) or not items:
        _bad("declare at least one deliverable")
    deliverables = {}
    for item in items:
        item = _object(item, "deliverable")
        _keys(
            item,
            {"id", "question", "files", "criteria", "review", "condition"},
            "deliverable",
        )
        ident = _text(item.get("id"), "deliverable id")
        if ident in deliverables:
            _bad(f"duplicate deliverable {ident}")
        if item.get("review") not in ("user", "agent"):
            _bad(f"{ident}.review must be user or agent")
        deliverables[ident] = {
            "question": _text(item.get("question"), f"{ident}.question"),
            "files": _names(item.get("files"), f"{ident}.files"),
            "criteria": _text(item.get("criteria"), f"{ident}.criteria"),
            "review": item["review"],
            **(
                {"condition": _text(item["condition"], f"{ident}.condition")}
                if "condition" in item
                else {}
            ),
        }
    if all("condition" in item for item in deliverables.values()):
        _bad("declare at least one unconditional deliverable")
    return deliverables


def _member(view, page, kind, ident):
    note = entities.find_by_id(view, kind, ident)
    if view.snapshot.plan_page_of(note) != page:
        _bad(f"{ident} belongs to a different PLAN")
    return note


def inventory(view, page):
    """State visible to the approver, including running stages and their run IDs."""
    notes = entities.lifecycle_members(view, page)
    return {
        identity(view.project_id, note): state_of(view, note)
        for note in notes
        if note.type in {"exec-plan", "evidence", "result"}
    }


def _fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def inventory_fingerprint(members):
    return _fingerprint(
        {
            ident: {k: v for k, v in state.items() if k != "publication"}
            for ident, state in members.items()
        }
    )


def _criteria(deliverables, names):
    return _fingerprint(
        {
            name: {
                key: deliverables[name].get(key)
                for key in ("question", "criteria", "review", "condition")
            }
            for name in names
        }
    )


def _needs_user(flow, receipt):
    return any(
        flow["deliverables"][name]["review"] == "user" for name in receipt["outputs"]
    )


def _output_path(view, path):
    base = view.context.resolved.material_dir.resolve()
    target = (base / path).resolve()
    if (
        Path(path).is_absolute()
        or not target.is_relative_to(base)
        or not target.is_file()
    ):
        _bad(f"Expected a file inside the Research project: {path}")
    return target


def _hash_file(path):
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise ResearchError("EVIDENCE_FILE", str(error), exit_code=1) from None


def _receipt(view, page, note, exec_id, outputs, deliverables):
    from expnote.research.storage_media import ImageCopies, is_image
    from expnote.research.storage_paths import file_key
    from expnote.research.textkit import iter_links

    resolved = view.context.resolved

    if not outputs or outputs.keys() - deliverables.keys():
        _bad("evidence must cover declared deliverable IDs")
    material = view.context.resolved.material_dir.resolve()
    appendix = view.snapshot.note(note.link_field("appendix") or "")
    require(
        appendix
        and appendix.type == "evidence-appendix"
        and appendix.link_field("parent") == note.cstem
        and view.snapshot.plan_page_of(appendix) == page,
        "EV_APPENDIX",
        f"Register the appendix of {note.stem} with this PLAN first.",
    )
    if note.link_field("exec_plan"):
        stage = view.snapshot.note(note.link_field("exec_plan"))
        linked = identity(view.project_id, stage) if stage else None
        require(
            linked == exec_id,
            "EV_STAGE",
            f"{note.stem} belongs to a different execution stage.",
        )
    files = {}
    revisions = {}
    attachment_paths = {
        name.casefold(): path for name, path in view.snapshot.attachment_paths.items()
    }
    image_files = ImageCopies(resolved, view.snapshot.attachment_paths.values())
    for doc in (note, appendix):
        from expnote.research.storage_paths import document_dir

        doc_path = doc.path.resolve()
        directory = document_dir(
            resolved, view.profile, "ev" if doc is note else "appendix"
        )
        require(
            doc_path.is_relative_to(directory.resolve()),
            "EV_PATH",
            f"Unexpected path for {doc.stem}.",
        )
        raw = doc_path.read_bytes()
        sha = digest(raw)
        files[file_key(resolved, doc.path)] = sha
        revisions[identity(view.project_id, doc)] = sha
        text = raw.decode("utf-8-sig")
        image_files.rewrite(text, doc.path, resolved.entry.storage("ev"))
        for image, asset_sha in image_files.referenced(doc.path).items():
            files[file_key(resolved, image)] = asset_sha
        figures = doc.get("figures") or []
        targets = set(figures) if isinstance(figures, list) else set()
        targets.update(
            target.split("#", 1)[0] for embed, target in iter_links(text) if embed
        )
        for target in sorted(targets):
            if is_image(target):
                if asset := image_files.find(target, doc.path):
                    files[file_key(resolved, asset)] = _hash_file(asset)
                    continue
            paths = [
                attachment_paths.get(hit.casefold())
                for hit in view.snapshot.resolve(target)
            ]
            assets = [
                path.resolve()
                for path in paths
                if path and path.resolve().is_relative_to(material)
            ]
            if assets:
                require(
                    len(set(assets)) == 1,
                    "EV_ASSET",
                    f"Ambiguous evidence attachment: {target}",
                )
                asset = assets[0]
                files[file_key(resolved, asset)] = _hash_file(asset)
            elif target in figures:
                require(False, "EV_ASSET", f"Missing Research figure: {target}")
    for name, entries in outputs.items():
        entries = _object(entries, f"outputs.{name}")
        if entries.keys() - set(deliverables[name]["files"]):
            _bad(f"undeclared files for {name}")
        for file in entries.values():
            target = _output_path(view, _text(file, "output path"))
            files[target.relative_to(material).as_posix()] = _hash_file(target)
    runs = sorted(
        {
            run
            for doc in (note, appendix)
            if isinstance(doc.get("runs"), list)
            for run in doc.get("runs")
            if isinstance(run, str) and run
        }
    )
    return {
        "exec": exec_id,
        "outputs": outputs,
        "files": files,
        "revisions": revisions,
        "runs": runs,
        "criteria": _criteria(deliverables, outputs),
    }


def _file_checks(view, receipt, *, draft=True):
    from expnote.research.storage import mapped_file, moved_files

    resolved = view.context.resolved
    equivalents = moved_files(resolved, receipt["revisions"])
    drafts = {}
    if draft:
        for ident, revision in receipt["revisions"].items():
            entry = view.cache["workflow"].get(ident, {}).get("labelDraft", {})
            if entry.get("revision") == revision:
                drafts[revision] = (entry["path"], entry["sha"])
    return dict(
        mapped_file(resolved, *drafts.get(sha, (name, sha)), equivalents)
        for name, sha in receipt["files"].items()
    )


def _fresh(view, receipt):
    try:
        return all(
            _hash_file(path) == sha for path, sha in _file_checks(view, receipt).items()
        )
    except ResearchError:
        return False


def _save_evidence(view, note, receipt, old, *, reset, apply):
    """Keep the previous labels/snapshot in the workflow event history."""
    ident = identity(view.project_id, note)
    revision = receipt["revisions"][ident]
    if apply:
        base = view.context.resolved.material_dir
        for path, sha in _file_checks(view, receipt, draft=False).items():
            raw = path.read_bytes()
            if digest(raw) != sha:
                raise ResearchError("CONFLICT", f"{path} changed", exit_code=5)
            if path.suffix.casefold() == ".md":
                persist_revision(view.project_id, raw)
            else:
                snapshot = base / "revisions" / "assets" / sha
                if not snapshot.exists():
                    atomic_bytes(snapshot, raw)
                if _hash_file(snapshot) != sha:
                    raise ResearchError(
                        "REVISION_INVALID", f"bad snapshot {snapshot}", exit_code=6
                    )
    archived = dict(old.get("archivedEvidence", {}))
    archived[revision] = receipt["files"]
    if reviewed := old.get("reviewedEvidenceRevision"):
        archived[reviewed] = old.get("archivedEvidence", {}).get(
            old.get("revision"), {}
        )
    state = {**old, "revision": revision, "archivedEvidence": archived}
    previous = old.get("revision")
    if previous and previous != revision:
        state["history"] = list(dict.fromkeys([*old.get("history", []), previous]))
    if reset:
        state.pop("labelDraft", None)
        state.pop("reviewedSourceSha", None)
        state["fields"] = {"status": "rendered"}
        state["sections"] = {}
        state["reviewedEvidence"] = None
        if reviewed := old.get("reviewedEvidenceRevision"):
            state["history"] = list(
                dict.fromkeys([*state.get("history", []), reviewed])
            )
        state["reviewedEvidenceRevision"] = None
    return state


def proposal(view, page, spec):
    """Validate the plan's requirements and explicit carry-forward choices."""
    deliverables = _contract(spec)
    old = flow_of(view, page)
    base = inventory(view, page)
    impact = _object(spec.get("impact", {}), "impact")
    _keys(impact, {"exec", "running", "evidence", "result"}, "impact")
    if page.get("status") != "draft" and "impact" not in spec:
        _bad("a revision or existing-plan enrollment needs an impact list")
    stages = _choices(
        impact.get("exec", {}), {"reuse", "redo", "history"}, "impact.exec"
    )
    running = _choices(impact.get("running", {}), {"finish", "stop"}, "impact.running")
    result = impact.get("result")
    if result not in (None, "reuse-body", "rewrite"):
        _bad("impact.result must be reuse-body or rewrite")
    result_notes = [
        n
        for n in view.snapshot.current_plan_members(str(page.get("id")))
        if n.type == "result" and n.get("status") not in {"superseded", "withdrawn"}
    ]
    if result_notes and not result:
        _bad("record how to handle the existing RES in impact.result")
    active = {
        ident
        for ident, state in base.items()
        if state.get("fields", {}).get("status") == "running"
        and (
            not old
            or ident not in old.get("running", {})
            or ident in old.get("carry", {})
        )
    }
    if running.keys() != active:
        _bad(
            f"decide finish or stop for each running stage: {', '.join(sorted(active))}"
        )

    carry = {}
    for ident, action in stages.items():
        note = _member(view, page, "exec-plan", ident)
        if identity(view.project_id, note) != ident:
            _bad(f"use the stable execution ID for {ident}")
        if action == "reuse":
            stage = base[ident]
            require(
                stage.get("revision")
                and note.get("status") in {"frozen", "running", "done"}
                and running.get(ident) != "stop",
                "EXEC_REUSE",
                f"{ident} has no eligible frozen execution to carry forward.",
            )
            source = _hash_file(note.path)
            pinned = (old or {}).get("carry", {}).get(ident, {})
            require(
                source == stage["revision"]
                or source == pinned.get("source")
                or stage.get("origin") == "imported-baseline",
                "EXEC_CHANGED",
                f"{ident} changed after freezing. Replace it in the new version.",
            )
            carry[ident] = {"revision": stage["revision"], "source": source}
    evidence = _object(impact.get("evidence", {}), "impact.evidence")
    receipts = {}
    for ident, instruction in evidence.items():
        note = _member(view, page, "evidence", ident)
        if identity(view.project_id, note) != ident:
            _bad(f"use the stable evidence ID for {ident}")
        if instruction == "history":
            continue
        prior = (old or {}).get("evidence", {}).get(ident)
        if isinstance(instruction, dict):
            _keys(
                instruction, {"action", "exec", "outputs"}, f"impact.evidence.{ident}"
            )
            if instruction.get("action") != "review":
                _bad("new evidence bindings need action=review")
            receipt = _receipt(
                view,
                page,
                note,
                _text(instruction.get("exec"), "evidence exec"),
                _object(instruction.get("outputs"), "evidence outputs"),
                deliverables,
            )
            action = "review"
        else:
            action = instruction
            if action not in ("reuse", "review") or not prior:
                _bad(f"{ident} needs an existing receipt or explicit exec and outputs")
            receipt = _receipt(
                view, page, note, prior["exec"], prior["outputs"], deliverables
            )
        _member(view, page, "exec-plan", receipt["exec"])
        if action == "reuse" and prior != receipt:
            require(
                False,
                "EV_REVIEW",
                f"Re-review {ident} because its evidence or criteria changed.",
            )
        receipts[ident] = receipt
    return {
        "deliverables": deliverables,
        "impact": {
            "exec": stages,
            "running": running,
            "evidence": evidence,
            "result": result,
        },
        "carry": carry,
        "evidence": receipts,
        "reopen": spec.get("reopen", False),
        "basis": inventory_fingerprint(base),
        "baseRevision": state_of(view, page).get("revision"),
        "baseApproval": (old or {}).get("approval"),
        "runningDetails": {
            ident: sorted(base[ident].get("start", {}).get("runs", {}))
            for ident in running
        },
    }


def activate(view, page, proposed, number, revision, apply):
    """Build the new active version from the approved submission."""
    pid = str(page.get("id"))
    changes = {}
    for ident, carried in proposed["carry"].items():
        note = _member(view, page, "exec-plan", ident)
        if _hash_file(note.path) != carried["source"]:
            raise ResearchError(
                "CONFLICT", f"{ident} changed since submission", exit_code=5
            )
    for ident, receipt in proposed["evidence"].items():
        note = _member(view, page, "evidence", ident)
        if not _fresh(view, receipt):
            raise ResearchError(
                "CONFLICT", f"{ident} changed since submission", exit_code=5
            )
        changes[ident] = _save_evidence(
            view,
            note,
            receipt,
            state_of(view, note),
            reset=proposed["impact"]["evidence"][ident] != "reuse",
            apply=apply,
        )
    for note in view.snapshot.current_plan_members(pid):
        if note.type != "result" or note.get("status") in {"withdrawn", "superseded"}:
            continue
        old = state_of(view, note)
        history = list(old.get("history", []))
        if old.get("revision") and old["revision"] not in history:
            history.append(old["revision"])
        changes[identity(view.project_id, note)] = {
            **old,
            "fields": {"status": "draft"},
            "sections": {},
            "revision": None,
            "resultSubmission": None,
            "reviewedResult": None,
            "history": history,
        }
        if proposed["impact"]["result"] == "rewrite":
            changes[identity(view.project_id, note)]["rewriteFrom"] = _body_hash(
                view, note
            )
        else:
            changes[identity(view.project_id, note)]["rewriteFrom"] = None
    old_flow = flow_of(view, page) or {}
    return {
        "approval": number,
        "revision": revision,
        "deliverables": proposed["deliverables"],
        "carry": proposed["carry"],
        "evidence": proposed["evidence"],
        "running": {**old_flow.get("running", {}), **proposed["impact"]["running"]},
        "checklist": {},
        "conditions": {},
    }, changes


def _plan_update(view, page, flow, **data):
    ident = identity(view.project_id, page)
    return WritePlan(
        [], {"id": ident, **data}, workflow_updates={ident: {"lifecycle": flow}}
    )


def _exec_state(view, page, ident, flow):
    note = _member(view, page, "exec-plan", ident)
    state = state_of(view, note)
    belongs = state.get("execApproval") == flow["approval"]
    carried = flow["carry"].get(ident, {}).get("revision") == state.get("revision")
    require(
        state.get("revision") and (belongs or carried),
        "EXEC_VERSION",
        f"{ident} is not frozen for the current approved PLAN.",
    )
    return note, state


def _requested_stage(flow, exec_id):
    step = flow["checklist"].get(exec_id)
    require(step, "STAGE_MISSING", f"{exec_id} is absent from the frozen checklist.")
    return step


def _active_stage(step, flow):
    return any(not _skipped(flow, name) for name in step["deliverables"])


def _skipped(flow, deliverable):
    return flow["conditions"].get(deliverable, {}).get("triggered") is False


def plan_checklist(view, plan_id, source):
    from graphlib import CycleError, TopologicalSorter

    page = entities.find_plan(view, plan_id)
    flow = approved(view, page)
    require(
        page.get("status") in {"approved", "running"},
        "PLAN_STATE",
        "Freeze the checklist before submitting the result.",
    )
    steps = {}
    coverage = set()
    for exec_id, row in source.items():
        row = _object(row, exec_id)
        _keys(row, {"deliverables", "after"}, exec_id)
        deliverables = _names(row.get("deliverables"), f"{exec_id}.deliverables")
        after = _names(row.get("after", []), f"{exec_id}.after", empty=True)
        if set(deliverables) - flow["deliverables"].keys():
            _bad(f"{exec_id} lists an unapproved deliverable")
        note, state = _exec_state(view, page, exec_id, flow)
        require(
            note.get("status") in {"frozen", "running", "done"},
            "EXEC_UNFROZEN",
            f"Freeze {exec_id} first.",
        )
        source = flow["carry"].get(exec_id, {}).get("source", state["revision"])
        require(
            _hash_file(note.path) == source,
            "EXEC_CHANGED",
            f"{exec_id} changed since freeze.",
        )
        steps[exec_id] = {
            "deliverables": deliverables,
            "after": after,
            "revision": state["revision"],
            "source": source,
        }
        coverage.update(deliverables)
    missing = flow["deliverables"].keys() - coverage
    require(
        not missing,
        "CHECKLIST_COVERAGE",
        f"Missing deliverables: {', '.join(sorted(missing))}",
    )
    for step in steps.values():
        if set(step["after"]) - steps.keys():
            _bad("checklist prerequisites must be in the checklist")
    for receipt in flow["evidence"].values():
        step = steps.get(receipt["exec"], {})
        if step and receipt["outputs"].keys() - set(step["deliverables"]):
            _bad(f"checklist must include approved evidence from {receipt['exec']}")
    try:
        list(
            TopologicalSorter(
                {key: step["after"] for key, step in steps.items()}
            ).static_order()
        )
    except CycleError:
        _bad("checklist prerequisites form a cycle")
    if flow["checklist"]:
        frozen = flow["checklist"]
        if frozen == steps:
            return WritePlan([], {"id": plan_id, "checklist": steps})
        require(
            page.get("status") == "approved"
            or all(steps.get(ident) == step for ident, step in frozen.items()),
            "CHECKLIST_FROZEN",
            "Keep the frozen stages while running. A PLAN revision can replace them.",
        )
    plan = _plan_update(view, page, {**flow, "checklist": steps}, checklist=steps)
    plan.workflow_reads.update(steps)
    plan.workflow_files.update(
        {
            _member(view, page, "exec-plan", ident).path: step["source"]
            for ident, step in steps.items()
        }
    )
    return plan


def create_exec(view, page, plan):
    if strict(view, page):
        approved(view, page)
        plan.workflow_reads.add(identity(view.project_id, page))
    return plan


def freeze_exec(view, note, plan):
    page = view.snapshot.plan_page_of(note)
    if not strict(view, page):
        return plan
    flow = approved(view, page)
    require(
        page.get("status") in {"approved", "running"},
        "PLAN_STATE",
        "Freeze execution before the result is submitted.",
    )
    ident = identity(view.project_id, note)
    before = state_of(view, note)
    if note.get("status") != "draft":
        require(
            plan.data["matchesPrevious"]
            or (
                note.get("status") == "frozen"
                and ident not in flow["checklist"]
                and before.get("execApproval") == flow["approval"]
            ),
            "EXEC_FROZEN",
            f"Use a new execution ID to replace {ident} in a PLAN revision.",
        )
        if plan.data["matchesPrevious"]:
            return plan
    plan.workflow_reads.add(identity(view.project_id, page))
    plan.workflow_updates[ident] = {
        "execApproval": flow["approval"],
        "planRevision": flow["revision"],
    }
    return plan


def _frozen_step(view, page, ident, flow):
    step = _requested_stage(flow, ident)
    note, state = _exec_state(view, page, ident, flow)
    sha = _hash_file(note.path)
    require(
        sha == step["source"] and step["revision"] == state["revision"],
        "EXEC_CHANGED",
        f"{ident} changed since the checklist was frozen.",
    )
    return note, state, step


def _runs(view, run_ids, *, terminal=None):
    if not run_ids:
        return {}
    rows = join_runs(linked_workspaces(view.context), set(run_ids)).runs
    found = {row.id: row for row in rows}
    for ident in run_ids:
        row = found.get(ident)
        require(
            row, "RUN_UNKNOWN", f"Run {ident} is absent from the linked workspaces."
        )
        if terminal is True:
            require(
                row.status in {"finished", "failed"},
                "RUN_INCOMPLETE",
                f"Run {ident} is {row.status}.",
            )
        if terminal is False:
            require(
                row.status in {"planned", "running"},
                "RUN_ALREADY_ENDED",
                f"Run {ident} already ended. Carry the original evidence forward.",
            )
    return {ident: found[ident].workspace for ident in run_ids}


def _all_receipts(flow, exec_id):
    return {
        key: item for key, item in flow["evidence"].items() if item["exec"] == exec_id
    }


def change_exec(view, note, to, runs, plan):
    page = view.snapshot.plan_page_of(note)
    if not strict(view, page):
        if runs:
            _bad("--run needs an enrolled PLAN")
        return plan
    flow = flow_of(view, page)
    require(flow, "APPROVAL", "Approve this PLAN version first.")
    ident = identity(view.project_id, note)
    pid = identity(view.project_id, page)
    state = state_of(view, note)
    plan.workflow_reads.add(pid)
    if runs and to != "running":
        _bad("--run is used with --to running")
    if to == "running":
        approved(view, page)
        require(
            page.get("status") == "running",
            "PLAN_NOT_RUNNING",
            "Advance the PLAN to running first.",
        )
        _, _, step = _frozen_step(view, page, ident, flow)
        require(_active_stage(step, flow), "STAGE_SKIPPED", f"{ident} did not trigger.")
        conditional = {
            name
            for name in step["deliverables"]
            if "condition" in flow["deliverables"][name]
        }
        if conditional == set(step["deliverables"]):
            require(
                conditional <= flow["conditions"].keys(),
                "CONDITION_PENDING",
                f"Record the trigger outcome for {ident} first.",
            )
        plan.workflow_files[note.path] = step["source"]
        for dependency in step["after"]:
            parent, _, previous = _frozen_step(view, page, dependency, flow)
            require(
                parent.get("status") == "done" or not _active_stage(previous, flow),
                "DEPENDENCY",
                f"Finish {dependency} before starting {ident}.",
            )
            plan.workflow_reads.add(dependency)
            plan.workflow_files[parent.path] = previous["source"]
        start = state.get("start", {})
        already_running = note.get("status") == "running"
        if already_running and not runs:
            return plan
        if already_running:
            require(
                start.get("approval") == flow["approval"],
                "EXEC_VERSION",
                "Register new runs on a stage started under this PLAN version.",
            )
        new_runs = set(runs) - start.get("runs", {}).keys()
        others = {
            run
            for key, value in inventory(view, page).items()
            if key != ident
            for run in value.get("start", {}).get("runs", {})
        }
        others.update(
            run for receipt in flow["evidence"].values() for run in receipt["runs"]
        )
        require(
            not others.intersection(new_runs),
            "RUN_BOUND",
            "A run already belongs to another stage.",
        )
        additions = _runs(view, sorted(new_runs), terminal=False)
        pinned = {**start.get("runs", {}), **additions}
        plan.workflow_updates[ident] = {
            "start": {
                "approval": flow["approval"],
                "planRevision": flow["revision"],
                "execRevision": step["revision"],
                "runs": pinned,
            }
        }
        plan.workflow_reads.update(inventory(view, page))
        plan.data["runBinding"] = plan.workflow_updates[ident]["start"]
    elif to == "done" and note.get("status") != "done":
        included = ident in flow["checklist"] or ident in flow["carry"]
        if not included:
            require(
                flow["running"].get(ident) != "stop",
                "STOP_STAGE",
                f"Record {ident} as aborted after stopping it.",
            )
            return plan
        require(
            page.get("status") == "running",
            "PLAN_NOT_RUNNING",
            "Advance the PLAN to running first.",
        )
        _, _, step = _frozen_step(view, page, ident, flow)
        plan.workflow_files[note.path] = step["source"]
        receipts = _all_receipts(flow, ident)
        require(
            receipts,
            "STAGE_NO_EVIDENCE",
            f"Register evidence from {ident} before marking it done.",
        )
        for ev_id, receipt in receipts.items():
            require(
                _fresh(view, receipt),
                "EVIDENCE_CHANGED",
                f"Register the current version of {ev_id}.",
            )
            plan.workflow_files.update(_file_checks(view, receipt))
        all_runs = set(state.get("start", {}).get("runs", {}))
        all_runs.update(run for receipt in receipts.values() for run in receipt["runs"])
        plan.workflow_updates[ident] = {
            "completedRuns": _runs(view, sorted(all_runs), terminal=True)
        }
    return plan


def register_evidence(view, ident, exec_id, outputs, apply):
    note = entities.find_by_id(view, "evidence", ident)
    ident = identity(view.project_id, note)
    page = view.snapshot.plan_page_of(note)
    flow = approved(view, page)
    require(
        page.get("status") in {"running", "read-out", "confirmed"},
        "PLAN_NOT_RUNNING",
        "Advance the PLAN to running before registering its evidence.",
    )
    exec_note, exec_state, step = _frozen_step(view, page, exec_id, flow)
    require(_active_stage(step, flow), "STAGE_SKIPPED", f"{exec_id} did not trigger.")
    require(
        exec_note.get("status") in {"running", "done"},
        "EXEC_NOT_STARTED",
        f"Start {exec_id} before registering its evidence.",
    )
    if outputs.keys() - set(step["deliverables"]):
        _bad(f"evidence covers deliverables outside {exec_id}")
    for name in outputs:
        if "condition" in flow["deliverables"][name]:
            require(
                flow["conditions"].get(name, {}).get("triggered"),
                "CONDITION_PENDING",
                f"Record that {name} was triggered first.",
            )
    receipt = _receipt(view, page, note, exec_id, outputs, flow["deliverables"])
    runs = set(receipt["runs"])
    if exec_state.get("start"):
        require(
            runs <= exec_state["start"]["runs"].keys(),
            "RUN_NOT_BOUND",
            "Bind every cited run to the stage before registering evidence.",
        )
    elif runs:
        require(
            exec_id in flow["carry"], "RUN_NOT_BOUND", "No run binding for this stage."
        )
        _runs(view, sorted(runs))
    current = flow["evidence"].get(ident)
    if current == receipt:
        return WritePlan([], {"id": ident, "evidence": receipt})
    require(
        current is None or current["exec"] == exec_id,
        "EV_STAGE",
        f"{ident} is already registered to {current['exec'] if current else exec_id}.",
    )
    require(
        page.get("status") != "confirmed" or current is not None,
        "RESULT_CONFIRMED",
        "Submit a PLAN revision to add evidence to this confirmed result.",
    )
    state = _save_evidence(
        view, note, receipt, state_of(view, note), reset=True, apply=apply
    )
    updated = {**flow, "evidence": {**flow["evidence"], ident: receipt}}
    # Recheck any conditional disposition whose supporting evidence just changed.
    updated["conditions"] = {
        key: row
        for key, row in flow["conditions"].items()
        if ident not in row["evidence"]
    }
    plan = _plan_update(view, page, updated, evidence=receipt)
    plan.workflow_updates[ident] = state
    plan.workflow_reads.add(exec_id)
    plan.workflow_files = _file_checks(view, receipt)
    plan.workflow_files[exec_note.path] = step["source"]
    return plan


def record_condition(view, plan_id, deliverable, reason, evidence, *, triggered):
    page = entities.find_plan(view, plan_id)
    flow = approved(view, page)
    require(
        page.get("status") == "running",
        "PLAN_NOT_RUNNING",
        "Advance the PLAN to running first.",
    )
    item = flow["deliverables"].get(deliverable, {})
    require(
        item.get("condition"),
        "REQUIRED_DELIVERABLE",
        f"{deliverable} is not a conditional deliverable.",
    )
    reason = _text(reason, "condition outcome")
    checks = {}
    reads = set()
    for ident in _names(evidence, "condition evidence"):
        receipt = flow["evidence"].get(ident)
        require(
            receipt and deliverable not in receipt["outputs"] and _fresh(view, receipt),
            "CONDITION_EVIDENCE",
            f"Record separate evidence of the trigger outcome ({ident}).",
        )
        ready = evidence_ready(view, flow, ident, receipt)
        require(
            ready,
            "EVIDENCE_REVIEW",
            f"Finish reviewing {ident} before settling the condition.",
        )
        stage = _member(view, page, "exec-plan", receipt["exec"])
        require(
            stage.get("status") == "done",
            "CONDITION_EVIDENCE",
            f"Finish the supporting stage {receipt['exec']} first.",
        )
        reads.add(receipt["exec"])
        checks.update(_file_checks(view, receipt))
    outcome = {"triggered": triggered, "reason": reason, "evidence": evidence}
    previous = flow["conditions"].get(deliverable)
    require(
        previous is None or previous == outcome,
        "CONDITION_RECORDED",
        f"{deliverable} already has a recorded outcome for this version.",
    )
    conditions = {**flow["conditions"], deliverable: outcome}
    for ident, step in flow["checklist"].items():
        if deliverable in step["deliverables"] and all(
            conditions.get(name, {}).get("triggered") is False
            for name in step["deliverables"]
        ):
            stage = _member(view, page, "exec-plan", ident)
            require(
                stage.get("status") == "frozen",
                "COND_EXECUTED",
                f"{ident} has already started. Record its actual outcome instead.",
            )
            reads.add(ident)
    plan = _plan_update(
        view,
        page,
        {**flow, "conditions": conditions},
        condition=conditions[deliverable],
    )
    plan.workflow_reads.update({*evidence, *reads})
    plan.workflow_files = checks
    return plan


def evidence_ready(view, flow, ident, receipt):
    return not _needs_user(flow, receipt) or (
        view.cache["workflow"].get(ident, {}).get("reviewedEvidence")
        == _fingerprint(receipt)
    )


def evidence_review_required(view, note):
    page = view.snapshot.plan_page_of(note)
    if not strict(view, page):
        return None
    flow = flow_of(view, page)
    ident = identity(view.project_id, note)
    receipt = flow["evidence"].get(ident) if flow else None
    return bool(receipt and page.get("status") in OPEN and _needs_user(flow, receipt))


def review_evidence(view, note, plan, *, commit):
    page = view.snapshot.plan_page_of(note)
    if not strict(view, page):
        return plan
    flow = approved(view, page)
    ident = identity(view.project_id, note)
    receipt = flow["evidence"].get(ident)
    require(
        receipt and _fresh(view, receipt),
        "EVIDENCE_CHANGED",
        f"Register the current evidence for {ident} before reviewing it.",
    )
    plan.workflow_reads.add(identity(view.project_id, page))
    plan.workflow_files.update(_file_checks(view, receipt))
    if commit:
        plan.workflow_updates[ident] = {"reviewedEvidence": _fingerprint(receipt)}
    return plan


def running_gate(view, page, flow):
    require(flow["checklist"], "CHECKLIST", "Freeze the execution checklist first.")
    for ident in flow["checklist"]:
        note, _, _ = _frozen_step(view, page, ident, flow)
        require(
            note.get("status") in {"frozen", "running", "done", "aborted"},
            "STAGE_INVALID",
            f"{ident} needs a frozen execution plan.",
        )


def readout_issues(view, page, flow):
    issues = []
    if not flow["checklist"]:
        return [_gate("FLOW.CHECKLIST", "Freeze the execution checklist first.")]
    covered = {name: set() for name in flow["deliverables"]}
    for ident, step in flow["checklist"].items():
        if not _active_stage(step, flow):
            continue
        try:
            note = _member(view, page, "exec-plan", ident)
        except ResearchError:
            issues.append(_gate("FLOW.STAGE_MISSING", f"Restore {ident}."))
            continue
        if note.get("status") != "done":
            issues.append(_gate("FLOW.STAGE_INCOMPLETE", f"Finish {ident}."))
        elif not _all_receipts(flow, ident):
            issues.append(
                _gate("FLOW.STAGE_NO_EVIDENCE", f"Register evidence from {ident}.")
            )
    for ident, receipt in flow["evidence"].items():
        if not _fresh(view, receipt):
            issues.append(
                _gate(
                    "FLOW.EVIDENCE_CHANGED", f"Register the current version of {ident}."
                )
            )
        elif not evidence_ready(view, flow, ident, receipt):
            issues.append(_gate("FLOW.EVIDENCE_REVIEW", f"Review {ident}."))
        for name, outputs in receipt["outputs"].items():
            covered[name].update(outputs)
    for name, item in flow["deliverables"].items():
        if "condition" in item and name not in flow["conditions"]:
            issues.append(
                _gate("FLOW.CONDITION_PENDING", f"Record whether {name} triggered.")
            )
            continue
        if _skipped(flow, name):
            continue
        missing = set(item["files"]) - covered[name]
        if missing:
            issues.append(
                _gate(
                    "FLOW.DELIVERABLE_MISSING",
                    f"{name} needs {', '.join(sorted(missing))}.",
                )
            )
    return issues


def _readout(view, page, flow):
    issues = readout_issues(view, page, flow)
    if issues:
        raise gate_failed(
            "Finish the required stages and evidence reviews first.", issues
        )


def _body_hash(view, note, raw=None):
    from expnote.research.model import parse_note

    raw = note.path.read_bytes() if raw is None else raw
    from expnote.research.storage import equivalent_digest
    from expnote.research.workflow import revision_text

    body_revision = state_of(view, note).get("revision")
    if body_revision and equivalent_digest(
        view.context.resolved, note.path, digest(raw), body_revision
    ):
        raw = revision_text(view.project_id, body_revision).encode()
    live = parse_note(note.path, note.rel, raw.decode("utf-8-sig"))
    if not live.has_fm or live.fm_error:
        _bad(f"invalid result Markdown: {note.path}")
    return _fingerprint(
        {
            "body": live.body.strip(),
            "evidence": live.get("evidence"),
            "plan": live.link_field("plan"),
            "id": live.get("id"),
        }
    )


def _package(view, page, note, flow, raw=None, *, pin_images=False):
    data = {
        "approval": flow["approval"],
        "planRevision": flow["revision"],
        "checklist": _fingerprint(flow["checklist"]),
        "evidence": _fingerprint(flow["evidence"]),
        "conditions": _fingerprint(flow["conditions"]),
        "body": _body_hash(view, note, raw),
    }
    stored = state_of(view, note).get("resultSubmission") or {}
    if pin_images or "images" in stored:
        from expnote.research.results import image_plan

        raw = note.path.read_bytes() if raw is None else raw
        _, images = image_plan(view, note.path, raw.decode("utf-8-sig"), "res")
        data["images"] = sorted(set(images.referenced(note.path).values()))
    return data


def _pin(view, page, flow, plan):
    plan.workflow_reads.add(identity(view.project_id, page))
    plan.workflow_reads.update(flow["checklist"])
    for ident, receipt in flow["evidence"].items():
        plan.workflow_reads.add(ident)
        plan.workflow_files.update(_file_checks(view, receipt))


def result_submit(view, note, page, plan):
    flow = approved(view, page)
    require(
        page.get("status") in {"running", "read-out", "confirmed"},
        "PLAN_NOT_RUNNING",
        "Advance the PLAN to running before submitting RES.",
    )
    _readout(view, page, flow)
    members = [
        candidate
        for candidate in view.snapshot.current_plan_members(str(page.get("id")))
        if candidate.type == "result"
        and candidate.get("status") not in {"withdrawn", "superseded"}
    ]
    require(
        len(members) == 1 and members[0] == note,
        "RESULT_COUNT",
        "The active PLAN must have one final result.",
    )
    result = _package(view, page, note, flow, pin_images=True)
    require(
        result["body"] != state_of(view, note).get("rewriteFrom"),
        "RESULT_REWRITE",
        "Update the RES as specified in the approved impact list.",
    )
    ident = identity(view.project_id, note)
    plan.workflow_updates[ident] = {
        "resultSubmission": result,
        "reviewedResult": None,
        "rewriteFrom": None,
    }
    _pin(view, page, flow, plan)
    return plan


def review_result(view, note, page, plan, *, passed):
    flow = approved(view, page)
    require(
        page.get("status") == "read-out",
        "RESULT_STATE",
        "Submit the current RES for this PLAN first.",
    )
    ident = identity(view.project_id, note)
    stored = state_of(view, note).get("resultSubmission")
    if passed:
        _readout(view, page, flow)
        require(
            stored and stored == _package(view, page, note, flow),
            "RESULT_STALE",
            "The PLAN, evidence or RES changed. Submit the current result again.",
        )
        _pin(view, page, flow, plan)
    else:
        plan.workflow_reads.add(identity(view.project_id, page))
    plan.workflow_updates[ident] = {
        "reviewedResult": stored if passed else None,
        "resultSubmission": stored if passed else None,
    }
    return plan


def close_guard(view, page, force):
    flow = approved(view, page)
    require(
        not force,
        "FORCE_CLOSE",
        "Withdraw the PLAN to stop before completing its workflow.",
    )
    require(
        page.get("status") == "confirmed",
        "RESULT_STATE",
        "Have the current RES reviewed first.",
    )
    _readout(view, page, flow)
    note = view.snapshot.note(page.link_field("result") or "")
    require(note, "RESULT_STATE", "The PLAN needs an approved result.")
    state = state_of(view, note)
    source = note.path.read_bytes()
    result = _package(view, page, note, flow, source)
    require(
        note.get("status") == "confirmed" and state.get("reviewedResult") == result,
        "RESULT_STALE",
        "PLAN, EV or RES changed. Submit and review the current result again.",
    )
    return note, state["revision"], digest(source)


def conclusion(view, page, plan, *, force, withdrawn):
    if not strict(view, page):
        return plan
    flow = flow_of(view, page)
    binding = {
        "revision": state_of(view, page).get("revision"),
        "kind": "withdraw" if withdrawn else "close",
        "decision": plan.data["decision"],
        "approval": (flow or {}).get("approval"),
    }
    if not withdrawn:
        note, revision, source = close_guard(view, page, force)
        binding.update(result=identity(view.project_id, note), resultRevision=revision)
        plan.workflow_reads.add(identity(view.project_id, note))
        plan.workflow_files[note.path] = source
        _pin(view, page, flow, plan)
    plan.workflow_updates[identity(view.project_id, page)] = {"conclusion": binding}
    return plan


def transition_issues(view, page, target):
    flow = flow_of(view, page)
    if not flow or target not in {"running", "read-out", "confirmed", "closed"}:
        return None
    try:
        if target == "running":
            running_gate(view, page, flow)
        elif target == "closed":
            close_guard(view, page, False)
        else:
            _readout(view, page, flow)
            results = [
                member
                for member in view.snapshot.current_plan_members(str(page.get("id")))
                if member.type == "result"
                and member.get("status") not in {"withdrawn", "superseded"}
            ]
            require(len(results) == 1, "RESULT_COUNT", "Prepare one final RES.")
            if target == "confirmed":
                note = results[0]
                require(
                    note.get("status") == "first-read"
                    and state_of(view, note).get("resultSubmission")
                    == _package(view, page, note, flow),
                    "RESULT_STALE",
                    "Submit the current RES for review.",
                )
    except ResearchError as error:
        return error.details.get("gates", [_gate(error.code, error.message)])
    return []


def describe(view, page):
    flow = flow_of(view, page)
    if not flow:
        return None
    checklist = {}
    for ident, step in flow["checklist"].items():
        try:
            status = _member(view, page, "exec-plan", ident).get("status")
        except ResearchError:
            status = "missing"
        checklist[ident] = {
            **step,
            "status": status if _active_stage(step, flow) else "not-triggered",
        }
    current = page.get("status") in OPEN
    blockers = readout_issues(view, page, flow) if current else []
    try:
        if page.get("status") == "confirmed" and not blockers:
            close_guard(view, page, False)
    except ResearchError as error:
        blockers = error.details.get("gates", [_gate(error.code, error.message)])
    return {
        "revision": flow["revision"],
        "approval": flow["approval"],
        "deliverables": flow["deliverables"],
        "checklist": checklist,
        "conditions": flow["conditions"],
        "blockers": blockers,
        "running": {
            ident: action
            for ident, action in flow["running"].items()
            if view.cache["workflow"].get(ident, {}).get("fields", {}).get("status")
            == "running"
        },
        "closeReady": page.get("status") == "confirmed" and not blockers,
    }
