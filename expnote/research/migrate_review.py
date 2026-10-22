"""Review tables of the migration plan (design 11.1): proposals, never applied.

The tables are plain data (JSON-able dicts) plus a Markdown rendering. Judgement items
(plan membership of legacy evidence, flagged decision rows, plan status, final RES,
hygiene, outside notes) are only proposed here; `migrate apply` reads a table's
decision back only through the coordinator's override files.
"""

from __future__ import annotations

import re
from pathlib import Path

from expnote.research.checks import label_state
from expnote.research.decisions import parse_decision_ref
from expnote.research.migrate_texts import migration_words, word_list
from expnote.research.model import Note
from expnote.research.snapshot import VaultSnapshot
from expnote.research.textkit import (
    fold,
    is_attachment_name,
    iter_links,
    link_stem,
    markdown_tables,
    split_table_row,
)

EV_STATUS_KEEP = ("rendered", "labelled", "label-na")


def words(profile, key: str) -> re.Pattern | None:
    """An alternation of the profile's migration words for `key` (None if none)."""
    found = word_list(profile, key)
    return re.compile("|".join(re.escape(w) for w in found), re.I) if found else None


def flagged_rows(profile) -> dict[int, str]:
    section = migration_words(profile).get("flagged_decision_rows")
    if not isinstance(section, dict):
        return {}
    return {int(k): str(v) for k, v in section.items() if str(k).isdigit()}


def any_match(pattern: re.Pattern | None, text: str) -> bool:
    return bool(pattern and pattern.search(text))


def md_cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


# ---- plan pages ------------------------------------------------------------------


def plan_pages(snap: VaultSnapshot) -> dict[str, Note]:
    """Plan id -> its page (a non-archived page wins over an archived draft)."""
    pages: dict[str, Note] = {}
    shape = (
        re.compile(snap.profile.plan_id_regex) if snap.profile.plan_id_regex else None
    )
    for note in snap.registry:
        ident = note.get("id")
        if note.type == "plan" and isinstance(ident, str):
            if shape is not None and not shape.fullmatch(ident):
                continue
            current = pages.get(ident)
            if current is None or (
                snap.is_archived(current) and not snap.is_archived(note)
            ):
                pages[ident] = note
    return pages


def plan_ids_in(text: str, known: list[str]) -> list[str]:
    """Known plan ids mentioned in `text` (longest id first so -gammafix wins)."""
    found = []
    rest = text
    for pid in sorted(known, key=len, reverse=True):
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(pid) + r"(?![\w-])")
        if pattern.search(rest):
            found.append(pid)
            rest = pattern.sub(" ", rest)
    return sorted(found, key=lambda p: text.find(p))


def _links_of(value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, str):
            out += [fold(link_stem(t)) for _, t in iter_links(item)]
    return out


# ---- (a) legacy EV -> plan ----------------------------------------------------------


def legacy_evidence(snap: VaultSnapshot) -> list[Note]:
    return [n for n in snap.registry if n.type == "evidence" and n.schema is None]


