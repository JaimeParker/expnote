"""`migrate plan`: the mechanical steps as data, a unified diff, review tables."""

from __future__ import annotations

import os
from pathlib import Path

from expnote.research import migrate_backup as backup
from expnote.research import migrate_review as review
from expnote.research import migrate_templates as templates
from expnote.research.errors import ResearchError
from expnote.research.migrate import (
    STEP_NAMES,
    Migration,
    build_step,
    full_diff,
    load_overrides,
    now_stamp,
    remember_run_stems,
    resolve_step,
    run_link_stems,
    step_changes_json,
)
from expnote.research.migrate_texts import migration_words
from expnote.research.migrate_verify import DELETABLE_DOT_DIRS
from expnote.research.textkit import fold, link_stem
from expnote.research.writeback import sha256_bytes

PLAN_JSON = "migration-plan.json"
PLAN_DIFF = "migration.diff"
TABLES = {
    "a": ("review-a-ev-plan", "legacy EV -> proposed plan"),
    "b": ("review-b-decision-rows", "flagged decision rows"),
    "c": ("review-c-plan-status", "legacy plan status proposals"),
    "d": ("review-d-final-res", "closed legacy plans without a final RES"),
    "e": ("review-e-hygiene", "hygiene items"),
    "f": ("review-f-outside-notes", "outside notes and their run-link rewrites"),
}


# ---- (e) hygiene and (f) outside notes ---------------------------------------------


def _dot_dir_proposal(name: str, has_contents: bool) -> str:
    if has_contents:
        return "kept; coordinator decides (not empty)"
    if name in DELETABLE_DOT_DIRS:
        return "delete at M5 (empty)"
    return "kept; coordinator decides (empty)"


def hygiene_items(m: Migration, snap) -> list[dict]:
    items: list[dict] = []
    project = m.project_dir
    for dirpath, dirnames, _ in os.walk(project):
        for name in sorted(dirnames):
            if not name.startswith("."):
                continue
            path = Path(dirpath) / name
            contents = sorted(
                p.relative_to(path).as_posix() for p in path.rglob("*")
            )
            items.append(
                {
                    "kind": "dot-directory",
                    "path": path.relative_to(m.vault_root).as_posix(),
                    "empty": not contents,
                    "contents": contents[:20],
                    "proposal": _dot_dir_proposal(name, bool(contents)),
                }
            )
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    from expnote.research.migrate import renameable_notes

    for source, target in renameable_notes(m, snap):
        from expnote.research.textkit import fold as _fold

        linking = [
            n.rel for n in snap.notes if source.stem in n.text and n.path != source
        ]
        items.append(
            {
                "kind": "rename",
                "path": source.relative_to(m.vault_root).as_posix(),
                "proposedName": target.name,
                "linksMustFollow": linking,
                "key": _fold(source.stem),
                "proposal": "rename (ASCII double quote is not valid on Windows)",
            }
        )
    for preview in sorted(project.rglob("_refactor_preview")):
        files = sorted(p.relative_to(preview).as_posix() for p in preview.rglob("*"))
        items.append(
            {
                "kind": "preview-folder",
                "path": preview.relative_to(m.vault_root).as_posix(),
                "empty": not files,
                "contents": files[:20],
                "proposal": "delete after the backup (M5)",
            }
        )
    section = m.profile.raw.get("templates")
    folder = (section.get("dir") if isinstance(section, dict) else None) or "templates"
    tdir = project / folder
    if tdir.is_dir():
        code = sorted(
            p.relative_to(tdir).as_posix()
            for p in tdir.rglob("*")
            if p.is_file() and p.suffix in (".py", ".pyc")
        )
        items.append(
            {
                "kind": "vault-code",
                "path": tdir.relative_to(m.vault_root).as_posix(),
                "empty": False,
                "contents": code,
                "proposal": "delete code and tests after verify (M5); kept in backup",
            }
        )
    for projection in m.projections():
        items.append(
            {
                "kind": "projection",
                "path": projection.root.relative_to(m.vault_root).as_posix()
                if projection.root
                else None,
                "workspace": projection.workspace,
                "stillEnabled": projection.enabled,
                "proposal": "step 8 turns it off; M5 deletes it (backup checked)",
            }
        )
    return items


