"""TODO catalogue: derived items (never written), manual TODO file, and `next`."""

from __future__ import annotations

import json
import shlex

from expnote.research import adopt
from expnote.research.entities import (
    decision_id,
    decision_plans,
    decision_ref_id,
    deviation_plans,
    is_legacy,
    lifecycle_members,
    normalized_result_status,
    note_date,
    notes_of,
    plan_id_of,
    plan_members,
    plan_pages,
    truncate,
)
from expnote.research.index import linked_workspaces
from expnote.research.model import Decision, Note
from expnote.research.runs import stale_running_runs
from expnote.research.schemas import EV_LABELLED_STATUSES, SUPPORTED_SCHEMAS
from expnote.research.textkit import fold, iter_links, link_stem
from expnote.research.view import ProjectView

SEVERITY_RANK = {"action": 0, "warn": 1, "info": 2}
GATE_CODES = ("LIFE.", "DEC.NOT_CONFIRMED")
IMPACT_CHOICES = "--deviation-impact <none|weak|moderate|affects>"
# Order of the agent's steps in `next`: unblock first, tidy last.
NEXT_PRIORITY = (
    "decision-confirmed-not-applied",
    "plan-gate-blocked",
    "plan-reopen-unmarked",
    "plan-ready-to-run",
    "plan-needs-exec",
    "exec-awaiting-freeze",
    "plan-prep-while-rules-pending",
    "res-returned",
    "res-draft",
    "legacy-no-final-res",
    "final-res-pending",
    "deviation-open",
    "ev-unmapped",
    "stale-running-runs",
)


def _item(
    kind: str,
    owner: str,
    severity: str,
    ref: str,
    message: str,
    action: str,
    *,
    plan: str | None = None,
    command: str | None = None,
    **extra,
) -> dict:
    return {
        "id": f"derived:{kind}:{ref}",
        "source": "derived",
        "kind": kind,
        "owner": owner,
        "severity": severity,
        "ref": ref,
        "message": message,
        "action": action,
        "plan": plan,
        "command": command,
        **extra,
    }


def lands_cites(decision: Decision, cstem: str) -> bool:
    """The decision's target column / `targets` field links the note."""
    return any(fold(link_stem(t)) == cstem for _, t in iter_links(decision.lands_text))


def _project_flag(view: ProjectView) -> str:
    return f" --project {view.project_id} --json"


