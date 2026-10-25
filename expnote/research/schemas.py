"""Entity field tables (schema 2 and 3) and the profile key list."""

from __future__ import annotations

import dataclasses

PLAN_STATUS = ("draft", "approved", "running", "read-out", "confirmed", "closed")
PLAN_STATUS_V3 = (*PLAN_STATUS, "superseded", "withdrawn")
EXEC_STATUS = (
    "draft",
    "frozen",
    "running",
    "done",
    "aborted",
    "closed",
)  # closed: legacy
EV_STATUS = ("rendered", "labelled", "label-na")
EV_LABELLED_STATUSES = ("labelled", "label-na")  # any other status still needs labels
DEV_STATUS = ("open", "closed")
RES_STATUS = ("draft", "first-read", "confirmed", "superseded", "withdrawn")
DECISION_STATUS = ("proposed", "confirmed", "superseded", "rejected")
LABELLED_BY = ("user", "agent-for-user")
RES_REVIEWED_BY = ("user", "agent-for-user")
DEVIATION_IMPACT = ("none", "weak", "moderate", "affects")

SUPPORTED_SCHEMAS = ("2", "3")

# Required fields a `legacy: true` note may leave empty.
LEGACY_RELAXED = {"evidence": {"exec_plan", "figures", "appendix"}}

ENTITY_TYPES = (
    "plan",
    "exec-plan",
    "evidence",
    "evidence-appendix",
    "result",
)

DEFAULT_ATTACHMENT_EXTS = frozenset(
    "png jpg jpeg gif svg webp bmp pdf mp4 webm mov mp3 wav canvas base csv json "
    "txt html py ipynb".split()
)
DEFAULT_COMMON_KEYS = frozenset(
    "project date created updated revised aliases tags related author cssclasses "
    "decision description".split()
)
DEFAULT_SKIP_DIRS = frozenset(
    {".git", ".obsidian", ".trash", "__pycache__", ".pytest_cache"}
)


@dataclasses.dataclass(frozen=True)
class Field:
    # str|int|ints|date|bool|link|links|strs|enum|sha256|decision-id|decision-ids
    kind: str
    required: bool
    writer: str  # engine|coordinator|user|agent
    choices: tuple = ()
    quoted: bool = False  # every scalar must be quoted in YAML


_V2: dict[str, dict[str, Field]] = {
    "plan": {
        "schema": Field("int", True, "engine"),
        "type": Field("str", True, "engine"),
        "id": Field("str", True, "coordinator"),
        "title": Field("str", True, "coordinator"),
        "status": Field("enum", True, "coordinator", PLAN_STATUS),
        "owner": Field("str", False, "coordinator"),
        "parent": Field("link", False, "coordinator"),
        "approved_decision": Field("int", False, "coordinator"),
        "closed_decision": Field("int", False, "coordinator"),
        "result": Field("link", False, "coordinator"),
        "legacy": Field("bool", False, "coordinator"),
        "forced_close": Field("bool", False, "cli"),
        "reopened_on": Field("date", False, "coordinator"),
        "earlier_cycle": Field("links", False, "coordinator", quoted=True),
    },
    "exec-plan": {
        "schema": Field("int", True, "engine"),
        "type": Field("str", True, "engine"),
        "id": Field("str", True, "coordinator"),
        "plan": Field("link", True, "coordinator"),
        "stage": Field("str", True, "coordinator"),
        "status": Field("enum", True, "coordinator", EXEC_STATUS),
        "servers": Field("strs", False, "coordinator", quoted=True),
        "expnote_topic": Field("str", False, "agent", quoted=True),
        "rules_decision": Field("int", False, "coordinator"),
        "rules_sha256": Field("sha256", False, "engine"),
        "frozen_on": Field("date", False, "engine"),
        "approved_by": Field("enum", False, "engine", ("agent", "user")),
        "new_rules": Field("bool", False, "coordinator"),
        "rules_amended_by": Field("strs", False, "coordinator", quoted=True),
        "legacy": Field("bool", False, "coordinator"),
    },
    "evidence": {
        "schema": Field("int", True, "engine"),
        "type": Field("str", True, "engine"),
        "plan": Field("link", True, "agent"),
        "exec_plan": Field("link", True, "agent"),
        "question": Field("str", False, "agent"),
        "status": Field("enum", True, "agent/user", EV_STATUS),
        "figures": Field("strs", True, "agent", quoted=True),
        "runs": Field("strs", False, "agent", quoted=True),
        "appendix": Field("link", True, "engine"),
        "labelled_by": Field("enum", False, "user", LABELLED_BY),
        "labelled_on": Field("date", False, "user"),
        "label_authorization": Field("str", False, "coordinator"),
        "legacy": Field("bool", False, "coordinator"),
    },
    "evidence-appendix": {
        "schema": Field("int", True, "engine"),
        "type": Field("str", True, "engine"),
        "parent": Field("link", True, "engine"),
        "plan": Field("link", True, "agent"),
        "runs": Field("strs", False, "agent", quoted=True),
        "expnote_snapshot": Field("str", False, "engine", quoted=True),
        "generated_by": Field("str", False, "engine"),
    },
    "result": {
        "schema": Field("int", True, "engine"),
        "type": Field("str", True, "engine"),
        "id": Field("str", True, "coordinator"),
        "plan": Field("link", True, "coordinator"),
        "status": Field("enum", True, "coordinator", RES_STATUS),
        "evidence": Field("links", True, "coordinator"),
        "decisions": Field("ints", False, "coordinator"),
        "confirmed_decision": Field("int", False, "coordinator"),
        "deviation_impact": Field("enum", False, "coordinator", DEVIATION_IMPACT),
        "superseded_by": Field("link", False, "coordinator"),
        "reviewed_by": Field("str", False, "coordinator"),
        "submitted_on": Field("date", False, "agent"),
        "returned_on": Field("date", False, "user"),
        "reviewed_on": Field("date", False, "user"),
        "review_authorization": Field("str", False, "coordinator"),
    },
}