def infer_ev_plans(snap: VaultSnapshot, overrides: dict | None = None) -> list[dict]:
    overrides = overrides or {}
    pages = plan_pages(snap)
    known = sorted(pages)
    id_by_stem = {fold(page.stem): pid for pid, page in pages.items()}
    results = [n for n in snap.registry if snap.entity_type(n) == "result"]
    result_plan = {n.cstem: id_by_stem.get(n.link_field("plan") or "") for n in results}
    rows = []
    for note in sorted(legacy_evidence(snap), key=lambda n: n.rel):
        votes: dict[str, list[dict]] = {}

        def vote(plan_id, kind, detail, votes=votes):
            if plan_id:
                votes.setdefault(plan_id, []).append({"kind": kind, "detail": detail})

        own = id_by_stem.get(note.link_field("plan") or "")
        marker = migration_words(snap.profile).get("data_layer_marker")
        if own:
            vote(own, "frontmatter-plan", note.get("plan"))
        for pid in plan_ids_in(note.stem, known):
            vote(pid, "file-name", pid)
        for result in results:
            if note.cstem in _links_of(result.get("evidence")):
                vote(result_plan.get(result.cstem), "result-backref", result.stem)
        result_link = note.link_field("result")
        if result_link:
            vote(result_plan.get(result_link), "own-result-key", result_link)
        for line in note.body.split("\n"):
            if marker is None or marker not in line:
                continue
            for stem in _links_of(line):
                vote(id_by_stem.get(stem), "data-layer-reference", stem)
                vote(result_plan.get(stem), "data-layer-reference", stem)
            for pid in plan_ids_in(line, known):
                vote(pid, "data-layer-reference", pid)
        kinds = {p: {v["kind"] for v in vs} for p, vs in votes.items()}
        proposed, confidence = None, "none"
        if own:
            proposed, confidence = own, "high"
        elif len(votes) == 1:
            (proposed,) = votes
            details = {v["detail"] for v in votes[proposed]}
            agree = len(kinds[proposed]) >= 2 or len(details) >= 2
            confidence = "high" if agree else "medium"
        elif len(votes) > 1:
            proposed = max(votes, key=lambda p: (len(kinds[p]), len(votes[p]), p))
            confidence = "low"
        state = label_state(note.body, snap.profile)
        override = overrides.get(note.rel, "unset")
        applied = proposed if confidence == "high" else None
        if override != "unset":
            applied = override
        rows.append(
            {
                "ev": note.rel,
                "stem": note.stem,
                "status": note.get("status") or "",
                "currentPlan": own,
                "proposedPlan": proposed,
                "confidence": confidence,
                "evidence": [
                    {**v, "plan": p} for p, vs in sorted(votes.items()) for v in vs
                ],
                "appliedPlan": applied,
                "override": None if override == "unset" else override,
                "labelState": state,
                "statusAfter": (
                    note.get("status")
                    if note.get("status") in EV_STATUS_KEEP
                    else "label-na"
                ),
                "labelNote": (
                    "label table is filled: candidate for `labelled` (decide "
                    "labelled_by); migration maps the status to label-na"
                    if state == "filled" and note.get("status") != "labelled"
                    else ""
                ),
            }
        )
    return rows


# ---- (b) flagged decision rows -------------------------------------------------------


def rewrite_notes_originals(snap: VaultSnapshot) -> dict[int, tuple[str, str]]:
    """Original status text and the reviewer note from the status-rewrite table."""
    found: dict[int, tuple[str, str]] = {}
    header_word = migration_words(snap.profile).get("rewrite_table_header")
    if not isinstance(header_word, str) or not header_word:
        return found
    for note in snap.notes:
        for table in markdown_tables(note.body):
            header = split_table_row(table[0]) or []
            if len(header) < 4 or header[0] != "#" or header_word not in header[1]:
                continue
            for line in table[2:]:
                cells = split_table_row(line) or []
                if len(cells) >= 4 and cells[0].isdigit():
                    found[int(cells[0])] = (cells[1], cells[3])
    return found


def flagged_decision_rows(snap: VaultSnapshot) -> list[dict]:
    originals = rewrite_notes_originals(snap)
    rows = []
    for number, reason in sorted(flagged_rows(snap.profile).items()):
        decision = snap.decisions.get(number)
        if decision is None:
            rows.append({"number": number, "reason": reason, "missing": True})
            continue
        partial_word = migration_words(snap.profile).get("partial_word")
        partial = isinstance(partial_word, str) and partial_word in decision.status_raw
        original, note = originals.get(number, ("", ""))
        rows.append(
            {
                "number": number,
                "reason": reason,
                "currentStatusCell": decision.status_raw,
                "originalStatus": original,
                "reviewerNote": note,
                "proposedStatus": decision.status or "confirmed",
                "partialRevisedBy": re.findall(r"#(\d+)", decision.status_raw)
                if partial
                else [],
                "firstClause": decision.decision_text[:90],
                "proposal": (
                    "keep confirmed; name the revising decision(s) in the successor's "
                    "body (no partial-supersede state exists)"
                    if reason == "partial revision"
                    else "confirmed (execution status belongs in the execution log)"
                    if reason == "execution-status word"
                    else "confirmed; coordinator to confirm the original wording"
                ),
            }
        )
    return rows


# ---- (c) legacy plan status ----------------------------------------------------------


def legacy_format_plans(snap: VaultSnapshot) -> list[Note]:
    pages = plan_pages(snap)
    return [
        p
        for _, p in sorted(pages.items())
        if p.schema is None and not snap.is_archived(p)
    ]


def _numbers(value) -> list[int]:
    items = value if isinstance(value, list) else [value]
    return [
        int(n)
        for item in items
        if isinstance(item, str)
        for n in re.findall(r"#(\d+)", item)
    ]


def _published_cards(snap: VaultSnapshot, plan_id: str) -> list[Note]:
    return [
        m
        for m in snap.plan_members(plan_id)
        if snap.entity_type(m) == "result"
        and m.get("status") in ("published", "confirmed")
    ]