class _Derive:
    """One pass over a view that yields the derived items kind by kind."""

    def __init__(self, view: ProjectView):
        self.view = view
        self.snap = view.snapshot
        self.flag = _project_flag(view)
        self.pages = plan_pages(view)
        self.decisions = [self.snap.decisions[n] for n in sorted(self.snap.decisions)]
        self.plans_of = decision_plans(view)
        self.unmarked = {
            f.where
            for f in view.checks().findings
            if f.code == "LIFE.PLAN_REOPEN_UNMARKED"
        }

    def decision_id(self, decision: Decision) -> str:
        return decision_id(self.view, decision)

    def decisions_proposed(self) -> list[dict]:
        items = []
        for decision in self.decisions:
            if decision.status != "proposed":
                continue
            did = self.decision_id(decision)
            plans = self.plans_of.get(decision.number, [])
            items.append(
                _item(
                    "decision-proposed",
                    "user",
                    "action",
                    did,
                    f"{did} is proposed and waits for the user",
                    "approve or reject it in the GUI (or confirm in chat and let the "
                    "agent record it with the user's words)",
                    plan=plans[0] if plans else None,
                )
            )
        return items

    def evidence(self) -> list[dict]:
        items = []
        for note in notes_of(self.view, "evidence"):
            if note.get("status") not in EV_LABELLED_STATUSES:
                items.append(
                    _item(
                        "ev-unlabelled",
                        "user",
                        "action",
                        note.stem,
                        f"evidence page {note.stem} is not labelled yet and needs "
                        "the user's labels",
                        "open the evidence page and fill the label table",
                        plan=plan_id_of(self.view, note),
                    )
                )
            if note.link_field("plan") is None and is_legacy(self.view, note):
                items.append(
                    _item(
                        "ev-unmapped",
                        "agent",
                        "info",
                        note.stem,
                        f"legacy evidence page {note.stem} has no plan field",
                        "infer the plan and propose it to the coordinator",
                        command="expnote ev list --fields stem,plan,path" + self.flag,
                    )
                )
        return items

    def plan_waiting_on_decisions(self, page: Note) -> list[dict]:
        """Draft plans: a proposed decision waits, or a confirmed one is unapplied."""
        plan_id = str(page.get("id", ""))
        if page.get("status") != "draft":
            return []
        items = []
        concerning = [d for d, _ in self.snap.decisions_for_plan(plan_id)]
        waiting = [d for d in concerning if d.status == "proposed"]
        if waiting:
            ids = ", ".join(self.decision_id(d) for d in waiting)
            items.append(
                _item(
                    "plan-awaiting-approval",
                    "user",
                    "action",
                    plan_id,
                    f"draft plan {plan_id} waits for a decision: {ids}",
                    "decide the proposed decision(s)",
                    plan=plan_id,
                    decisions=[self.decision_id(d) for d in waiting],
                )
            )
        if not page.get("approved_decision"):
            for decision in self.decisions:
                if decision.status == "confirmed" and lands_cites(decision, page.cstem):
                    did = self.decision_id(decision)
                    items.append(
                        _item(
                            "decision-confirmed-not-applied",
                            "agent",
                            "action",
                            f"{did}:{plan_id}",
                            f"{did} approves {plan_id} but the plan is still draft",
                            "advance the plan",
                            plan=plan_id,
                            role="approval",
                            decision=did,
                            command=f"expnote plan advance {plan_id} --to approved "
                            f"--decision {did} --apply" + self.flag,
                        )
                    )
        return items

    def exec_rules(self, page: Note, member: Note) -> list[dict]:
        if member.get("status") != "draft":
            return []
        plan_id = str(page.get("id", ""))
        exec_id = str(member.get("id") or member.stem)
        items = []
        for decision in self.decisions:
            if not lands_cites(decision, member.cstem):
                continue
            did = self.decision_id(decision)
            if decision.status == "proposed":
                if page.get("status") == "approved" and page.rel not in self.unmarked:
                    items.append(
                        _item(
                            "plan-prep-while-rules-pending",
                            "agent",
                            "info",
                            f"{plan_id}:{did}",
                            f"formal runs of {plan_id} wait for {did}; do "
                            "engineering preparation now (implementation, "
                            "material checks, smoke tests)",
                            "prepare; do not start formal runs",
                            plan=plan_id,
                            decision=did,
                            stem=member.stem,
                        )
                    )
                items.append(
                    _item(
                        "exec-rules-unconfirmed",
                        "user",
                        "action",
                        exec_id,
                        f"rules of {exec_id} wait for the user ({did})",
                        "decide the rules decision",
                        plan=plan_id,
                        decision=did,
                        stem=member.stem,
                    )
                )
            elif decision.status == "confirmed":
                items.append(
                    _item(
                        "decision-confirmed-not-applied",
                        "agent",
                        "action",
                        f"{did}:{exec_id}",
                        f"{did} confirms the rules of {exec_id}, still draft",
                        "freeze the exec-plan",
                        plan=plan_id,
                        role="rules",
                        decision=did,
                        stem=member.stem,
                        command=f"expnote exec freeze {exec_id} "
                        f"--rules-decision {did} --apply" + self.flag,
                    )
                )
        return items

    def result_review(self, page: Note, member: Note) -> list[dict]:
        """The review loop of a current-cycle result: draft, first-read, returned."""
        if member.schema not in SUPPORTED_SCHEMAS:
            return []
        status = member.get("status")
        plan_id = str(page.get("id", ""))
        res_id = str(member.get("id") or member.stem)
        if status == "first-read":
            return [
                _item(
                    "res-awaiting-review",
                    "user",
                    "action",
                    res_id,
                    f"read and check result {res_id}, then pass or return it",
                    "open the result in the GUI, read it, then pass the review or "
                    "return it with remarks",
                    plan=plan_id,
                    stem=member.stem,
                )
            ]
        if status != "draft" or page.get("status") in ("superseded", "withdrawn"):
            return []
        command = f"expnote res submit {res_id} {IMPACT_CHOICES} --apply{self.flag}"
        if member.get("returned_on"):
            return [
                _item(
                    "res-returned",
                    "agent",
                    "action",
                    res_id,
                    f"{res_id} was returned on {member.get('returned_on')}",
                    "revise it per the user's review remarks (the Review remarks "
                    "section), then submit it again",
                    plan=plan_id,
                    stem=member.stem,
                    command=command,
                )
            ]
        return [
            _item(
                "res-draft",
                "agent",
                "info",
                res_id,
                f"{res_id} is still a draft",
                "finish the result, then submit it for the user's review",
                plan=plan_id,
                stem=member.stem,
                command=command,
            )
        ]

    def legacy_without_final_result(
        self, page: Note, members: list[Note]
    ) -> list[dict]:
        status = page.get("status")
        if not (is_legacy(self.view, page) and status in ("confirmed", "closed")):
            return []
        results = [m for m in members if self.snap.entity_type(m) == "result"]
        if any(normalized_result_status(self.view, r) == "confirmed" for r in results):
            return []
        plan_id = str(page.get("id", ""))
        return [
            _item(
                "legacy-no-final-res",
                "agent",
                "warn",
                plan_id,
                f"legacy plan {plan_id} is {status} without a confirmed result",
                "draft the final result, `res submit` it for the user's review",
                plan=plan_id,
                command=f'expnote new res --plan {plan_id} --title "<title>" '
                f"--apply{self.flag}",
            )
        ]

    def final_res_pending(self, page: Note, members: list[Note]) -> list[dict]:
        """Legacy plans with result cards but not exactly one active (final) one."""
        if not is_legacy(self.view, page):
            return []
        if page.get("status") in ("draft", "superseded", "withdrawn"):
            return []  # nothing to read out, or no result expected any more
        gone = ("superseded", "withdrawn")
        cards = [m for m in members if self.snap.entity_type(m) == "result"]
        active = [
            c for c in cards if normalized_result_status(self.view, c) not in gone
        ]
        if not cards or len(active) == 1:
            return []  # nothing to merge yet, or already one final result
        plan_id = str(page.get("id", ""))
        return [
            _item(
                "final-res-pending",
                "agent",
                "warn",
                plan_id,
                f"legacy plan {plan_id} has {len(active)} active result(s), "
                "not one final result",
                "draft the final result, supersede its stage cards, `res submit` it "
                "for the user's review",
                plan=plan_id,
                command=f'expnote new res --plan {plan_id} --title "<title>" '
                f"--apply{self.flag}",
            )
        ]

    def plans(self) -> list[dict]:
        items = []
        for page in self.pages:
            members = plan_members(self.view, page)
            items += self.plan_waiting_on_decisions(page)
            current = {
                m.cstem for m in self.snap.current_plan_members(str(page.get("id", "")))
            }
            for member in members:
                entity = self.snap.entity_type(member)
                if entity == "exec-plan":
                    items += self.exec_rules(page, member)
                elif entity == "result" and member.cstem in current:
                    items += self.result_review(page, member)
            items += self.legacy_without_final_result(page, members)
            items += self.final_res_pending(page, members)
            items += self.approved_plan_next_step(page)
        return items

    def approved_plan_next_step(self, page: Note) -> list[dict]:
        """An approved plan: write the exec-plan, decide its rules, or start runs."""
        if page.get("status") != "approved" or page.rel in self.unmarked:
            return []  # an unmarked reopen comes first
        plan_id = str(page.get("id", ""))
        execs = [
            m
            for m in self.snap.current_plan_members(plan_id)
            if self.snap.entity_type(m) == "exec-plan"
        ]
        ready = [m for m in execs if m.get("status") in ("frozen", "running", "done")]
        usable = [m for m in execs if m.get("status") != "aborted"]
        if ready:
            reopened = page.get("reopened_on")
            ids = ", ".join(str(m.get("id") or m.stem) for m in ready)
            cycle = f" (reopened {reopened})" if reopened else ""
            return [
                _item(
                    "plan-ready-to-run",
                    "agent",
                    "action",
                    plan_id,
                    f"{plan_id}{cycle} is approved and {ids} is frozen: start runs "
                    "per the exec-plan, then advance the plan to running",
                    "start the runs, then advance the plan",
                    plan=plan_id,
                    command=f"expnote plan advance {plan_id} --to running --apply"
                    + self.flag,
                    execPlans=[
                        {
                            "id": str(m.get("id") or m.stem),
                            "stem": m.stem,
                            "path": self.view.vault_rel(m),
                        }
                        for m in ready
                    ],
                )
            ]
        if not usable:
            return [
                _item(
                    "plan-needs-exec",
                    "agent",
                    "action",
                    plan_id,
                    f"{plan_id} is approved but has no exec-plan: write it "
                    "(engineering preparation can start now; formal runs wait for "
                    "a frozen exec-plan)",
                    "create the exec-plan and write its rules section",
                    plan=plan_id,
                    command=f"expnote new exec --plan {plan_id} --stage <STAGE> "
                    f'--title "<TITLE>" --apply' + self.flag,
                )
            ]
        items = []
        for draft in (m for m in usable if m.get("status") == "draft"):
            if any(
                d.status in ("proposed", "confirmed") and lands_cites(d, draft.cstem)
                for d in self.decisions
            ):
                continue
            exec_id = str(draft.get("id") or draft.stem)
            propose = (
                'expnote decision new --title "<TITLE>" --layer <LAYER> '
                '--decided "<RULES>" --reason "<WHY>" '
                f'--plans {plan_id} --targets "[[{draft.stem}]]" --apply' + self.flag
            )
            self_approve = (
                f"expnote exec freeze {exec_id} --self-approve --no-new-rules "
                "--apply" + self.flag
            )
            user_only = draft.get("new_rules") == "true"
            items.append(
                _item(
                    "exec-awaiting-freeze",
                    "agent",
                    "action",
                    exec_id,
                    f"{exec_id} is a draft: freeze it yourself if it adds no "
                    "judgement rules, otherwise propose the rules decision for "
                    "the user",
                    "freeze it, or propose the rules decision",
                    plan=plan_id,
                    command=propose if user_only else self_approve,
                    altCommand=None if user_only else propose,
                    stem=draft.stem,
                    execPlans=[
                        {
                            "id": exec_id,
                            "stem": draft.stem,
                            "path": self.view.vault_rel(draft),
                        }
                    ],
                )
            )
        return items

    def reopen_unmarked(self) -> list[dict]:
        items = []
        for page in self.pages:
            if page.rel not in self.unmarked:
                continue
            plan_id = str(page.get("id", ""))
            did = decision_ref_id(self.view, page.get("approved_decision"))
            undated = sorted(
                m.stem
                for m in lifecycle_members(self.view, page)
                if note_date(self.view, m) is None
            )
            hint = (
                f"; {len(undated)} member(s) have no date, each needs "
                "`--earlier <stem>` or a date: " + ", ".join(undated)
                if undated
                else ""
            )
            items.append(
                _item(
                    "plan-reopen-unmarked",
                    "agent",
                    "warn",
                    plan_id,
                    f"plan {plan_id} is {page.get('status')} but still holds a "
                    f"confirmed result: mark the reopen{hint}",
                    "run the dry run, review the earlier/current split, then apply",
                    plan=plan_id,
                    command=f"expnote plan reopen {plan_id} --mark-existing "
                    f"--decision {did or '<APPROVED_DECISION>'}" + self.flag,
                    undated=undated,
                )
            )
        return items

    def gate_blocked(self) -> list[dict]:
        blocked: dict[str, list[str]] = {}
        for finding in self.view.checks().findings:
            if finding.level == "FAIL" and finding.code.startswith(GATE_CODES):
                blocked.setdefault(finding.where, []).append(finding.code)
        items = []
        for page in self.pages:
            codes = blocked.get(page.rel)
            if not codes:
                continue
            plan_id = str(page.get("id", ""))
            items.append(
                _item(
                    "plan-gate-blocked",
                    "agent",
                    "warn",
                    plan_id,
                    f"plan {plan_id} fails lifecycle checks: "
                    f"{', '.join(sorted(set(codes)))}",
                    "read the findings and repair the missing gate",
                    plan=plan_id,
                    command=f"expnote check --plan {plan_id}" + self.flag,
                )
            )
        return items

    def open_deviations(self) -> list[dict]:
        items = []
        for row in sorted(self.snap.deviations.values(), key=lambda d: d.did):
            if row.closed:
                continue
            plans = deviation_plans(self.view, row)
            items.append(
                _item(
                    "deviation-open",
                    "agent",
                    "warn",
                    row.did,
                    f"deviation {row.did} is open: {truncate(row.content, 100)}",
                    "close it or record why it stays open",
                    plan=plans[0] if plans else None,
                    command=f"expnote deviation show {row.did}" + self.flag,
                )
            )
        return items

    def reviews_due(self) -> list[dict]:
        today = self.view.today()
        items = []
        for decision in self.decisions:
            review_by = decision.review_by
            if (
                review_by
                and review_by <= today
                and decision.status in ("proposed", "confirmed")
            ):
                did = self.decision_id(decision)
                items.append(
                    _item(
                        "decision-review-due",
                        "user",
                        "info",
                        did,
                        f"{did} was due for review on {review_by}",
                        "review the decision and supersede or keep it",
                    )
                )
        return items

    def conflict_files(self) -> list[dict]:
        prefix = self.view.prefix
        rows = self.view.conn.execute(
            "SELECT rel FROM files WHERE is_conflict = 1 "
            "AND substr(rel, 1, ?) = ? ORDER BY rel",
            (len(prefix) + 1, prefix + "/"),
        )
        return [
            _item(
                "sync-conflict-file",
                "user",
                "warn",
                rel,
                f"sync conflict file: {rel}",
                "merge or delete the conflict copy in Obsidian",
            )
            for (rel,) in rows
        ]

    def unadopted_plan_drafts(self) -> list[dict]:
        drafts = adopt.unadopted_plan_drafts(self.view)
        if not drafts:
            return []
        next_id = adopt.next_plan_id(self.view)
        return [
            _item(
                "plan-draft-unadopted",
                "user",
                "info",
                note.rel,
                f"{note.rel} sits in the plans folder but is not a standard plan",
                "adopt it as a plan: pick an id; the note keeps its text",
                command=f"expnote plan adopt {shlex.quote(note.rel)} "
                f"--id {next_id or '<PLAN_ID>'} --apply" + self.flag,
                stem=note.stem,
                path=self.view.vault_rel(note),
                sha256=self.view.file_sha256(note.rel),
                suggestedId=next_id,
                suggestedTitle=adopt.suggested_title(self.view, note),
            )
            for note in drafts
        ]

    def stale_runs(self) -> tuple[list[dict], list[dict]]:
        names = linked_workspaces(self.view.context)
        stale, problems = (
            stale_running_runs(names, self.view.clock()) if names else ([], {})
        )
        warnings = [
            {"code": "RUNS_UNAVAILABLE", "message": f"workspace {w}: {why}"}
            for w, why in problems.items()
        ]
        by_workspace: dict[str, list[dict]] = {}
        for run in stale:
            by_workspace.setdefault(run["workspace"], []).append(run)
        items = [
            _item(
                "stale-running-runs",
                "agent",
                "info",
                workspace,
                f"{len(runs)} running run(s) older than 2 days with no result in "
                f"workspace {workspace}",
                "mark them finished/failed or stop them",
                command=f"expnote validate --workspace {workspace} --json",
                runs=[r["runId"] for r in runs[:20]],
            )
            for workspace, runs in sorted(by_workspace.items())
        ]
        return items, warnings


