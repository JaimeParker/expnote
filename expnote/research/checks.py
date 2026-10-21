"""Content checks over a VaultSnapshot, with stable finding codes.

Every check is a pure function of the snapshot; nothing here reads or writes the
file system. Messages are English; `where` follows the project-relative note path.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import PurePosixPath

from expnote.research.decisions import parse_decision_ref
from expnote.research.frontmatter import has_nested_map, scalar_is_quoted
from expnote.research.model import Decision, Finding, FindingSink, Note
from expnote.research.profile import Profile
from expnote.research.schemas import (
    DEV_STATUS,
    DEVIATION_IMPACT,
    LABELLED_BY,
    LEGACY_RELAXED,
    SUPPORTED_SCHEMAS,
    field_table,
)
from expnote.research.snapshot import VaultSnapshot
from expnote.research.textkit import (
    HTML_COMMENT_RE,
    WIKILINK_RE,
    find_section,
    hash_section,
    is_attachment_name,
    iter_links,
    link_stem,
    markdown_tables,
    nfc,
    normalize_for_hash,
    reserved_name_chars,
    section_text,
    sha256_text,
    split_table_row,
    strip_code,
)

# Codes vault.py never had; `--compat vault-py` suppresses them.
NEW_CODES = frozenset(
    {
        "GEN.STATUS_TAIL_PRESENT",
        "PROSE.RAW_NUMBERS",
        "SCHEMA.OLD",
        "DEC.AUTH_WEAK",
        "DEC.BODY_CHANGED",
        "DEC.NO_OPTIONS",
        "FM.NESTED_MAP",
    }
)

LEVEL_RANK = {"FAIL": 0, "WARN": 1, "INFO": 2}


@dataclasses.dataclass
class CheckResult:
    findings: list[Finding]
    stats: dict
    scope: dict


# ---- note helpers -------------------------------------------------------------


def label_state(body: str, profile: Profile) -> str:
    """'missing' | 'empty' | 'partial' | 'filled' for the user-label table."""
    section = section_text(body, lambda t: any(k in t for k in profile.heading_labels))
    if section is None:
        return "missing"
    tables = markdown_tables(HTML_COMMENT_RE.sub("", section))
    if not tables:
        return "empty"
    column = profile.label_value_columns[0]
    rows = [
        r for r in (split_table_row(x) for x in tables[0][2:]) if r and len(r) > column
    ]
    done = [r for r in rows if r[column].strip()]
    if not done:
        return "empty"
    return "filled" if len(done) == len(rows) else "partial"


def observation_nonempty(body: str, profile: Profile) -> bool:
    section = section_text(body, lambda t: t.startswith(profile.heading_observation))
    if section is None:
        return False
    return bool(HTML_COMMENT_RE.sub("", section).strip())


def rules_section(body: str, profile: Profile) -> str | None:
    return section_text(body, lambda t: t.strip().startswith(profile.heading_rules))


def rules_sha256(body: str, profile: Profile) -> str | None:
    section = rules_section(body, profile)
    return None if section is None else hash_section(section)


# ---- field validation ---------------------------------------------------------

_LINK_RE = re.compile(r"\[\[[^\[\]]+\]\]")


def _value_ok(kind: str, value, choices, profile: Profile, lenient: bool) -> bool:
    if kind == "int":
        return isinstance(value, str) and bool(re.fullmatch(r"\d+", value))
    if kind == "ints":
        return isinstance(value, list) and all(
            isinstance(v, str) and re.fullmatch(r"\d+", v) for v in value
        )
    if kind == "date":
        return isinstance(value, str) and bool(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        )
    if kind == "bool":
        return value in ("true", "false")
    if kind == "link":
        return isinstance(value, str) and bool(_LINK_RE.fullmatch(value.strip()))
    if kind == "links":
        return isinstance(value, list) and all(
            isinstance(v, str) and _LINK_RE.fullmatch(v.strip()) for v in value
        )
    if kind == "strs":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == "enum":
        return value in choices
    if kind == "sha256":
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))
    if kind == "str":
        return isinstance(value, str)
    if kind == "decision-id":
        return parse_decision_ref(value, profile, lenient=True) is not None
    if kind == "decision-ids":
        return isinstance(value, list) and all(
            parse_decision_ref(v, profile, lenient=True) is not None for v in value
        )
    return True


def validate_fields(
    snap: VaultSnapshot, note: Note, entity: str, sink: FindingSink
) -> None:
    profile = snap.profile
    version = note.schema or ""
    table = field_table(entity, version, profile.decision_layers)
    if table is None:
        return
    where = note.rel
    relaxed = LEGACY_RELAXED.get(entity, set()) if note.is_true("legacy") else set()
    for key, spec in table.items():
        value = note.fm.get(key)
        node = note.fm_nodes.get(key)
        if value is None or value == "" or value == []:
            if spec.required and key not in relaxed:
                sink.add(
                    "FAIL",
                    "SCHEMA.MISSING_FIELD",
                    where,
                    f"missing required field `{key}` ({spec.kind}, written by "
                    f"{spec.writer})",
                    field=key,
                )
            continue
        kind = spec.kind
        if (
            kind in ("link", "links")
            and isinstance(value, list)
            and any(isinstance(v, list) for v in value)
        ):
            sink.add(
                "FAIL",
                "SCHEMA.UNQUOTED",
                where,
                f"`{key}` holds an unquoted wikilink (YAML parsed it as a nested "
                "list); "
                'write "[[...]]"',
                field=key,
            )
            continue
        if not _value_ok(kind, value, spec.choices, profile, version == "3"):
            hint = f" (allowed: {' / '.join(spec.choices)})" if spec.choices else ""
            sink.add(
                "FAIL",
                "SCHEMA.BAD_VALUE",
                where,
                f"`{key}` value {value!r} does not match type {kind}{hint}",
                field=key,
            )
            continue
        if spec.quoted and node is not None:
            yaml_module_nodes = node.value if isinstance(node.value, list) else [node]
            if not all(scalar_is_quoted(s) for s in yaml_module_nodes):
                sink.add(
                    "FAIL",
                    "SCHEMA.UNQUOTED",
                    where,
                    f"`{key}` must be quoted (YAML would rewrite ids)",
                    field=key,
                )
    unknown = sorted(set(note.fm) - set(table) - profile.common_keys)
    if unknown:
        sink.add(
            "INFO",
            "SCHEMA.UNKNOWN_KEYS",
            where,
            f"keys outside the schema (kept, not validated): {', '.join(unknown)}",
            keys=unknown,
        )


# ---- decisions referenced from notes --------------------------------------------


def _decision_ok(
    snap: VaultSnapshot,
    value,
    sink: FindingSink,
    where: str,
    what: str,
    gate_code: str,
    *,
    lenient: bool,
) -> Decision | None:
    number = parse_decision_ref(value, snap.profile, lenient=lenient)
    if number is None:
        sink.add("FAIL", gate_code, where, f"{what}: decision reference missing")
        return None
    row = snap.decisions.get(number)
    if row is None:
        sink.add("FAIL", gate_code, where, f"{what}: decision #{number} does not exist")
        return None
    if row.status != "confirmed":
        sink.add(
            "FAIL",
            "DEC.NOT_CONFIRMED",
            where,
            f"{what}: decision #{number} is {row.status or row.status_raw!r}, "
            "must be confirmed",
        )
    return row


# ---- lifecycle ----------------------------------------------------------------


def _is_active_result(note: Note) -> bool:
    return note.get("status") not in ("superseded", "withdrawn")


def _supported(note: Note) -> bool:
    return note.schema in SUPPORTED_SCHEMAS


def _check_plan(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    where = note.rel
    lenient = note.schema == "3"
    status = note.get("status")
    legacy = note.is_true("legacy")
    order = ("draft", "approved", "running", "read-out", "confirmed", "closed")
    rank = order.index(status) if status in order else -1
    members = snap.plan_members(note.get("id", ""))
    results = [m for m in members if snap.entity_type(m) == "result"]
    active = [r for r in results if _is_active_result(r)]
    if len(active) > 1:
        sink.add(
            "FAIL",
            "LIFE.RES_MULTIPLE_ACTIVE",
            where,
            "a plan may have one result that is not superseded/withdrawn: "
            + ", ".join(sorted(r.stem for r in active)),
        )
    if rank >= order.index("approved"):
        _decision_ok(
            snap,
            note.get("approved_decision"),
            sink,
            where,
            "status >= approved needs an approving decision",
            "LIFE.PLAN_NO_APPROVAL",
            lenient=lenient,
        )
    if rank >= order.index("running") and not legacy:
        frozen = [
            m
            for m in members
            if snap.entity_type(m) == "exec-plan"
            and m.get("status") in ("frozen", "running", "done")
        ]
        if not frozen:
            sink.add(
                "FAIL",
                "LIFE.PLAN_NO_FROZEN_EXEC",
                where,
                "status >= running needs at least one frozen (frozen/running/done) "
                "exec-plan",
            )
    if rank >= order.index("read-out"):
        if len(active) != 1:
            sink.add(
                "FAIL",
                "LIFE.PLAN_RES_COUNT",
                where,
                "status >= read-out needs exactly one final result (found "
                f"{len(active)})",
            )
        if not legacy:
            for member in members:
                if snap.entity_type(member) == "evidence" and member.get(
                    "status"
                ) not in (
                    "labelled",
                    "label-na",
                ):
                    sink.add(
                        "FAIL",
                        "LIFE.PLAN_EV_UNLABELLED",
                        where,
                        "status >= read-out needs every evidence page labelled; "
                        "unlabelled: "
                        f"{member.stem}",
                    )
    if rank >= order.index("confirmed"):
        result = note.link_field("result")
        if result is None:
            sink.add(
                "FAIL",
                "LIFE.PLAN_RESULT_MISSING",
                where,
                "status >= confirmed needs `result` pointing at the final result note",
            )
        else:
            target = snap.note(result)
            if target is None or snap.entity_type(target) != "result":
                sink.add(
                    "FAIL",
                    "LIFE.PLAN_RESULT_INVALID",
                    where,
                    "`result` does not point at a result note",
                )
            elif target.get("status") != "confirmed":
                level = "WARN" if legacy and lenient else "FAIL"
                sink.add(
                    level,
                    "LIFE.PLAN_RESULT_NOT_CONFIRMED",
                    where,
                    f"final result status is {target.get('status')!r}; it must be "
                    "confirmed before the plan is confirmed/closed",
                )
            if active and result not in {a.cstem for a in active}:
                sink.add(
                    "FAIL",
                    "LIFE.PLAN_RESULT_NOT_ACTIVE",
                    where,
                    "`result` is not the plan's single active result",
                )
    if status == "closed":
        _decision_ok(
            snap,
            note.get("closed_decision"),
            sink,
            where,
            "closed needs a closing decision",
            "LIFE.PLAN_NO_CLOSING",
            lenient=lenient,
        )
    if status in ("superseded", "withdrawn"):
        _decision_ok(
            snap,
            note.get("closed_decision"),
            sink,
            where,
            f"{status} needs a recorded decision (closed_decision)",
            "LIFE.PLAN_TERMINAL",
            lenient=lenient,
        )
        if status == "superseded":
            successor = note.link_field("superseded_by")
            target = snap.note(successor) if successor else None
            if target is None or target.type != "plan":
                sink.add(
                    "FAIL",
                    "LIFE.PLAN_TERMINAL",
                    where,
                    "superseded needs `superseded_by` pointing at an existing plan",
                )


def _check_exec_plan(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    profile = snap.profile
    where = note.rel
    status = note.get("status")
    legacy = note.is_true("legacy")
    if status == "closed" and not legacy:
        sink.add(
            "FAIL",
            "LIFE.EXEC_CLOSED_NOT_LEGACY",
            where,
            "status=closed is for legacy exec docs only; use done/aborted",
        )
    if rules_section(note.body, profile) is None and not legacy:
        sink.add(
            "FAIL",
            "EXEC.NO_RULES_SECTION",
            where,
            f"missing the rules section (heading starting {profile.heading_rules!r})",
        )
    if status in ("frozen", "running", "done") and not legacy:
        _decision_ok(
            snap,
            note.get("rules_decision"),
            sink,
            where,
            "freezing needs the user-confirmed rules decision",
            "LIFE.EXEC_NO_RULES_DECISION",
            lenient=note.schema == "3",
        )
        stored = note.get("rules_sha256")
        if not stored:
            sink.add(
                "FAIL",
                "LIFE.EXEC_NO_RULES_SHA",
                where,
                "status >= frozen but there is no rules_sha256 (freeze first)",
            )
        elif rules_sha256(note.body, profile) != stored:
            sink.add(
                "FAIL",
                "EXEC.RULES_CHANGED",
                where,
                "rules section changed after freezing (sha256 mismatch); register a "
                "deviation and re-freeze with --amend",
            )
    amended = note.get("rules_amended_by")
    for deviation_id in amended if isinstance(amended, list) else []:
        if deviation_id not in snap.deviations:
            sink.add(
                "FAIL",
                "EXEC.AMEND_UNREGISTERED",
                where,
                f"rules_amended_by cites {deviation_id}, which is not in the "
                "deviation register",
            )


def _validated_type(note: Note) -> str | None:
    return note.type if _supported(note) else None


def _check_evidence(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    profile = snap.profile
    where = note.rel
    lenient = note.schema == "3"
    legacy = note.is_true("legacy")
    status = note.get("status")
    body = note.body
    appendix = note.link_field("appendix")
    twin = snap.note(appendix) if appendix else None
    if twin is None and not legacy:
        sink.add(
            "FAIL",
            "EV.NO_APPENDIX",
            where,
            "evidence page has no appendix twin (`appendix` does not resolve to an "
            "evidence-appendix note)",
        )
    elif twin is not None:
        if twin.type != "evidence-appendix":
            sink.add(
                "FAIL",
                "EV.TWIN_MISMATCH",
                where,
                f"`appendix` points at a note with type={twin.type!r}",
            )
        if twin.link_field("parent") != note.cstem:
            sink.add(
                "FAIL",
                "EV.TWIN_MISMATCH",
                where,
                "the appendix `parent` does not point back here",
            )
    if not legacy and note.get("exec_plan"):
        exec_note = snap.note(note.link_field("exec_plan") or "")
        if exec_note is None or _validated_type(exec_note) != "exec-plan":
            sink.add(
                "FAIL",
                "EV.EXEC_PLAN_INVALID",
                where,
                "`exec_plan` does not point at a note with type exec-plan",
            )
    state = label_state(body, profile)
    if status == "labelled":
        if state != "filled":
            sink.add(
                "FAIL",
                "EV.LABEL_NOT_FILLED",
                where,
                f"status=labelled but the label table is {state}",
            )
        labelled_by = note.get("labelled_by")
        if labelled_by not in LABELLED_BY:
            sink.add(
                "FAIL",
                "EV.LABELLED_BY_MISSING",
                where,
                "status=labelled needs labelled_by (user / agent-for-user)",
            )
        authorization = str(note.get("label_authorization") or "")
        if labelled_by == "agent-for-user" and not authorization:
            sink.add(
                "FAIL",
                "EV.AUTHORIZATION_MISSING",
                where,
                "labels were filled by an agent but label_authorization is empty",
            )
        elif re.fullmatch(r"#?\d+", authorization) or (
            lenient
            and parse_decision_ref(authorization, profile, lenient=True) is not None
        ):
            _decision_ok(
                snap,
                authorization if lenient else authorization.lstrip("#"),
                sink,
                where,
                "label_authorization decision",
                "EV.AUTHORIZATION_UNRESOLVED",
                lenient=lenient,
            )
    elif status == "label-na":
        if state == "filled":
            sink.add(
                "WARN",
                "EV.LABEL_NA_BUT_FILLED",
                where,
                "status=label-na but the label table is filled: should be labelled",
            )
    else:
        if observation_nonempty(body, profile):
            sink.add(
                "FAIL",
                "EV.OBSERVATION_BEFORE_LABEL",
                where,
                "no observation text before labelling (status rendered)",
            )
        if state == "filled":
            sink.add(
                "WARN",
                "EV.LABEL_FILLED_STATUS_RENDERED",
                where,
                "label table is filled but status is still rendered",
            )
    if legacy:
        return
    label_block = find_section(
        body, lambda t: any(k in t for k in profile.heading_labels)
    )
    lines = body.split("\n")
    rest = (
        "\n".join(lines[: label_block[0]] + lines[label_block[1] :])
        if label_block
        else body
    )
    tables = markdown_tables(rest)
    if len(tables) > 1:
        sink.add(
            "FAIL",
            "EV.EXTRA_TABLES",
            where,
            "evidence page may hold at most one endpoint table besides the label "
            f"table (found {len(tables)})",
        )
    if (
        twin is not None
        and len(tables) == 1
        and tables[0] not in markdown_tables(twin.body)
    ):
        sink.add(
            "WARN",
            "EV.ENDPOINT_NOT_IN_APPENDIX",
            where,
            "the endpoint table matches no table in the appendix row for row",
        )
    embeds = {link_stem(t).casefold() for embed, t in iter_links(body) if embed}
    figures = note.get("figures")
    for figure in figures if isinstance(figures, list) else []:
        if not snap.resolve(figure):
            sink.add(
                "FAIL",
                "EV.FIGURE_UNRESOLVED",
                where,
                f"figure {figure} not found in the vault",
            )
        if figure.casefold() not in embeds:
            sink.add(
                "WARN",
                "EV.FIGURE_NOT_EMBEDDED",
                where,
                f"figure {figure} is not embedded in the body",
            )


def _check_appendix(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    parent = note.link_field("parent")
    page = snap.note(parent) if parent else None
    if page is None:
        sink.add(
            "FAIL",
            "EV.APPENDIX_NO_PARENT",
            note.rel,
            "the appendix `parent` does not resolve to an evidence page",
        )
    elif page.link_field("appendix") != note.cstem:
        sink.add(
            "FAIL",
            "EV.TWIN_MISMATCH",
            note.rel,
            "the evidence page `appendix` does not point back here",
        )


def _check_result(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    where = note.rel
    lenient = note.schema == "3"
    status = note.get("status")
    legacy_exempt = lenient and note.is_true("legacy")
    if status == "confirmed":
        if legacy_exempt:
            if not note.get("confirmed_decision"):
                sink.add(
                    "INFO",
                    "RES.LEGACY_UNCONFIRMED",
                    where,
                    "legacy result without a confirming decision",
                )
        else:
            row = _decision_ok(
                snap,
                note.get("confirmed_decision"),
                sink,
                where,
                "a confirmed result needs its confirming decision",
                "RES.CONFIRM_NO_DECISION",
                lenient=lenient,
            )
            if row is not None and not row.cites_stem(note.cstem):
                sink.add(
                    "FAIL",
                    "RES.DECISION_NOT_LINKED",
                    where,
                    f"decision #{row.number} does not link this result",
                )
            if note.get("deviation_impact") not in DEVIATION_IMPACT:
                sink.add(
                    "FAIL",
                    "RES.NO_IMPACT",
                    where,
                    "a confirmed result needs deviation_impact "
                    "(none/weak/moderate/affects)",
                )
    if status == "superseded":
        successor_stem = note.link_field("superseded_by")
        successor = snap.note(successor_stem) if successor_stem else None
        if successor is None:
            sink.add(
                "FAIL",
                "RES.SUPERSEDED_INVALID",
                where,
                "status=superseded needs superseded_by pointing at an existing result",
            )
        elif successor.link_field("plan") != note.link_field("plan"):
            sink.add(
                "FAIL",
                "RES.SUPERSEDED_INVALID",
                where,
                "superseded_by points at a result of a different plan",
            )
    evidence = note.get("evidence")
    for item in evidence if isinstance(evidence, list) else []:
        if isinstance(item, str):
            for _, target in iter_links(item):
                if not snap.resolve(target):
                    sink.add(
                        "FAIL",
                        "RES.EVIDENCE_UNRESOLVED",
                        where,
                        f"evidence link [[{target}]] not found",
                    )


def _check_decision_file(snap: VaultSnapshot, note: Note, sink: FindingSink) -> None:
    profile = snap.profile
    where = note.rel
    status = note.get("status")
    legacy = note.is_true("legacy")
    number = parse_decision_ref(note.get("number"), profile, lenient=False)
    identifier = note.get("id")
    if number is not None and isinstance(identifier, str):
        expected = f"{profile.decision_id_prefix}-{number:04d}"
        if identifier != expected:
            sink.add(
                "FAIL",
                "SCHEMA.BAD_VALUE",
                where,
                f"`id` {identifier!r} should be {expected!r} for number {number}",
            )
    if status == "confirmed" and not legacy:
        for key in ("confirmed_by", "confirmed_on", "body_sha256"):
            if not note.get(key):
                sink.add(
                    "FAIL",
                    "SCHEMA.MISSING_FIELD",
                    where,
                    f"confirmed decision needs `{key}`",
                    field=key,
                )
    if status == "superseded" and not note.get("superseded_by"):
        sink.add(
            "FAIL",
            "SCHEMA.MISSING_FIELD",
            where,
            "superseded decision needs `superseded_by`",
            field="superseded_by",
        )
    exempt = legacy or (number is not None and number < profile.decision_strict_from)
    if not exempt:
        for heading in profile.heading_decision_required:
            if (
                find_section(note.body, lambda t, h=heading: t.strip().startswith(h))
                is None
            ):
                sink.add(
                    "FAIL",
                    "DEC.SECTION_MISSING",
                    where,
                    f"missing the {heading!r} section",
                )
        options = profile.heading_decision_options
        if (
            options
            and find_section(note.body, lambda t: t.strip().startswith(options)) is None
        ):
            level = (
                "WARN" if note.get("layer") in profile.options_warn_layers else "INFO"
            )
            sink.add(
                level,
                "DEC.NO_OPTIONS",
                where,
                f"no {options!r} section listing considered options",
            )
    if note.get("confirmed_by") == "agent-for-user":
        authorization = str(note.get("authorization") or "").strip()
        if not authorization:
            sink.add(
                "FAIL",
                "DEC.AGENT_NO_AUTH",
                where,
                "agent-confirmed decision has no authorization",
            )
        else:
            reference = parse_decision_ref(authorization, profile, lenient=True)
            cited = snap.decisions.get(reference) if reference is not None else None
            if (
                not (cited is not None and cited.status == "confirmed")
                and len(authorization) < 8
            ):
                sink.add(
                    "WARN",
                    "DEC.AUTH_WEAK",
                    where,
                    f"authorization {authorization!r} is shorter than 8 characters",
                )
    stored = note.get("body_sha256")
    if (
        status == "confirmed"
        and stored
        and sha256_text(normalize_for_hash(note.body)) != stored
    ):
        sink.add(
            "WARN",
            "DEC.BODY_CHANGED",
            where,
            "body changed after confirmation; amend by superseding",
        )


# ---- prose ---------------------------------------------------------------------

_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-\d[\w-]*|#\d+")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_LIST_NUMBERING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\d+(?:\.\d+)*[.)]?)(?=[ \t])", re.M
)
_URL_RE = re.compile(r"https?://\S+")
_NUMBER_RES = (
    re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])"),
    re.compile(r"(?<![\w.])\d+(?:\.\d+)?[ \t]*%"),
    re.compile(
        r"(?<![\w.])\d+[ \t]*"
        r"(?:steps?|seeds?|runs?|epochs?|episodes?|ms|s|k|M|GB|MB|Hz|px)\b"
    ),
)


def raw_number_hits(text: str, profile: Profile) -> list[str]:
    cleaned = strip_code(text, comments=True)
    cleaned = WIKILINK_RE.sub(" ", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)
    for pattern in profile.number_allow_regexes:
        cleaned = re.sub(pattern, " ", cleaned)
    cleaned = _ID_RE.sub(" ", cleaned)
    cleaned = _DATE_RE.sub(" ", cleaned)
    cleaned = _LIST_NUMBERING_RE.sub(" ", cleaned)
    hits: list[str] = []
    for pattern in _NUMBER_RES:
        hits += [m.group(0).strip() for m in pattern.finditer(cleaned)]
    return hits


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    visible = strip_code(text)  # markers quoted in code are documentation, not blocks
    return any(marker in visible for marker in markers)


def _check_prose(where: str, text: str, profile: Profile, sink: FindingSink) -> None:
    hits = raw_number_hits(text, profile)
    if hits:
        sink.add(
            "WARN",
            "PROSE.RAW_NUMBERS",
            where,
            f"{len(hits)} raw number(s) outside evidence pages, e.g. "
            f"{', '.join(hits[:3])}",
            count=len(hits),
            samples=hits[:5],
        )


# ---- whole-vault checks ----------------------------------------------------------


def _check_parse(notes: list[Note], sink: FindingSink, stats: dict) -> None:
    bad = 0
    for note in notes:
        if note.fm_error:
            bad += 1
            code = (
                "FM.DUPLICATE_KEY"
                if note.fm_error_kind == "duplicate_key"
                else "FM.PARSE"
            )
            sink.add("FAIL", code, note.rel, note.fm_error)
        elif note.type != "project-profile" and has_nested_map(note.fm):
            sink.add(
                "WARN",
                "FM.NESTED_MAP",
                note.rel,
                "nested map in frontmatter (a stray ': ' in a list item?); only the "
                "project profile note may nest",
            )
    stats["parse"] = {"notes": len(notes), "failed": bad}


def _check_uniqueness(snap: VaultSnapshot, sink: FindingSink, stats: dict) -> None:
    groups = 0
    for key, paths in sorted(snap.md_index.items()):
        if len(paths) > 1:
            groups += 1
            sink.add(
                "FAIL",
                "NAME.DUPLICATE_STEM",
                key,
                "note file name (case-insensitive) is duplicated: "
                + "; ".join(sorted(paths)),
            )
    for key, paths in sorted(snap.attach_index.items()):
        if len(paths) > 1:
            groups += 1
            sink.add(
                "FAIL",
                "NAME.DUPLICATE_ATTACHMENT",
                key,
                "attachment file name (case-insensitive) is duplicated: "
                + "; ".join(sorted(paths)),
            )
    for paths in [*snap.md_index.values(), *snap.attach_index.values()]:
        for path in paths:
            bad = reserved_name_chars(PurePosixPath(path).name)
            if bad:
                sink.add(
                    "WARN",
                    "NAME.RESERVED_CHAR",
                    path,
                    f"file name contains wikilink-reserved characters {' '.join(bad)}; "
                    "a [[...]] link is cut at that point",
                )
    stats["names"] = {
        "notes": sum(len(v) for v in snap.md_index.values()),
        "attachments": sum(len(v) for v in snap.attach_index.values()),
        "duplicateGroups": groups,
    }


def _check_links(
    snap: VaultSnapshot, scope: list[Note], sink: FindingSink, stats: dict
) -> None:
    profile = snap.profile
    exts = profile.attachment_exts
    prefix = profile.images_required_prefix.casefold()
    images_rel = snap.images_rel.casefold()
    unresolved = 0
    for note in scope:
        for embed, target in iter_links(note.text):
            hits = snap.resolve(target)
            if not hits:
                unresolved += 1
                image = embed and is_attachment_name(target, exts)
                sink.add(
                    "FAIL" if image else "WARN",
                    "LINK.IMAGE_UNRESOLVED" if image else "LINK.UNRESOLVED",
                    note.rel,
                    f"{'image embed' if image else 'link'} not found: [[{target}]]",
                )
            elif embed and prefix and link_stem(target).casefold().startswith(prefix):
                if not any(h.casefold().startswith(images_rel + "/") for h in hits):
                    sink.add(
                        "WARN",
                        "LINK.IMAGE_OUTSIDE_DIR",
                        note.rel,
                        f"image with prefix {profile.images_required_prefix!r} is "
                        "not in "
                        f"{snap.images_rel}: {target}",
                    )
    stats["links"] = {"checked": len(scope), "unresolved": unresolved}
    if snap.images is not None:
        referenced = {
            link_stem(t).casefold() for n in snap.notes for _, t in iter_links(n.text)
        }
        orphans = [i for i in snap.images if nfc(i).casefold() not in referenced]
        sink.add(
            "INFO",
            "LINK.ORPHAN_IMAGES",
            snap.images_rel,
            f"images dir holds {len(snap.images)} files, {len(orphans)} unreferenced "
            "inside the project",
            total=len(snap.images),
            orphans=len(orphans),
        )
        stats["images"] = {"total": len(snap.images), "orphans": len(orphans)}
    else:
        sink.add(
            "WARN",
            "LINK.IMAGES_DIR_MISSING",
            snap.images_rel,
            "images directory does not exist",
        )


def _check_decisions(
    snap: VaultSnapshot, sink: FindingSink, detail: set[int], stats: dict
) -> None:
    strict_from = snap.profile.decision_strict_from
    for code, where, message in snap.decision_problems:
        sink.add("FAIL", code, where, message)
    tally: dict[str, int] = {}
    for number in sorted(snap.decisions):
        row = snap.decisions[number]
        key = (row.status or "unmapped") + ("/legacy" if row.legacy else "")
        key += "/review" if row.review and row.legacy else ""
        tally[key] = tally.get(key, 0) + 1
        if not row.legacy and row.status is None:
            sink.add(
                "FAIL",
                "DEC.STATUS_VOCAB",
                row.where,
                f"status {row.status_raw!r}: "
                f"{row.review or 'not in the status vocabulary'}",
            )
        if row.legacy and number in detail:
            note = f"; needs review: {row.review}" if row.review else ""
            succ = f" -> #{row.successor}" if row.successor else ""
            sink.add(
                "WARN" if row.review else "INFO",
                "DEC.LEGACY_STATUS",
                row.where,
                f"old status {row.status_raw[:40]!r} -> suggested "
                f"{row.status or '(none)'}{succ}{note}",
            )
        if row.status == "superseded":
            successor = (
                snap.decisions.get(row.successor) if row.successor is not None else None
            )
            if successor is None:
                sink.add(
                    "FAIL",
                    "DEC.SUPERSEDED_NO_SUCCESSOR",
                    row.where,
                    "superseded must name an existing successor #N",
                )
            elif not successor.refers_back_to(number):
                level = "WARN" if successor.number >= strict_from else "INFO"
                sink.add(
                    level,
                    "DEC.NO_BACKREF",
                    row.where,
                    f"successor #{successor.number} does not refer back to #{number}",
                )
    stats["decisions"] = {
        "count": len(snap.decisions),
        "statusTally": dict(sorted(tally.items())),
    }


def _check_deviations(
    snap: VaultSnapshot, sink: FindingSink, plan_id: str | None, stats: dict
) -> None:
    profile = snap.profile
    rows = list(snap.deviations.values())
    with_status = [d for d in rows if d.status is not None]
    opened = [d.did for d in rows if d.status == "open"]
    stats["deviations"] = {
        "count": len(rows),
        "statusColumn": bool(with_status),
        "open": opened,
    }
    if with_status:
        for row in rows:
            if row.status not in DEV_STATUS:
                sink.add(
                    "FAIL",
                    "DEV.STATUS_VOCAB",
                    row.did,
                    f"status {row.status!r} not in {'/'.join(DEV_STATUS)}",
                )
            if row.found not in profile.deviation_found_values:
                sink.add(
                    "FAIL",
                    "DEV.FOUND_VOCAB",
                    row.did,
                    f"found-time {row.found!r} not in "
                    f"{'/'.join(profile.deviation_found_values)}",
                )
    if plan_id:
        for row in snap.deviations_for_plan(plan_id):
            if row.status is None:
                if row.closed:
                    sink.add(
                        "INFO",
                        "DEV.CLOSED_BY_DISPOSITION",
                        row.did,
                        "disposition marks it closed",
                    )
                else:
                    sink.add(
                        "WARN",
                        "DEV.OPEN_FOR_PLAN",
                        row.did,
                        "disposition does not say closed; the register has no status "
                        "column",
                    )
            elif row.status == "open":
                sink.add(
                    "WARN",
                    "DEV.OPEN_FOR_PLAN",
                    row.did,
                    "this plan's deviation is still open",
                )


# ---- driver --------------------------------------------------------------------------


def run_checks(snap: VaultSnapshot, plan_id: str | None = None) -> CheckResult:
    profile = snap.profile
    sink = FindingSink()
    stats: dict = {}
    registry = snap.registry
    if plan_id:
        page = snap.plan_page(plan_id)
        if page is None:
            sink.add(
                "FAIL",
                "SCHEMA.PLAN_NOT_FOUND",
                plan_id,
                "no note with type plan and a matching id",
            )
            scope: list[Note] = []
        else:
            scope = [page, *snap.plan_members(plan_id)]
    else:
        scope = registry
    ids: dict[tuple[str, str], list[str]] = {}
    for note in registry:
        if snap.is_archived(note):
            continue
        if note.type in ("plan", "exec-plan", "result") and isinstance(
            note.get("id"), str
        ):
            ids.setdefault((note.type, note.get("id")), []).append(note.rel)
    for (kind, identifier), rels in sorted(ids.items()):
        if len(rels) > 1:
            sink.add(
                "WARN",
                "SCHEMA.ID_DUPLICATE",
                identifier,
                f"{kind} id is duplicated (archive included): "
                + "; ".join(sorted(rels)),
            )
    _check_parse(scope if plan_id else snap.notes, sink, stats)
    _check_uniqueness(snap, sink, stats)
    _check_links(snap, scope, sink, stats)
    detail: set[int] = set()
    plan_decisions: list[Decision] = []
    if plan_id:
        plan_decisions = [d for d, _ in snap.decisions_for_plan(plan_id)]
        detail = {d.number for d in plan_decisions}
    _check_decisions(snap, sink, detail, stats)
    _check_deviations(snap, sink, plan_id, stats)
    for note in scope:
        if note.fm_error or note.type == "project-profile":
            continue
        if profile.forbidden_markers and _has_marker(
            note.text, profile.forbidden_markers
        ):
            level = "FAIL" if profile.forbidden_marker_level == "fail" else "WARN"
            sink.add(
                level,
                "GEN.STATUS_TAIL_PRESENT",
                note.rel,
                "generated block markers are present; plan bodies are human-owned",
            )
        if note.schema == "2" and profile.schema == "3":
            sink.add(
                "INFO",
                "SCHEMA.OLD",
                note.rel,
                "note uses schema 2; the project is on schema 3",
            )
        if not _supported(note):
            if note.type in profile.legacy_unmigrated_types:
                sink.add(
                    "INFO",
                    "SCHEMA.LEGACY_FORMAT",
                    note.rel,
                    f"legacy note (type={note.type}, no supported schema); "
                    "not validated against the schema",
                )
            continue
        entity = note.type
        if (
            field_table(entity or "", note.schema or "", profile.decision_layers)
            is None
        ):
            continue
        validate_fields(snap, note, entity, sink)
        if entity == "plan":
            _check_plan(snap, note, sink)
            if not note.is_true("legacy"):
                _check_prose(note.rel, note.body, profile, sink)
        elif entity == "exec-plan":
            _check_exec_plan(snap, note, sink)
        elif entity == "evidence":
            _check_evidence(snap, note, sink)
        elif entity == "evidence-appendix":
            _check_appendix(snap, note, sink)
        elif entity == "result":
            _check_result(snap, note, sink)
            if not note.is_true("legacy"):
                _check_prose(note.rel, note.body, profile, sink)
        elif entity == "decision":
            _check_decision_file(snap, note, sink)
    strict_from = profile.decision_strict_from
    for number in sorted(snap.decisions):
        decision = snap.decisions[number]
        if plan_id and number not in detail:
            continue
        if number >= strict_from and not decision.legacy:
            body = decision.text if decision.source == "table" else decision.note.body
            _check_prose(decision.where, body, profile, sink)
    items = sorted(
        sink.items, key=lambda f: (LEVEL_RANK[f.level], f.code, f.where, f.message)
    )
    return CheckResult(
        items,
        stats,
        {"plan": plan_id, "notes": len(scope), "projectNotes": len(snap.notes)},
    )


def split_new_codes(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    regular = [f for f in findings if f.code not in NEW_CODES]
    new = [f for f in findings if f.code in NEW_CODES]
    return regular, new


def summarize(findings: list[Finding]) -> dict[str, int]:
    counts = {"fail": 0, "warn": 0, "info": 0}
    for finding in findings:
        counts[finding.level.lower()] += 1
    return counts