def outside_note_rows(m: Migration) -> list[dict]:
    stems, roots = run_link_stems(m)
    from expnote.research.migrate import _runs_text

    rows = []
    for entry in backup.outside_notes(m.vault_root, m.project_dir, roots, set(stems)):
        if entry["archived"]:
            rows.append({**entry, "rewrites": [], "action": "left alone (archived)"})
            continue
        text = (m.vault_root / entry["path"]).read_text(encoding="utf-8")
        from expnote.research.textkit import iter_links

        seen: list[dict] = []
        for _, target in iter_links(text):
            stem = link_stem(target)
            if fold(stem) in stems and stem not in [s["id"] for s in seen]:
                seen.append({"id": stem, "replacement": f"`{stem}`"})
        rows.append(
            {
                **entry,
                "rewrites": seen[:30],
                "wouldChange": _runs_text(text, stems=frozenset(stems)) != text,
                "action": "rewrite run/analysis links to plain `id` text (step 7)",
            }
        )
    return rows


# ---- rendering -----------------------------------------------------------------------


def _render_a(rows: list[dict]) -> str:
    table = review.md_table(
        [
            "EV",
            "status",
            "current plan",
            "proposed plan",
            "confidence",
            "evidence",
            "applied",
            "label table",
        ],
        [
            [
                r["ev"],
                r["status"],
                r["currentPlan"] or "",
                r["proposedPlan"] or "(none)",
                r["confidence"],
                "; ".join(
                    sorted({f"{e['kind']}:{e['plan']}" for e in r["evidence"]})
                )[:160],
                r["appliedPlan"] or "(held)",
                r["labelState"] + (" -> candidate: labelled" if r["labelNote"] else ""),
            ]
            for r in rows
        ],
    )
    return (
        "# (a) Legacy EV -> proposed plan\n\n`plan:` is written only for confidence "
        "high (or a coordinator override in plan-overrides.json); the other rows stay "
        "unmigrated.\n\n" + table
    )


def _render_b(rows: list[dict]) -> str:
    table = review.md_table(
        ["#", "why flagged", "current status cell", "original", "proposed", "proposal"],
        [
            [
                r["number"],
                r["reason"],
                r.get("currentStatusCell", "(missing)"),
                r.get("originalStatus", ""),
                r.get("proposedStatus", ""),
                r.get("proposal", ""),
            ]
            for r in rows
        ],
    )
    return "# (b) Flagged decision rows\n\n" + table


def _render_c(rows: list[dict]) -> str:
    out = ["# (c) Legacy plan status proposals\n"]
    out.append(
        review.md_table(
            ["plan", "current", "proposed", "basis", "approved by", "closed by"],
            [
                [
                    r["plan"],
                    r["currentStatus"],
                    r["proposedStatus"],
                    r["basis"],
                    r["proposedApprovedDecision"] or "",
                    r["proposedClosedDecision"] or "",
                ]
                for r in rows
            ],
        )
    )
    for r in rows:
        out.append(f"\n## {r['plan']}: decision rows read\n")
        out.append(
            review.md_table(
                ["#", "date", "status", "first clause", "why"],
                [
                    [
                        d["number"],
                        d["date"],
                        d["status"],
                        d["firstClause"],
                        ",".join(d["why"]),
                    ]
                    for d in r["decisionsRead"]
                ],
            )
        )
    return "\n".join(out)


def _render_d(rows: list[dict]) -> str:
    out = ["# (d) Closed legacy plans lacking a final RES\n"]
    out.append(
        review.md_table(
            [
                "plan",
                "current",
                "proposed",
                "stage cards",
                "promotable summary card",
                "note",
            ],
            [
                [
                    r["plan"],
                    r["currentStatus"],
                    r["proposedStatus"],
                    len(r["stageCards"]),
                    "; ".join(r["promotableSummaryCards"]) or "(none)",
                    r["note"],
                ]
                for r in rows
            ],
        )
    )
    for r in rows:
        out.append(f"\n## {r['plan']}: stage cards\n")
        out.append(
            review.md_table(
                ["card", "status", "archived"],
                [[c["stem"], c["status"], c["archived"]] for c in r["stageCards"]],
            )
        )
    return "\n".join(out)


def _render_e(rows: list[dict]) -> str:
    return "# (e) Hygiene\n\n" + review.md_table(
        ["kind", "path", "empty", "detail", "proposal"],
        [
            [
                r["kind"],
                r.get("path") or "",
                r.get("empty", ""),
                (
                    f"-> {r['proposedName']}; links: {len(r['linksMustFollow'])}"
                    if r["kind"] == "rename"
                    else ", ".join(r.get("contents", [])[:8])
                ),
                r["proposal"],
            ]
            for r in rows
        ],
    )