def derived_todos(
    view: ProjectView, include_runs: bool = False
) -> tuple[list[dict], list[dict]]:
    """(items, warnings) for the catalogue of the design (section 9.4)."""
    derive = _Derive(view)
    items = [
        *derive.decisions_proposed(),
        *derive.evidence(),
        *derive.plans(),
        *derive.gate_blocked(),
        *derive.reopen_unmarked(),
        *derive.open_deviations(),
        *derive.reviews_due(),
        *derive.conflict_files(),
        *derive.unadopted_plan_drafts(),
    ]
    warnings: list[dict] = []
    if include_runs:
        stale, warnings = derive.stale_runs()
        items += stale
    return items, warnings


def manual_todos(view: ProjectView, include_done: bool = False) -> list[dict]:
    todo_file = view.profile.todo_file
    if not todo_file:
        return []
    rows = view.conn.execute(
        "SELECT t.todo_id, t.synthetic, t.state, t.line, t.text, t.fields_json "
        "FROM todos t JOIN files f ON f.id = t.src_file_id WHERE f.rel = ? "
        "ORDER BY t.line",
        (view.vault_rel(todo_file),),
    ).fetchall()
    items = []
    for row in rows:
        if row["state"] == "done" and not include_done:
            continue
        fields = json.loads(row["fields_json"])
        items.append(
            {
                "id": row["todo_id"],
                "source": "manual",
                "kind": "manual",
                "owner": fields.get("owner", "user"),
                "severity": "info",
                "ref": row["todo_id"],
                "message": row["text"],
                "action": None,
                "plan": fields.get("plan"),
                "command": None,
                "state": row["state"],
                "due": fields.get("due"),
                "by": fields.get("by"),
                "line": row["line"],
                "synthetic": bool(row["synthetic"]),
            }
        )
    return items


