"""Decisions (legacy table rows, store rows) and the deviation register."""

from __future__ import annotations

import dataclasses
import re

from expnote.research.decision_store import read_rows
from expnote.research.model import Decision, DeviationRow
from expnote.research.profile import Profile, StatusRule
from expnote.research.schemas import (
    DECISION_COLUMN_FIELDS,
    DECISION_STATUS,
    DEVIATION_COLUMN_FIELDS,
    DEVIATION_REQUIRED_COLUMNS,
)
from expnote.research.textkit import (
    fold,
    is_separator_row,
    iter_links,
    link_stem,
    normalize_text,
    split_table_row,
)

NEW_STATUS_RE = re.compile(r"^(proposed|confirmed|superseded|rejected)\b(.*)$", re.S)


@dataclasses.dataclass
class StatusMap:
    status: str | None
    successor: int | None
    review: str | None
    legacy: bool


def map_decision_status(
    raw: str, number: int, strict_from: int, rules: tuple[StatusRule, ...]
) -> StatusMap:
    raw = raw.strip()
    match = NEW_STATUS_RE.match(raw)
    if match:
        successor = None
        if match.group(1) == "superseded":
            found = re.search(r"#(\d+)", match.group(2))
            successor = int(found.group(1)) if found else None
        return StatusMap(match.group(1), successor, None, False)
    if number >= strict_from:
        return StatusMap(
            None, None, f"must start with one of {'/'.join(DECISION_STATUS)}", False
        )
    for rule in rules:
        if re.search(rule.pattern, raw):
            successor = None
            if rule.status == "superseded":
                found = re.search(r"#(\d+)", raw)
                successor = int(found.group(1)) if found else None
            return StatusMap(rule.status, successor, rule.review, True)
    return StatusMap(None, None, "cannot be mapped automatically", True)


def parse_decision_ref(value, profile: Profile, *, lenient: bool) -> int | None:
    """Decision number from a typed field; lenient also takes `#N` and `PREFIX-NNNN`."""
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"\d+", value):
        return int(value)
    if lenient:
        match = re.fullmatch(
            rf"#(\d+)|{re.escape(profile.decision_id_prefix)}-(\d+)", value
        )
        if match:
            return int(match.group(1) or match.group(2))
    return None


def parse_decision_table(
    text: str, profile: Profile, table_name: str
) -> tuple[list[Decision], list[tuple[str, str, str]]]:
    """Rows of the legacy decision table, plus problems as (code, where, message)."""
    columns = profile.legacy_decision_columns
    problems: list[tuple[str, str, str]] = []
    rows: list[Decision] = []
    index: dict[str, int] | None = None
    header_len = 0
    separator_seen = False
    for line in normalize_text(text).split("\n"):
        cells = split_table_row(line)
        if cells is None:
            continue
        if index is None:
            missing = [
                columns[f] for f in DECISION_COLUMN_FIELDS if columns[f] not in cells
            ]
            if missing:
                problems.append(
                    ("DEC.TABLE_COLUMNS", table_name, f"header lacks columns {missing}")
                )
                return [], problems
            index = {f: cells.index(columns[f]) for f in DECISION_COLUMN_FIELDS}
            header_len = len(cells)
            continue
        if not separator_seen and is_separator_row(cells):
            separator_seen = True
            continue
        if not cells or not re.fullmatch(
            r"\d+", cells[index["number"]] if len(cells) > index["number"] else ""
        ):
            continue
        if len(cells) != header_len:
            problems.append(
                (
                    "DEC.TABLE_COLUMNS",
                    table_name,
                    f"#{cells[index['number']]} has {len(cells)} columns (expected "
                    f"{header_len})",
                )
            )
            continue
        number = int(cells[index["number"]])
        cell = {f: cells[index[f]] for f in DECISION_COLUMN_FIELDS}
        status = map_decision_status(
            cell["status"],
            number,
            profile.decision_strict_from,
            profile.legacy_status_rules,
        )
        rows.append(
            Decision(
                number=number,
                where=f"#{number}",
                source="table",
                status=status.status,
                status_raw=cell["status"],
                legacy=status.legacy,
                review=status.review,
                successor=status.successor,
                layer=cell["layer"],
                date=cell["date"],
                text=" ".join(
                    (cell["decision"], cell["reason"], cell["lands"], cell["status"])
                ),
                decision_text=cell["decision"],
                lands_text=cell["lands"],
            )
        )
    return rows, problems


