"""Texts the migration writes or proposes: schema 3 templates, hub stub, proposals.

The wording lives in `migrate_data/*.md` (data, reviewed by the coordinator) and in the
profile's `migration` section; nothing project-specific is spelled in this module.
Templates are the old Off2On ones without status markers and with `expnote` commands
instead of `vault.py`; placeholders are the ones `new plan|exec|ev|res` fills.
"""

from __future__ import annotations

import re

from expnote.research.migrate_profile import read_data
from expnote.research.profile import Profile

TEMPLATE_KINDS = ("plan", "exec", "ev", "appendix", "res")
EXTRA_TEMPLATES = ("todo",)


def migration_section(profile: Profile) -> dict:
    section = profile.raw.get("migration")
    return section if isinstance(section, dict) else {}


def template_names(profile: Profile) -> dict[str, str]:
    """Kind -> file name of the schema 3 templates (from the profile)."""
    templates = profile.raw.get("templates")
    files = templates.get("files") if isinstance(templates, dict) else None
    names = dict(files) if isinstance(files, dict) else {}
    extra = migration_section(profile).get("template_names")
    if isinstance(extra, dict):
        names.update(extra)
    return {k: v for k, v in names.items() if k in (*TEMPLATE_KINDS, *EXTRA_TEMPLATES)}


def schema3_template_files(profile: Profile) -> dict[str, str]:
    return {
        name: read_data(f"template-{kind}.md")
        for kind, name in template_names(profile).items()
    }


def hub_stub_text(decision_prefix: str, hub_stem: str) -> str:
    return (
        read_data("hub-stub.md")
        .replace("{prefix}", decision_prefix)
        .replace("{hub_stem}", hub_stem)
    )


# ---- proposals (judgement: written to the migration directory, never applied) -------


def _retired(
    profile: Profile,
) -> tuple[re.Pattern | None, list[tuple[re.Pattern, str]]]:
    section = migration_section(profile)
    words = section.get("retired_keywords") or []
    keywords = re.compile("|".join(re.escape(w) for w in words)) if words else None
    pairs = section.get("retired_replacements")
    replacements = (
        [(re.compile(k), v) for k, v in pairs.items()]
        if isinstance(pairs, dict)
        else []
    )
    return keywords, replacements


def _suggest(line: str, replacements) -> str:
    for pattern, replacement in replacements:
        line = pattern.sub(replacement, line)
    return line


def propose_layering_text(profile: Profile, current: str) -> str:
    """Proposed layering-convention note: old section replaced, retired words noted."""
    keywords, replacements = _retired(profile)
    marker = re.search(r"^## schema 2 .*$", current, re.M)
    head = current[: marker.start()] if marker else current.rstrip("\n") + "\n\n"
    out = []
    for line in head.split("\n"):
        if keywords is not None and keywords.search(line):
            fixed = _suggest(line, replacements)
            note = (
                ""
                if fixed == line
                else f"  <!-- proposed: {fixed.strip()[:120]} -->"
            )
            out.append(f"{line}{note}  <!-- coordinator to review -->")
        else:
            out.append(line)
    return "\n".join(out) + read_data("layering-schema3.md")


def propose_moc_wording(profile: Profile, moc_text: str) -> str:
    """MOC lines that mention retired mechanisms, with a mechanical suggestion."""
    keywords, replacements = _retired(profile)
    out = [
        "# MOC wording proposal (not applied; coordinator reviews, user approves)",
        "",
        "These lines mention retired boards, views, `vault.py` or status tails. The "
        "mechanical suggestion is a text replacement only; nothing was reviewed.",
        "",
    ]
    for number, line in enumerate(moc_text.split("\n"), start=1):
        if keywords is None or not keywords.search(line):
            continue
        fixed = _suggest(line, replacements)
        out += [f"## Line {number}", "", "Current:", "", f"> {line}", ""]
        if fixed != line:
            out += ["Mechanical suggestion:", "", f"> {fixed}", ""]
        else:
            out += ["Mechanical suggestion: none, needs a human rewrite.", ""]
    return "\n".join(out)
