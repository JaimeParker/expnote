"""Project profile: every project-specific constant, read from the profile note."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from expnote.research.errors import ResearchError
from expnote.research.frontmatter import (
    FrontmatterError,
    load_frontmatter,
    split_frontmatter,
)
from expnote.research.schemas import (
    DECISION_COLUMN_FIELDS,
    DEFAULT_ATTACHMENT_EXTS,
    DEFAULT_COMMON_KEYS,
    DEFAULT_SKIP_DIRS,
    DEVIATION_COLUMN_FIELDS,
    DEVIATION_REQUIRED_COLUMNS,
    PROFILE_KEYS,
)
from expnote.research.textkit import normalize_text, sha256_text

PROFILE_TYPE = "project-profile"


@dataclasses.dataclass(frozen=True)
class StatusRule:
    pattern: str
    status: str
    review: str | None = None


@dataclasses.dataclass(frozen=True)
class Profile:
    project_id: str
    schema: str
    images_required_prefix: str
    heading_rules: str
    heading_labels: tuple[str, ...]
    heading_observation: str
    heading_decision_required: tuple[str, ...]
    heading_decision_options: str
    heading_decision_consequences: str
    decision_id_prefix: str
    decision_strict_from: int
    decision_layers: tuple[str, ...]
    options_warn_layers: tuple[str, ...]
    deviation_register: str
    decision_export: str
    todo_export: str
    todo_file: str
    run_id_regex: str
    skip_dirs: frozenset[str]
    template_dirs: frozenset[str]
    archive_dir_names: frozenset[str]
    ignore_globs: tuple[str, ...]
    conflict_globs: tuple[str, ...]
    legacy_decision_table: str | None
    legacy_decision_columns: dict[str, str]
    legacy_status_rules: tuple[StatusRule, ...]
    legacy_type_map: dict[str, str]
    legacy_unmigrated_types: tuple[str, ...]
    legacy_res_status_map: dict[str, str]
    forbidden_markers: tuple[str, ...]
    forbidden_marker_level: str
    label_value_columns: tuple[int, ...]
    deviation_id_regex: str
    deviation_columns: dict[str, str]
    deviation_found_values: tuple[str, ...]
    deviation_closed_marker: str
    common_keys: frozenset[str]
    attachment_exts: frozenset[str]
    number_allow_regexes: tuple[str, ...]
    unknown_keys: tuple[str, ...] = ()
    raw: dict = dataclasses.field(default_factory=dict, compare=False, hash=False)
    plan_id_regex: str = ""
    generated_notes: tuple[str, ...] = ()

    def decision_ref_pattern(self) -> re.Pattern:
        return re.compile(rf"(?:#|{re.escape(self.decision_id_prefix)}-)?(\d+)")


@dataclasses.dataclass
class ProfileNote:
    """Result of reading the profile note: either a Profile or a list of problems."""

    found: bool
    sha256: str | None = None
    profile: Profile | None = None
    problems: list[str] = dataclasses.field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return self.profile is not None


def _dig(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _unknown_keys(data: dict) -> tuple[str, ...]:
    declared = set(PROFILE_KEYS) | {"type", "schema", "project_id"}
    unknown = []
    for key, value in data.items():
        if key in declared:
            continue
        if isinstance(value, dict):
            unknown += [
                f"{key}.{sub}" for sub in value if f"{key}.{sub}" not in declared
            ]
        else:
            unknown.append(key)
    return tuple(sorted(unknown))


def _coerce(path: str, kind: str, value, problems: list[str]):
    if kind == "str":
        if isinstance(value, str):
            return value
    elif kind == "int":
        if isinstance(value, str) and re.fullmatch(r"\d+", value):
            return int(value)
    elif kind == "regex":
        if isinstance(value, str):
            try:
                re.compile(value)
                return value
            except re.error as error:
                problems.append(f"{path}: invalid regex ({error})")
                return None
    elif kind == "strs":
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return tuple(value)
    elif kind == "ints":
        if isinstance(value, list) and all(
            isinstance(v, str) and re.fullmatch(r"\d+", v) for v in value
        ):
            return tuple(int(v) for v in value)
    elif kind == "any":
        if isinstance(value, dict):
            return dict(value)
    elif kind == "map":
        if isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
            return dict(value)
    elif kind == "maps":
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return list(value)
    problems.append(f"{path}: expected {kind}, got {value!r}")
    return None


def parse_profile_text(text: str) -> ProfileNote:
    text = normalize_text(text)
    note = ProfileNote(found=True, sha256=sha256_text(text))
    try:
        fm_text, _ = split_frontmatter(text)
        if fm_text is None:
            note.problems.append("profile note has no frontmatter")
            return note
        data, _ = load_frontmatter(fm_text)
    except FrontmatterError as error:
        note.problems.append(f"frontmatter: {error}")
        return note
    if data.get("type") != PROFILE_TYPE:
        note.problems.append(f"type must be {PROFILE_TYPE!r}, got {data.get('type')!r}")
    for key in ("project_id", "schema"):
        if not isinstance(data.get(key), str) or not data.get(key):
            note.problems.append(f"missing required key {key}")
    values: dict[str, object] = {}
    for path, (required, kind) in PROFILE_KEYS.items():
        raw = _dig(data, path)
        if raw is None or raw == "" or raw == []:
            if required:
                note.problems.append(f"missing required key {path}")
            continue
        coerced = _coerce(path, kind, raw, note.problems)
        if coerced is not None:
            values[path] = coerced
    columns = values.get("deviation.columns", {})
    missing = [f for f in DEVIATION_REQUIRED_COLUMNS if f not in columns]
    if "deviation.columns" in values and missing:
        note.problems.append(f"deviation.columns lacks: {', '.join(missing)}")
    unknown_fields = [f for f in columns if f not in DEVIATION_COLUMN_FIELDS]
    if unknown_fields:
        note.problems.append(f"deviation.columns unknown fields: {unknown_fields}")
    dec_cols = values.get("legacy.decision_columns", {})
    if dec_cols and set(dec_cols) != set(DECISION_COLUMN_FIELDS):
        note.problems.append(
            "legacy.decision_columns must name exactly: "
            f"{', '.join(DECISION_COLUMN_FIELDS)}"
        )
    if values.get("legacy.decision_table") and not dec_cols:
        note.problems.append(
            "legacy.decision_table set but legacy.decision_columns missing"
        )
    if note.problems:
        return note
    rules = tuple(
        StatusRule(r.get("pattern", ""), r.get("status", ""), r.get("review") or None)
        for r in values.get("legacy.decision_status_rules", [])
    )
    for rule in rules:
        try:
            re.compile(rule.pattern)
        except re.error as error:
            note.problems.append(f"legacy.decision_status_rules: bad regex ({error})")
        if not rule.status:
            note.problems.append("legacy.decision_status_rules: rule without status")
    if note.problems:
        return note

    def get(path, default=None):
        return values.get(path, default)

    layers = get("decisions.layers", ())
    note.profile = Profile(
        project_id=data["project_id"],
        schema=data["schema"],
        images_required_prefix=get("images.required_prefix", ""),
        heading_rules=get("headings.rules"),
        heading_labels=get("headings.labels"),
        heading_observation=get("headings.observation"),
        heading_decision_required=get("headings.decision_required", ()),
        heading_decision_options=get("headings.decision_options", ""),
        heading_decision_consequences=get("headings.decision_consequences", ""),
        decision_id_prefix=get("decisions.id_prefix"),
        decision_strict_from=get("decisions.strict_from"),
        decision_layers=layers,
        options_warn_layers=get("decisions.options_warn_layers", layers[:1]),
        deviation_register=get("paths.deviation_register"),
        decision_export=get("paths.decision_export", "ops/decisions.jsonl"),
        todo_export=get("paths.todo_export", "ops/todos.jsonl"),
        todo_file=get("paths.todo_file", ""),
        run_id_regex=get("runs.id_regex", ""),
        skip_dirs=DEFAULT_SKIP_DIRS | frozenset(get("scan.skip_dirs", ())),
        template_dirs=frozenset(get("scan.template_dirs", ())),
        archive_dir_names=frozenset(get("scan.archive_dir_names", ())),
        ignore_globs=get("scan.ignore_globs", ()),
        conflict_globs=get("scan.conflict_globs", ()),
        legacy_decision_table=get("legacy.decision_table"),
        legacy_decision_columns=get("legacy.decision_columns", {}),
        legacy_status_rules=rules,
        legacy_type_map=get("legacy.type_map", {}),
        legacy_unmigrated_types=get("legacy.unmigrated_types", ()),
        legacy_res_status_map=get("legacy.res_status_map", {}),
        forbidden_markers=get("forbidden.markers", ()),
        forbidden_marker_level=get("forbidden.marker_level", "warn").lower(),
        label_value_columns=get("labels.value_columns", (1,)),
        deviation_id_regex=get("deviation.id_regex"),
        deviation_columns=get("deviation.columns"),
        deviation_found_values=get("deviation.found_values"),
        deviation_closed_marker=get("deviation.closed_marker", ""),
        common_keys=DEFAULT_COMMON_KEYS
        | frozenset(get("frontmatter.extra_common_keys", ())),
        attachment_exts=DEFAULT_ATTACHMENT_EXTS
        | frozenset(e.lower() for e in get("attachments.extra_exts", ())),
        number_allow_regexes=get("prose.number_allow_regexes", ()),
        unknown_keys=_unknown_keys(data),
        raw=data,
        plan_id_regex=get("ids.plan_regex", ""),
        generated_notes=get("forbidden.generated_notes", ()),
    )
    return note


def read_profile_note(path: Path) -> ProfileNote:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ProfileNote(found=False, problems=[f"profile note not found: {path}"])
    except OSError as error:
        raise ResearchError(
            "VAULT_UNAVAILABLE", f"cannot read profile note: {error}", exit_code=6
        ) from None
    return parse_profile_text(text)