def decision_from_row(row: dict) -> Decision:
    """A Decision from a decision-store row."""
    targets = tuple(row["targets"])
    parts = [" ".join(targets), row["decided"], row["reason"]]
    parts += [row["options"], row["lands"]]
    return Decision(
        number=row["number"],
        where=f"#{row['number']}",
        source="db",
        status=row["status"],
        status_raw=row["status_raw"],
        legacy=row["legacy"],
        review=row["review"],
        successor=row["superseded_by"],
        decision_id=row["id"],
        layer=row["layer"],
        date=row["date"],
        text="\n".join(p for p in parts if p),
        decision_text=row["decided"],
        lands_text="\n".join(p for p in (row["lands"], " ".join(targets)) if p),
        plans=tuple(row["plans"]),
        supersedes=tuple(row["supersedes"]),
        title=row["title"],
        reason_text=row["reason"],
        options_text=row["options"],
        lands_raw=row["lands"],
        targets=targets,
        confirmed_by=row["confirmed_by"],
        confirmed_on=row["confirmed_on"],
        authorization=row["authorization"],
        review_by=row["review_by"],
        version=row["version"],
        recorded_via=row["source"],
    )


def stored_decisions(project_id: str) -> dict[int, Decision] | None:
    """Decisions of the project's store; None while it has not been created."""
    loaded = read_rows(project_id)
    if loaded is None:
        return None
    return {row["number"]: decision_from_row(row) for row in loaded[0]}


def decision_concerns_plan(
    decision: Decision, plan_id: str, plan_regex: re.Pattern, member_stems: set[str]
) -> list[str]:
    """Why a decision belongs to a plan: ['lands', 'plan-id'] (empty if unrelated)."""
    why = []
    if any(
        fold(link_stem(t)) in member_stems for _, t in iter_links(decision.lands_text)
    ):
        why.append("lands")
    if (
        plan_id in decision.plans
        or plan_regex.search(decision.decision_text)
        or plan_regex.search(decision.lands_text)
    ):
        why.append("plan-id")
    return why


def parse_deviation_log(text: str, profile: Profile) -> list[DeviationRow]:
    """Deviation rows matched by header name (positional order when no header)."""
    names = profile.deviation_columns
    id_pattern = re.compile(profile.deviation_id_regex)
    default_index = {f: i for i, f in enumerate(DEVIATION_COLUMN_FIELDS)}
    index: dict[str, int] = dict(default_index)
    rows = []
    for line in normalize_text(text).split("\n"):
        cells = split_table_row(line)
        if cells is None:
            continue
        if names["id"] in cells and cells[cells.index(names["id"])] == names["id"]:
            index = {f: cells.index(names[f]) for f in names if names[f] in cells}
            continue
        id_at = index.get("id")
        if (
            id_at is None
            or id_at >= len(cells)
            or not id_pattern.fullmatch(cells[id_at])
        ):
            continue
        needed = [index.get(f) for f in DEVIATION_REQUIRED_COLUMNS]
        if any(i is None or i >= len(cells) for i in needed):
            continue

        values = {
            field: cells[i]
            if (i := index.get(field)) is not None and i < len(cells)
            else None
            for field in DEVIATION_COLUMN_FIELDS
        }
        rows.append(
            DeviationRow(
                did=values["id"],
                date=values["date"],
                experiment=values["experiment"],
                content=values["content"],
                impact=values["impact"],
                disposition=values["disposition"],
                cards=values["cards"],
                status=values["status"],
                found=values["found"],
                closed_marker=profile.deviation_closed_marker,
            )
        )
    return rows