def legacy_plan_status(snap: VaultSnapshot) -> list[dict]:
    rows = []
    for page in legacy_format_plans(snap):
        plan_id = page.get("id")
        read = [
            {
                "number": d.number,
                "date": d.date,
                "status": d.status,
                "firstClause": d.decision_text[:80],
                "why": why,
            }
            for d, why in snap.decisions_for_plan(plan_id)
        ]
        approving = words(snap.profile, "approval_words")
        closing_words = words(snap.profile, "closing_words")
        approvals = _numbers(page.get("approval"))
        mentioned = [
            r["number"]
            for r in read
            if plan_id in snap.decisions[r["number"]].decision_text
            and any_match(approving, snap.decisions[r["number"]].decision_text)
        ]
        closing = [
            r["number"]
            for r in read
            if any_match(closing_words, snap.decisions[r["number"]].decision_text)
            and plan_id in snap.decisions[r["number"]].decision_text
        ]
        supersession = _numbers(page.get("supersession_decision"))
        current = page.get("status") or ""
        if page.get("superseded_by"):
            proposed, basis = "superseded", "`superseded_by` key present"
        elif current in ("closed", "superseded", "withdrawn", "draft"):
            proposed, basis = current, "status already in the schema 3 vocabulary"
        elif closing:
            proposed, basis = "closed", f"decision(s) {closing} close the plan"
        elif _published_cards(snap, plan_id):
            proposed, basis = "closed", "stage cards published; no closing row found"
        elif approvals or mentioned:
            proposed, basis = (
                "approved",
                f"approval text / rows name {approvals or mentioned}",
            )
        else:
            proposed, basis = "draft", "no approving decision found"
        rows.append(
            {
                "plan": plan_id,
                "path": page.rel,
                "currentStatus": current,
                "proposedStatus": proposed,
                "basis": basis,
                "approvalDecisions": approvals,
                "approvalCandidates": mentioned,
                "proposedApprovedDecision": (approvals or mentioned or [None])[0],
                "closingCandidates": closing,
                "proposedClosedDecision": (supersession or closing or [None])[-1]
                if proposed in ("closed", "superseded")
                else None,
                "supersededBy": page.get("superseded_by"),
                "decisionsRead": read,
                "needsCoordinator": True,
            }
        )
    return rows


# ---- (d) closed legacy plans without a final RES -------------------------------------


def missing_final_res(snap: VaultSnapshot, status_rows: list[dict]) -> list[dict]:
    pages = plan_pages(snap)
    proposed = {r["plan"]: r["proposedStatus"] for r in status_rows}
    res_prefix = snap.profile.raw.get("ids", {}).get("res_prefix") or "RES-"
    summary_words = words(snap.profile, "final_card_words")
    final_words = words(snap.profile, "final_result_words")
    rows = []
    for plan_id, page in sorted(pages.items()):
        if snap.is_archived(page):
            continue
        legacy = page.schema is None or page.is_true("legacy")
        if not legacy:
            continue
        members = snap.plan_members(plan_id)
        cards = [m for m in members if snap.entity_type(m) == "result"]
        final = [
            c
            for c in cards
            if c.get("id") == f"{res_prefix}{plan_id}" or any_match(final_words, c.stem)
        ]
        if final or not cards:
            continue
        current = page.get("status") or ""
        promotable = [c.stem for c in cards if any_match(summary_words, c.stem)]
        rows.append(
            {
                "plan": plan_id,
                "currentStatus": current,
                "proposedStatus": proposed.get(plan_id, current),
                "closedLike": "closed" in (current, proposed.get(plan_id)),
                "stageCards": [
                    {
                        "stem": c.stem,
                        "path": c.rel,
                        "status": c.get("status"),
                        "archived": snap.is_archived(c),
                    }
                    for c in sorted(cards, key=lambda c: c.rel)
                ],
                "promotableSummaryCards": promotable,
                "note": (
                    "an existing summary card could be promoted to the final RES"
                    if promotable
                    else "no summary card exists; a final RES must be drafted"
                ),
            }
        )
    return rows


# ---- helpers shared with the apply steps ---------------------------------------------


def figures_of(note: Note, attachment_exts) -> list[str]:
    seen: list[str] = []
    for embed, target in iter_links(note.body):
        name = link_stem(target)
        if embed and is_attachment_name(name, attachment_exts) and name not in seen:
            seen.append(name)
    return seen


def decision_number(value, profile) -> int | None:
    return parse_decision_ref(value, profile, lenient=True)


def relative(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()