def _v3_tables() -> dict[str, dict[str, Field]]:
    tables = {entity: dict(fields) for entity, fields in _V2.items()}
    decision_id = Field("decision-id", False, "coordinator")
    plan = tables["plan"]
    plan["status"] = Field("enum", True, "coordinator", PLAN_STATUS_V3)
    plan["approved_decision"] = decision_id
    plan["closed_decision"] = decision_id
    plan["superseded_by"] = Field("link", False, "coordinator")
    plan["legacy_status"] = Field("str", False, "migration")
    tables["exec-plan"]["rules_decision"] = decision_id
    tables["evidence"]["legacy_status"] = Field("str", False, "migration")
    result = tables["result"]
    result["decisions"] = Field("decision-ids", False, "coordinator")
    result["confirmed_decision"] = decision_id
    result["legacy"] = Field("bool", False, "migration")
    return tables


_V3 = _v3_tables()


def field_table(entity: str, schema_version: str):
    """Field table for an entity; None when the entity has none at that schema."""
    return (_V3 if schema_version == "3" else _V2).get(entity)


# Profile keys: dotted path -> (required, kind). kind: str|int|strs|map|maps|regex
PROFILE_KEYS: dict[str, tuple[bool, str]] = {
    "images.required_prefix": (False, "str"),
    "headings.rules": (True, "str"),
    "headings.labels": (True, "strs"),
    "headings.observation": (True, "str"),
    "headings.decision_required": (False, "strs"),
    "headings.decision_options": (False, "str"),
    "headings.decision_consequences": (False, "str"),
    "decisions.id_prefix": (True, "str"),
    "decisions.strict_from": (True, "int"),
    "decisions.layers": (False, "strs"),
    "decisions.options_warn_layers": (False, "strs"),
    "decisions.title_delimiters": (False, "str"),
    "decisions.close_title": (False, "str"),
    "decisions.close_decided": (False, "str"),
    "decisions.close_forced_decided": (False, "str"),
    "paths.deviation_register": (True, "str"),
    "paths.todo_file": (False, "str"),
    "paths.decision_export": (False, "str"),
    "paths.todo_export": (False, "str"),
    "runs.id_regex": (False, "regex"),
    "scan.skip_dirs": (False, "strs"),
    "scan.template_dirs": (False, "strs"),
    "scan.archive_dir_names": (False, "strs"),
    "scan.ignore_globs": (False, "strs"),
    "scan.conflict_globs": (False, "strs"),
    "legacy.decision_table": (False, "str"),
    "legacy.decision_columns": (False, "map"),
    "legacy.decision_status_rules": (False, "maps"),
    "legacy.type_map": (False, "map"),
    "legacy.unmigrated_types": (False, "strs"),
    "legacy.res_status_map": (False, "map"),
    "forbidden.markers": (False, "strs"),
    "forbidden.marker_level": (False, "str"),
    "forbidden.generated_notes": (False, "strs"),
    "templates.doc_markers": (False, "strs"),
    "templates.dir": (False, "str"),
    "templates.files": (False, "map"),
    "images.dir": (False, "str"),
    "labels.remark_prefix": (False, "str"),
    "labels.value_columns": (False, "ints"),
    "deviation.id_regex": (True, "regex"),
    "deviation.columns": (True, "map"),
    "deviation.found_values": (True, "strs"),
    "deviation.impact_cn": (False, "map"),
    "deviation.closed_marker": (False, "str"),
    "ids.plan_regex": (False, "regex"),
    "ids.exec_regex": (False, "regex"),
    "ids.res_prefix": (False, "str"),
    "frontmatter.extra_common_keys": (False, "strs"),
    "attachments.extra_exts": (False, "strs"),
    "prose.number_allow_regexes": (False, "strs"),
    "split": (False, "any"),
    "migration": (False, "any"),
    "display": (False, "any"),
    "names": (False, "any"),
    "folders": (False, "any"),
}

DEVIATION_COLUMN_FIELDS = (
    "id",
    "date",
    "experiment",
    "content",
    "impact",
    "disposition",
    "cards",
    "status",
    "found",
)
DEVIATION_REQUIRED_COLUMNS = DEVIATION_COLUMN_FIELDS[:7]
DECISION_COLUMN_FIELDS = (
    "number",
    "date",
    "decision",
    "reason",
    "lands",
    "status",
    "layer",
)
