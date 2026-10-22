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
        "Markdown notes in the vault are the truth for plans, evidence and results; "
        "decisions are records in a SQLite store outside the vault (text never "
        "changes: supersede instead) with a JSONL backup export in the vault; the "
        "index is rebuildable and lives outside the vault",
        "Every command takes --project ID (default: the active project) and --json",
        "--json prints one envelope: ok, schemaVersion, command, data, warnings, meta",
        "Reads refresh the index first (--no-refresh skips it); they never write "
        "the vault",
        "Agents edit prose bodies and agent-owned frontmatter keys with their own "
        "file tools; CLI-only keys (status gates, hashes, confirmations) go "
        "through commands",
        "Never invent a user decision: confirmations need the user's own words "
        "(--authorization) or a click in the GUI",
        "The GUI writes only: approve/reject a decision, close a plan, save EV "
        "labels, add/tick a TODO, close a deviation",
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
        "first time (set up a vault project)": [
            _step(
                "expnote project init --project-dir <PROJECT_FOLDER_IN_VAULT> "
                "--images-dir <FIGURES_FOLDER_IN_VAULT>",
                "dry-run: shows the profile note it would create (or reuses the "
                "one in the folder), the registry entry and the next commands; the "
                "vault root is the nearest folder with .obsidian/ (--vault-root "
                "overrides); --id/--name default to the folder",
            ),
            _step(
                "expnote project init --project-dir <PROJECT_FOLDER_IN_VAULT> "
                "--images-dir <FIGURES_FOLDER_IN_VAULT> --apply",
                "creates the profile note if missing, registers the project and "
                "makes it active when none is; running it again changes nothing",
            ),
            _step(
                "expnote check --project <ID> --json", "FAIL 0 means the vault is sound"
            ),
            _step("expnote web", "the localhost GUI"),
        ],
        "start a plan": [
            _step(
                'expnote new plan --id <PLAN_ID> --title "<TITLE>" --apply',
                "then write the body",
                available=True,
            ),
            _step(
                'expnote decision new --title "<TITLE>" --layer <LAYER> '
                '--decided "<WHAT>" --reason "<WHY>" --plans <PLAN_ID> '
                '--targets "[[<PLAN_NOTE>]]" --apply',
                "records a proposed decision; add --status confirmed "
                '--authorization "<USER_WORDS>" when the user already said yes in '
                "chat",
                available=True,
            ),
            _step(
                "expnote decision set <DECISION_ID> --status confirmed "
                '--authorization "<USER_WORDS>" --apply',
                "only after the user approved; or the user clicks in the GUI",
                available=True,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to approved "
                "--decision <DECISION_ID> --apply",
                "",
                available=True,
            ),
        ],
        "run a stage": [
            _step(
                "expnote new exec --plan <PLAN_ID> --stage <STAGE> "
                "--title <TITLE> --apply",
                "write the rules section",
                available=True,
            ),
            _step(
                "expnote exec freeze <EXEC_ID> --rules-decision <DECISION_ID> --apply",
                "stores the rules hash; editing the rules afterwards needs a "
                "registered deviation",
                available=True,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to running --apply",
                "",
                available=True,
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
                available=True,
            ),
            _step("expnote check --plan <PLAN_ID> --json", "FAIL must be 0"),
        ],
        "close a plan": [
            _step(
                "expnote new res --plan <PLAN_ID> --title <TITLE> --apply",
                "write the final result",
                available=True,
            ),
            _step(
                "expnote res confirm <RES_ID> --decision <DECISION_ID> "
                "--deviation-impact <none|weak|moderate|affects> --apply",
                "needs a confirmed decision that links the result",
                available=True,
            ),
            _step(
                "expnote plan advance <PLAN_ID> --to read-out --apply",
                "then --to confirmed, then --to closed --decision <DECISION_ID>",
                available=True,
            ),
            _step(
                'expnote plan close <PLAN_ID> --reason "<WHY>" '
                '--authorization "<USER_WORDS>" --apply',
                "the user's shortcut: records a confirmed decision and closes the "
                "plan; add --force when the final result is missing or unconfirmed "
                "(check keeps a WARN)",
                available=True,
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
        "final-res-pending",
        "decision-review-due",
        "sync-conflict-file",
        "stale-running-runs",
        "ev-unmapped",
    ],
    "exitCodes": EXIT_CODES,
    "availability": "every writing command is a dry-run until --apply (exit 4 "
    "means nothing was written); confirmations by CLI need --authorization with "
    "the user's own words (exit 7 without it); exit 5 means the note changed, was "
    "edited a moment ago or is held open: re-read and retry",
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