def sort_todos(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda i: (
            i["owner"] != "user",
            SEVERITY_RANK.get(i["severity"], 3),
            i["kind"],
            i["ref"],
        ),
    )


def all_todos(
    view: ProjectView,
    *,
    source: str = "all",
    include_runs: bool = True,
    include_done: bool = False,
) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    warnings: list[dict] = []
    if source in ("derived", "all"):
        derived, warnings = derived_todos(view, include_runs=include_runs)
        items += derived
    if source in ("manual", "all"):
        items += manual_todos(view, include_done)
    return sort_todos(items), warnings


def filter_todos(
    items: list[dict],
    *,
    owner: str | None = None,
    kind: str | None = None,
    plan: str | None = None,
) -> list[dict]:
    return [
        i
        for i in items
        if (not owner or i["owner"] == owner)
        and (not kind or i["kind"] == kind)
        and (not plan or i["plan"] == plan)
    ]


def next_steps(view: ProjectView, owner: str = "agent") -> tuple[dict, list[dict]]:
    items, warnings = all_todos(view, source="all", include_runs=True)
    mine = [i for i in items if i["owner"] == owner]
    other = [i for i in items if i["owner"] != owner]

    def priority(item: dict) -> tuple[int, str]:
        kind = item["kind"]
        rank = (
            NEXT_PRIORITY.index(kind) if kind in NEXT_PRIORITY else len(NEXT_PRIORITY)
        )
        return rank, item["ref"]

    steps = [
        {
            "order": number,
            "kind": item["kind"],
            "ref": item["ref"],
            "plan": item["plan"],
            "message": item["message"],
            "action": item["action"],
            "command": item["command"],
        }
        for number, item in enumerate(sorted(mine, key=priority), start=1)
    ]
    waiting = {
        "owner": "user" if owner == "agent" else "agent",
        "count": len(other),
        "items": [
            {
                "id": i["id"],
                "kind": i["kind"],
                "ref": i["ref"],
                "message": i["message"],
                "action": i["action"],
            }
            for i in other
        ],
    }
    return {"owner": owner, "steps": steps, "waiting": waiting}, warnings
