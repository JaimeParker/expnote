"""Authoritative lifecycle state for split-layout projects."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3

from expnote.research.decision_store import store_path
from expnote.research.edits import edit_frontmatter
from expnote.research.errors import ResearchError
from expnote.research.layout import atomic_bytes, record_id
from expnote.research.model import parse_note
from expnote.research.textkit import find_section
from expnote.research.writeback import cas_mismatch, project_state_dir

FIELDS = {
    "status",
    "approved_decision",
    "closed_decision",
    "forced_close",
    "superseded_by",
    "result",
    "reopened_on",
    "earlier_cycle",
    "rules_decision",
    "rules_sha256",
    "frozen_on",
    "approved_by",
    "new_rules",
    "rules_amended_by",
    "labelled_by",
    "labelled_on",
    "label_authorization",
    "confirmed_decision",
    "deviation_impact",
    "reviewed_by",
    "submitted_on",
    "returned_on",
    "reviewed_on",
    "review_authorization",
}
TYPES = {"plan", "exec-plan", "evidence", "result"}
INITIAL = {
    "plan": "draft",
    "exec-plan": "draft",
    "evidence": "rendered",
    "result": "draft",
}


def identity(project: str, note) -> str:
    return str(note.get("id") or note.get("record_id") or record_id(project, note.rel))


def schema(conn: sqlite3.Connection) -> None:
    for sql in (
        "CREATE TABLE IF NOT EXISTS workflow "
        "(id TEXT PRIMARY KEY, state TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS workflow_events (seq INTEGER PRIMARY KEY, "
        "id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL, "
        "actor TEXT NOT NULL, at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS plan_submissions (decision INTEGER PRIMARY KEY,"
        " plan TEXT NOT NULL, revision TEXT NOT NULL, base_revision TEXT)",
        "CREATE TABLE IF NOT EXISTS workflow_proposals (decision INTEGER PRIMARY KEY,"
        " spec TEXT NOT NULL)",
    ):
        conn.execute(sql)


def read_state(project: str, *, proposals: dict | None = None) -> tuple[dict, dict]:
    db = store_path(project)
    if not db.is_file():
        return {}, {}
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "workflow" not in tables:
            return {}, {}
        states = {
            key: json.loads(raw)
            for key, raw in conn.execute("SELECT id, state FROM workflow")
        }
        submissions = {
            number: (plan, revision)
            for number, plan, revision in conn.execute(
                "SELECT decision, plan, revision FROM plan_submissions"
            )
        }
        if proposals is not None and "workflow_proposals" in tables:
            proposals.update(
                (number, json.loads(spec))
                for number, spec in conn.execute(
                    "SELECT decision, spec FROM workflow_proposals"
                )
            )
    return states, submissions


def put(conn, ident, state, action, actor, at) -> None:
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO workflow VALUES (?,?)", (ident, payload))
    conn.execute(
        "INSERT INTO workflow_events(id,action,state,actor,at) VALUES (?,?,?,?,?)",
        (ident, action, payload, actor, at),
    )


def current_state(conn, ident):
    row = conn.execute("SELECT state FROM workflow WHERE id=?", (ident,)).fetchone()
    return json.loads(row[0]) if row else {}


def check_states(conn, expected):
    for ident, state in expected.items():
        if current_state(conn, ident) != state:
            raise ResearchError(
                "CONFLICT", f"{ident} changed, reload before retrying", exit_code=5
            )


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def revision_text(project: str, revision: str) -> str:
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ResearchError("REVISION_INVALID", "invalid revision hash", exit_code=6)
    path = project_state_dir(project) / "revisions" / (revision + ".md")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ResearchError(
            "REVISION_MISSING", f"cannot read revision {revision}: {error}", exit_code=6
        ) from None
    if digest(raw) != revision:
        raise ResearchError(
            "REVISION_INVALID", f"revision checksum mismatch: {path}", exit_code=6
        )
    return raw.decode("utf-8-sig")


def persist_revision(project: str, raw: bytes) -> str:
    sha = digest(raw)
    path = project_state_dir(project) / "revisions" / (sha + ".md")
    if not path.exists():
        atomic_bytes(path, raw)
    revision_text(project, sha)
    return sha


def _predicates(profile):
    return {
        "labels": lambda title: any(key in title for key in profile.heading_labels),
        "remarks": lambda title: title == "Review remarks",
    }


def _sections(note, profile) -> dict:
    sections = {}
    for key, predicate in _predicates(profile).items():
        span = find_section(note.body, predicate)
        if span:
            start, end, _ = span
            sections[key] = "\n".join(note.body.split("\n")[start:end])
    return sections


def _deviation_id(context) -> str:
    return ".deviations"


def virtual_note(context, note, states):
    deviation = context.resolved.material_dir / context.profile.deviation_register
    if note.path == deviation and ".deviations" in states:
        return parse_note(note.path, note.rel, states[".deviations"]["text"])
    kind = context.profile.legacy_type_map.get(note.type, note.type)
    state = states.get(identity(context.entry.id, note), {})
    if kind not in TYPES and not state:
        return note
    fm = dict(state.get("fields", {}))
    if kind in TYPES:
        fm.setdefault("status", INITIAL[kind])
    revision = state.get("revision")
    text = revision_text(context.entry.id, revision) if revision else note.text
    base = parse_note(note.path, note.rel, text)
    lines = base.body.split("\n")
    for key, predicate in _predicates(context.profile).items():
        if key not in state.get("sections", {}):
            continue
        replacement = state["sections"][key].split("\n")
        span = find_section("\n".join(lines), predicate)
        if span:
            lines[span[0] : span[1]] = replacement
        else:
            lines.extend(["", *replacement])
    header = base.text[: -len(base.body)] if base.body else base.text
    text = header + "\n".join(lines)
    items = [(key, json.dumps(value, ensure_ascii=False)) for key, value in fm.items()]
    if items and base.has_fm:
        text = edit_frontmatter(text, items, frozenset(FIELDS - fm.keys()))
    return parse_note(note.path, note.rel, text)


def import_workflow(context, records, files, backup_vault) -> None:
    from datetime import UTC, datetime

    from expnote.research.decision_store import DecisionStore

    project = context.entry.id
    stamp = datetime.now(UTC).isoformat()
    vault = context.resolved.vault_root
    plan_versions = {}

    def source_bytes(note):
        return (backup_vault / note.path.relative_to(vault)).read_bytes()

    for _, ident, note in records:
        if note.type == "plan" and note.get("status") and note.get("status") != "draft":
            revision = persist_revision(project, source_bytes(note))
            plan_versions[ident.casefold()] = revision
            plan_versions[note.cstem] = revision
    with DecisionStore.open(project) as store, store.transaction():
        schema(store.conn)
        existing, _ = read_state(project)
        for _, ident, note in records:
            if ident in existing:
                continue
            fields = {k: v for k, v in note.fm.items() if k in FIELDS}
            if not fields:
                continue
            state = {"fields": fields, "sections": _sections(note, context.profile)}
            if fields.get("status") not in {"draft", "rendered"}:
                state.update(
                    revision=persist_revision(project, source_bytes(note)),
                    origin="imported-baseline",
                )
            if note.type == "exec-plan" and "revision" in state:
                parent = note.link_field("plan")
                if parent in plan_versions:
                    state["planRevision"] = plan_versions[parent]
            put(store.conn, ident, state, "layout-import", "migration", stamp)
        source = context.resolved.project_dir / context.profile.deviation_register
        if source in files and _deviation_id(context) not in existing:
            put(
                store.conn,
                _deviation_id(context),
                {"text": source.read_text(encoding="utf-8-sig")},
                "layout-import",
                "migration",
                stamp,
            )


def prepare_write(view, plan, command, apply):
    """Translate existing text-based gate builders to transactional workflow updates."""
    from expnote.research.writes import StoreOp

    if command == "plan adopt":
        return plan
    context, project = view.context, view.project_id
    states = view.cache["workflow"]
    notes = {note.path: note for note in view.snapshot.notes}
    deviation = context.resolved.material_dir / view.profile.deviation_register
    remaining = []
    updates = {}
    checked_raw = dict(plan.workflow_files)
    result_assets = []

    for edit in plan.edits:
        note = notes.get(edit.path)
        is_deviation = edit.path == deviation and edit.address.startswith("table-row:")
        is_split = command == "ev split" and edit.address == "body:split"
        if edit.transform is None or (
            not is_deviation
            and not is_split
            and (
                note is None
                or not (
                    edit.address.startswith(
                        ("frontmatter:", "label-row:", "label-rows:")
                    )
                )
            )
        ):
            remaining.append(edit)
            continue
        raw = edit.path.read_bytes()
        sha = digest(raw)
        if edit.path in checked_raw and checked_raw[edit.path] != sha:
            raise cas_mismatch(edit.path, checked_raw[edit.path], sha)
        indexed = view.file_sha256(note.rel) if note else None
        token = view.write_token(note.rel) if note else None
        if (
            edit.expected_sha256
            and edit.expected_sha256 != sha
            and not (edit.expected_sha256 == token and sha == indexed)
        ):
            raise cas_mismatch(edit.path, edit.expected_sha256, token or sha)
        checked_raw[edit.path] = sha
        if is_deviation:
            ident = _deviation_id(context)
            text = states.get(ident, {}).get("text", raw.decode("utf-8-sig"))
            updates[ident] = {"text": edit.transform(text)}
            continue
        ident = identity(project, note)
        updated = parse_note(note.path, note.rel, edit.transform(note.text))
        state = dict(updates.get(ident, states.get(ident, {})))
        if command == "plan advance" and updated.get("status") == "approved":
            raise ResearchError(
                "PLAN_SUBMIT_REQUIRED",
                "submit this plan with a proposed decision",
                exit_code=1,
            )
        state["fields"] = {k: v for k, v in updated.fm.items() if k in FIELDS}
        state["sections"] = _sections(updated, view.profile)
        if is_split:
            text = edit_frontmatter(
                updated.text,
                [
                    ("record_id", json.dumps(ident)),
                    (
                        "aliases",
                        json.dumps(
                            note.get("aliases") or [note.stem], ensure_ascii=False
                        ),
                    ),
                ],
                remove=FIELDS,
            )
            remaining.append(
                dataclasses.replace(
                    edit,
                    transform=lambda _text, output=text: output,
                    expected_sha256=sha,
                )
            )
            state.pop("revision", None)
        if command in {"exec freeze", "res submit"} and ident == str(
            plan.data.get("id")
        ):
            preserve = (
                state.get("revision")
                and (
                    (command == "res submit" and not plan.data.get("revised"))
                    or plan.data.get("matchesPrevious")
                )
                and ident not in plan.workflow_updates
            )
            if not preserve:
                previous = state.get("revision")
                if previous and "history" not in state:
                    state["history"] = []
                if previous:
                    state["history"] = [*state["history"], previous]
                if command == "res submit" and plan.data.get("from") == "confirmed":
                    state["previousConfirmed"] = previous
                if command == "res submit" and context.entry.results_in_vault:
                    result_assets.append(raw.decode("utf-8-sig"))
                state["revision"] = (
                    persist_revision(project, raw) if apply else digest(raw)
                )
                state["origin"] = "submitted"
                if command == "exec freeze":
                    parent = view.snapshot.plan_page_of(note)
                    if parent:
                        state["planRevision"] = states.get(
                            identity(project, parent), {}
                        ).get("revision")
        if command == "res review" and updated.get("status") == "draft":
            returned = state.pop("revision", None)
            if context.entry.results_in_vault and returned:
                state["history"] = [*state.get("history", []), returned]
        if updated.get("status") == "labelled" and plan.workflow_updates.get(
            ident, {}
        ).get("reviewedEvidence"):
            reviewed = updated.text.encode("utf-8")
            state["reviewedEvidenceRevision"] = (
                persist_revision(project, reviewed) if apply else digest(reviewed)
            )
        if state != states.get(ident, {}):
            updates[ident] = state

    for idx, edit in enumerate(remaining):
        if edit.transform is not None or not edit.new_text:
            continue
        resolved = context.resolved
        rel = resolved.logical_path(edit.path)
        note = parse_note(edit.path, rel, edit.new_text)
        if not note.has_fm or note.fm_error:
            continue
        items = []
        if edit.path.is_relative_to(resolved.research_dir) and note.type in {
            "evidence",
            "evidence-appendix",
        }:
            if not (note.get("id") or note.get("record_id")):
                items.append(("record_id", json.dumps(record_id(project, rel))))
            if not note.get("aliases"):
                items.append(("aliases", json.dumps([note.stem], ensure_ascii=False)))
        remove = FIELDS if note.type in TYPES else frozenset()
        if items or remove:
            raw = edit_frontmatter(edit.new_text, items, remove=remove)
            remaining[idx] = dataclasses.replace(edit, new_text=raw)

    for ident, fields in plan.workflow_updates.items():
        candidate = {**updates.get(ident, states.get(ident, {})), **fields}
        if candidate != states.get(ident, {}):
            updates[ident] = candidate
    if not (updates or plan.workflow_reads):
        return dataclasses.replace(plan, edits=remaining)
    original = plan.store_op

    def transact(store, actor, at):
        schema(store.conn)
        for path, sha in checked_raw.items():
            try:
                current = digest(path.read_bytes())
            except OSError:
                current = "missing"
            if current != sha:
                raise cas_mismatch(path, sha, current)
        check_states(
            store.conn,
            {
                ident: states.get(ident, {})
                for ident in updates.keys() | plan.workflow_reads
            },
        )
        for ident, state in updates.items():
            put(store.conn, ident, state, command, actor, at)
        if result_assets:
            from expnote.research.results import sync_result_images

            for text in result_assets:
                sync_result_images(context, text, apply=apply)
        if original:
            original.apply(store, actor, at)

    return dataclasses.replace(
        plan,
        edits=remaining,
        store_op=StoreOp(
            "workflow",
            ",".join(updates),
            f"{command}: {', '.join(updates)}",
            transact,
        ),
    )


def plan_submission(view, plan_id, decision_id, apply, spec=None, *, replace=False):
    from expnote.research import entities, lifecycle
    from expnote.research.writes import StoreOp, WritePlan, _decision

    if view.context.entry.layout != "split":
        raise ResearchError(
            "SPLIT_REQUIRED", "plan submit requires the split layout", exit_code=2
        )
    page = entities.find_plan(view, plan_id)
    number, decision = _decision(view, decision_id)
    if decision.status != "proposed" or decision.source != "db":
        raise ResearchError(
            "DECISION_NOT_PROPOSED", "use a proposed decision", exit_code=1
        )
    if number not in {d.number for d, _ in view.snapshot.decisions_for_plan(plan_id)}:
        raise ResearchError(
            "DECISION_UNRELATED", "decision must target this plan", exit_code=1
        )
    if spec is None and (
        lifecycle.strict(view, page) or number in view.cache["proposals"]
    ):
        raise ResearchError(
            "WORKFLOW_REQUIRED",
            "submit approved deliverables with --workflow",
            exit_code=1,
        )
    if spec is not None and plan_id not in decision.plans:
        raise ResearchError(
            "DECISION_UNRELATED",
            "set the decision's --plans to this PLAN explicitly",
            exit_code=1,
        )
    if page.get("status") in entities.PLAN_TERMINAL or (
        page.get("status") == "closed" and not (spec and spec.get("reopen"))
    ):
        raise ResearchError(
            "PLAN_CLOSED", "submit an explicit reopening of a closed PLAN", exit_code=1
        )
    if spec and spec.get("reopen") and page.get("status") != "closed":
        raise ResearchError(
            "PLAN_NOT_CLOSED", "reopen applies to a closed PLAN", exit_code=1
        )
    proposal = lifecycle.proposal(view, page, spec) if spec is not None else None
    raw = page.path.read_bytes()
    project = view.project_id
    sha = persist_revision(project, raw) if apply else digest(raw)

    def transact(store, actor, at):
        schema(store.conn)
        row = store.get(number)
        if (
            row is None
            or row["status"] != "proposed"
            or row["version"] != decision.version
        ):
            raise ResearchError(
                "CONFLICT", "decision changed, reload and retry", exit_code=5
            )
        old = store.conn.execute(
            "SELECT plan, revision FROM plan_submissions WHERE decision=?", (number,)
        ).fetchone()
        if old:
            row = store.conn.execute(
                "SELECT spec FROM workflow_proposals WHERE decision=?", (number,)
            ).fetchone()
            if (
                tuple(old) == (plan_id, sha)
                and (json.loads(row[0]) if row else None) == proposal
            ):
                return
            if not replace or old[0] != plan_id:
                raise ResearchError(
                    "CONFLICT",
                    "this decision already has a submitted version",
                    exit_code=5,
                    remediation="use --replace to update this pending submission",
                )
        current = digest(page.path.read_bytes())
        if current != sha:
            raise cas_mismatch(page.path, sha, current)
        base = view.cache["workflow"].get(plan_id, {}).get("revision")
        check_states(store.conn, {plan_id: lifecycle.state_of(view, page)})
        store.conn.execute(
            "INSERT OR REPLACE INTO plan_submissions VALUES (?,?,?,?)",
            (number, plan_id, sha, base),
        )
        if proposal is not None:
            check_states(store.conn, lifecycle.inventory(view, page))
            store.conn.execute(
                "INSERT OR REPLACE INTO workflow_proposals VALUES (?,?)",
                (number, json.dumps(proposal)),
            )
        store.conn.execute(
            "UPDATE decisions SET version=version+1 WHERE number=?", (number,)
        )
        store.conn.execute(
            "INSERT INTO workflow_events(id,action,state,actor,at) VALUES (?,?,?,?,?)",
            (
                plan_id,
                "plan-submit",
                json.dumps({"revision": sha, "decision": number}),
                actor,
                at,
            ),
        )

    return WritePlan(
        [],
        {"id": plan_id, "decision": decision_id, "revision": sha, "workflow": proposal},
        store_op=StoreOp("plan-submit", plan_id, f"Submit {sha}", transact),
    )


def confirm_submission(view, store, number, actor, at, *, apply):
    if view.context.entry.layout != "split":
        return
    schema(store.conn)
    submitted = store.conn.execute(
        "SELECT plan, revision, base_revision FROM plan_submissions WHERE decision=?",
        (number,),
    ).fetchone()
    if submitted is None:
        return
    ident, revision, base = submitted
    displayed = view.cache["submissions"].get(number)
    if displayed != (ident, revision):
        raise ResearchError(
            "CONFLICT", "submission changed, reload the decision", exit_code=5
        )
    from expnote.research import entities, lifecycle
    from expnote.research.checks import _check_plan
    from expnote.research.model import FindingSink
    from expnote.research.writes import (
        _clone,
        _gate_rows,
        _snapshot_with_record,
        decision_ref_text,
        gate_failed,
    )

    state = current_state(store.conn, ident)
    if state.get("revision") != base or state != view.cache["workflow"].get(ident, {}):
        raise ResearchError(
            "CONFLICT", "plan changed, reload the decision", exit_code=5
        )
    page = entities.find_plan(view, ident)
    status = page.get("status")
    row = store.conn.execute(
        "SELECT spec FROM workflow_proposals WHERE decision=?", (number,)
    ).fetchone()
    proposed = json.loads(row[0]) if row else None
    if proposed != view.cache.get("proposals", {}).get(number):
        raise ResearchError(
            "CONFLICT", "impact changed, refresh the decision", exit_code=5
        )
    if status in entities.PLAN_TERMINAL or (
        status == "closed" and not (proposed and proposed["reopen"])
    ):
        raise ResearchError(
            "PLAN_CLOSED", "reopen the plan before approving a revision", exit_code=1
        )
    if base and base != revision:
        state["history"] = list(dict.fromkeys([*state.get("history", []), base]))
    state.update(revision=revision, origin="submitted")
    state["fields"] = dict(
        state.get("fields", {}),
        approved_decision=decision_ref_text(view, page, number),
        status="approved" if proposed or status == "draft" else status,
    )
    if proposed:
        if (state.get("lifecycle") or {}).get("approval") != proposed["baseApproval"]:
            raise ResearchError(
                "CONFLICT", "approved PLAN version changed", exit_code=5
            )
        members = lifecycle.inventory(view, page)
        if lifecycle.inventory_fingerprint(members) != proposed["basis"]:
            raise ResearchError(
                "CONFLICT",
                "stage or review changed; refresh and replace the impact list",
                exit_code=5,
            )
        check_states(store.conn, members)
        flow, changes = lifecycle.activate(
            view, page, proposed, number, revision, apply
        )
        state["lifecycle"] = flow
        state["conclusion"] = None
        for field in ("result", "closed_decision", "forced_close"):
            state["fields"].pop(field, None)
        for key, update in changes.items():
            put(store.conn, key, update, "plan-revision", actor, at)
    candidate = virtual_note(view.context, page, {ident: state})
    snap = _snapshot_with_record(view, store.get(number))
    sink = FindingSink()
    _check_plan(snap, _clone(candidate, status=state["fields"]["status"]), sink)
    unmet = _gate_rows(sink.items)
    if unmet:
        raise gate_failed(f"{ident} cannot adopt this submission", unmet)
    put(store.conn, ident, state, "plan-approve", actor, at)