def _render_f(rows: list[dict]) -> str:
    return "# (f) Outside notes\n\n" + review.md_table(
        ["note", "links", "archived", "action", "first ids"],
        [
            [
                r["path"],
                r["links"],
                r["archived"],
                r["action"],
                ", ".join(w["id"] for w in r["rewrites"][:6]),
            ]
            for r in rows
        ],
    )


RENDERERS = {
    "a": _render_a,
    "b": _render_b,
    "c": _render_c,
    "d": _render_d,
    "e": _render_e,
    "f": _render_f,
}


# ---- the plan ------------------------------------------------------------------------


def check_profile_exists(m: Migration) -> None:
    note_path = m.context.resolved.profile_note
    if not note_path.is_file():
        raise ResearchError(
            "PROFILE_NOT_FOUND",
            f"the profile note {note_path} does not exist",
            exit_code=3,
            remediation="expnote migrate profile --apply, then approve the note",
        )


def build_plan(m: Migration) -> dict:
    check_profile_exists(m)
    snap = m.snapshot()
    m.remember_projection()
    remember_run_stems(m)
    from expnote.research.migrate import legacy_table_text

    legacy_table_text(m, save=True)
    status_rows = review.legacy_plan_status(snap)
    tables = {
        "a": review.infer_ev_plans(snap, load_overrides(m)),
        "b": review.flagged_decision_rows(snap),
        "c": status_rows,
        "d": review.missing_final_res(snap, status_rows),
        "e": hygiene_items(m, snap),
        "f": outside_note_rows(m),
    }
    diff_parts: list[str] = []
    steps = []
    for step in STEP_NAMES:
        splan = (
            build_step(m, step, assume_decisions=True)
            if step == 3
            else build_step(m, step)
        )
        resolved = resolve_step(m, splan)
        steps.append(step_changes_json(m, resolved))
        for change in resolved.changes:
            diff_parts.append(f"# step {step} ({splan.name})\n{full_diff(change)}")
        for path in splan.deletes:
            rel = path.relative_to(m.vault_root).as_posix()
            diff_parts.append(
                f"# step {step} ({splan.name})\n--- a/{rel}\n+++ /dev/null\n"
                "(file deleted; its pre-image goes to the journal)"
            )
        for source, target in splan.renames:
            old = source.relative_to(m.vault_root).as_posix()
            new = target.relative_to(m.vault_root).as_posix()
            head = f"# step {step} ({splan.name})"
            diff_parts.append(f"{head}\nrename {old}\n    -> {new}")
    written = {}
    for key, (stem, _) in TABLES.items():
        m.write_json(f"{stem}.json", tables[key])
        m.write_text(f"{stem}.md", RENDERERS[key](tables[key]) + "\n")
        written[key] = str(m.state_path(f"{stem}.md"))
    layering_path = m.project_dir / "templates"
    proposals = []
    layering_name = migration_words(m.profile).get("layering_note")
    layering = (
        next(iter(m.project_dir.rglob(f"{layering_name}.md")), None)
        if layering_name
        else None
    )
    if layering is not None:
        m.write_text(
            "proposed--layering-convention.md",
            templates.propose_layering_text(
                m.profile, layering.read_text(encoding="utf-8")
            ),
        )
        proposals.append(str(m.state_path("proposed--layering-convention.md")))
    moc = next(iter(m.project_dir.glob("*MOC.md")), None)
    if moc is not None:
        m.write_text(
            "proposed--moc-wording.md",
            templates.propose_moc_wording(m.profile, moc.read_text(encoding="utf-8")),
        )
        proposals.append(str(m.state_path("proposed--moc-wording.md")))
    del layering_path
    m.write_text(PLAN_DIFF, "\n".join(diff_parts) + "\n")
    plan = {
        "project": m.project_id,
        "generatedAt": now_stamp(),
        "profileSha256": m.context.profile_sha256,
        "vaultRoot": str(m.vault_root),
        "projectDir": str(m.project_dir),
        "steps": steps,
        "tables": {
            k: {"name": TABLES[k][1], "rows": len(v)} for k, v in tables.items()
        },
        "tableFiles": written,
        "proposals": proposals,
        "diff": str(m.state_path(PLAN_DIFF)),
        "planSha256": sha256_bytes(
            "\n".join(diff_parts).encode("utf-8")
        ),
    }
    m.write_json(PLAN_JSON, plan)
    plan["planFile"] = str(m.state_path(PLAN_JSON))
    return plan
