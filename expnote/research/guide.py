"""`guide research`: the agent workflow of the research layer (design section 6.6)."""

from __future__ import annotations

EXIT_CODES = {
    "0": "ok",
    "1": "findings or a failed gate (check with FAIL, plan advance gate)",
    "2": "usage error",
    "3": "not found, ambiguous or project not registered",
    "4": "dry-run, nothing written (add --apply)",
    "5": "conflict (changed file, recently modified, file busy)",
    "6": "environment (vault unavailable, missing dependency, index locked)",
    "7": "refused as user-only",
}


def _step(run: str, note: str = "", available: bool = True) -> dict:
    return {"run": run, "note": note, "available": available}


RESEARCH_GUIDE = {
    "topic": "research",
    "principles": [
        "Markdown notes in the vault are the truth for plans, decisions, evidence "
        "and results; the index is rebuildable and lives outside the vault",
        "Every command takes --project ID (default: the active project) and --json",
        "--json prints one envelope: ok, schemaVersion, command, data, warnings, meta",
        "Reads refresh the index first (--no-refresh skips it); they never write "
        "the vault",
        "Agents edit prose bodies and agent-owned frontmatter keys with their own "
        "file tools; CLI-only keys (status gates, hashes, confirmations) go "
        "through commands",
        "Never invent a user decision: confirmations need the user's own words "
        "(--authorization) or a click in the GUI",
    ],
    "essentialCommands": [
        _step("expnote next --json", "what is actionable now, with exact commands"),
        _step(
            "expnote plan bundle <PLAN_ID> --json",
            "one call: plan, gates, decisions, exec-plans, evidence, results, "
            "related notes, runs, open items, findings; --max-bytes spills big "
            "bundles to a file and returns meta.spillFile and meta.nextCursor",
        ),
        _step("expnote check --json", "FAIL must be 0 before handing work over"),
        _step(
            "expnote search <QUERY> --json",
            "full-text (3+ characters) or substring (shorter) search with "
            "--type/--plan/--status/--linked-to/--backlinks-of/--images",
        ),
        _step("expnote todo list --json", "derived and manual TODOs, user items first"),
    ],
    "lifecycle": {
        "plan": "draft > approved > running > read-out > confirmed > closed "
        "(superseded and withdrawn are terminal)",
        "exec-plan": "draft > frozen > running > done | aborted",
        "evidence": "rendered > labelled | label-na",
        "result": "first-read > confirmed | superseded | withdrawn",
        "decision": "proposed > confirmed > superseded, or proposed > rejected",
        "gates": "plan bundle shows, per next transition, the unmet gate codes",
    },
    "idSchemes": {
        "plan": "from the profile (ids.plan_regex)",
        "decision": "<id_prefix>-<4 digit number>; legacy numbers also read as #N",
        "run": "from the profile (runs.id_regex), linked as [[run id]]",
    },
    "workflows": {
        "start a plan": [
            _step(
                'expnote new plan --id <PLAN_ID> --title "<TITLE>" --apply',
                "then write the body",
                available=False,
            ),
            _step(
                'expnote decision new --title "<TITLE>" --layer <LAYER> '
                '--plans <PLAN_ID> --targets "[[<PLAN_NOTE>]]" --apply',
                "creates a proposed decision",
                available=False,
            ),
            _step(
                "expnote decision set <DECISION_ID> --status confirmed "
                '--authorization "<USER_WORDS>" --apply',
                "only after the user approved; or the user clicks in the GUI",
                available=False,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to approved "
                "--decision <DECISION_ID> --apply",
                "",
                available=False,
            ),
        ],
        "run a stage": [
            _step(
                "expnote new exec --plan <PLAN_ID> --stage <STAGE> "
                "--title <TITLE> --apply",
                "write the rules section",
                available=False,
            ),
            _step(
                "expnote exec freeze <EXEC_ID> --rules-decision <DECISION_ID> --apply",
                "stores the rules hash; editing the rules afterwards needs a "
                "registered deviation",
                available=False,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to running --apply",
                "",
                available=False,
            ),
            _step(
                "register runs with the workspace commands (run add / run update)",
                "servers never write the vault",
            ),
            _step(
                "copy figures into the images directory (project show -> absImagesDir)",
                "use the profile's required prefix and a vault-unique name",
            ),
            _step(
                "expnote new ev --plan <PLAN_ID> --exec <EXEC_ID> "
                "--title <TITLE> --apply",
                "creates the evidence page and its appendix",
                available=False,
            ),
            _step("expnote check --plan <PLAN_ID> --json", "FAIL must be 0"),
        ],
        "close a plan": [
            _step(
                "expnote new res --plan <PLAN_ID> --title <TITLE> --apply",
                "write the final result",
                available=False,
            ),
            _step(
                "expnote res confirm <RES_ID> --decision <DECISION_ID> "
                "--deviation-impact <none|weak|moderate|affects> --apply",
                "needs a confirmed decision that links the result",
                available=False,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to read-out --apply",
                "then --to confirmed, then --to closed --decision <DECISION_ID>",
                available=False,
            ),
        ],
    },
    "derivedTodoKinds": [
        "decision-proposed",
        "ev-unlabelled",
        "plan-awaiting-approval",
        "exec-rules-unconfirmed",
        "res-awaiting-confirmation",
        "decision-confirmed-not-applied",
        "deviation-open",
        "plan-gate-blocked",
        "legacy-no-final-res",
        "decision-review-due",
        "sync-conflict-file",
        "stale-running-runs",
        "ev-unmapped",
    ],
    "exitCodes": EXIT_CODES,
    "availability": "steps with available=false belong to the write set and are "
    "not implemented yet; until then edit the notes directly and keep check at "
    "FAIL 0",
}


def render_research_guide() -> str:
    lines = ["expnote research guide", "", "Principles:"]
    lines += [f"- {item}" for item in RESEARCH_GUIDE["principles"]]
    lines += ["", "Essential commands:"]
    for step in RESEARCH_GUIDE["essentialCommands"]:
        lines.append(f"- {step['run']}  # {step['note']}")
    lines += ["", "Lifecycle:"]
    lines += [f"- {k}: {v}" for k, v in RESEARCH_GUIDE["lifecycle"].items()]
    lines += ["", "Id schemes:"]
    lines += [f"- {k}: {v}" for k, v in RESEARCH_GUIDE["idSchemes"].items()]
    for name, steps in RESEARCH_GUIDE["workflows"].items():
        lines += ["", f"Workflow: {name}"]
        for number, step in enumerate(steps, start=1):
            tag = "" if step["available"] else " [not implemented yet]"
            note = f"  # {step['note']}" if step["note"] else ""
            lines.append(f"{number}. {step['run']}{note}{tag}")
    lines += [
        "",
        "Derived TODO kinds: " + ", ".join(RESEARCH_GUIDE["derivedTodoKinds"]),
    ]
    lines += ["", "Exit codes:"]
    lines += [f"- {code}: {meaning}" for code, meaning in EXIT_CODES.items()]
    lines += ["", RESEARCH_GUIDE["availability"]]
    return "\n".join(lines)
