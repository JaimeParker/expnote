"""Markdown template files and each project's choice of which files to use."""

from __future__ import annotations

import dataclasses
import json
import tomllib
from contextlib import suppress
from pathlib import Path

from expnote.research import newnote
from expnote.research.checks import rules_section
from expnote.research.edits import FileFormat, encode_note
from expnote.research.errors import ResearchError
from expnote.research.layout import atomic_bytes
from expnote.research.model import parse_note
from expnote.research.registry import ProjectContext, open_project
from expnote.research.textkit import markdown_tables, section_text
from expnote.research.writeback import (
    WriteContext,
    _create_no_clobber,
    _fsync_directory,
    _replace_with_retry,
    _write_temp,
    cas_mismatch,
    project_state_dir,
    sha256_bytes,
    write_lock,
)
from expnote.workspace import config_home

KINDS = {
    "plan": "plan",
    "exec": "exec-plan",
    "ev": "evidence",
    "appendix": "evidence-appendix",
    "res": "result",
}
SCOPES = ("project", "global")
GLOBAL_DIR = "research-templates"


def _path(root: Path, name: str) -> Path:
    relative = Path(name)
    path = root / relative
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ResearchError("TEMPLATE_PATH", f"Invalid template path {name!r}.")
    return path


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ResearchError(
            "TEMPLATE_READ_FAILED", f"Could not read {path.name}: {error}", exit_code=6
        ) from error


def _decode(raw: bytes, path: Path) -> str:
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as error:
        raise ResearchError(
            "TEMPLATE_INVALID", f"{path.name} must use UTF-8.", exit_code=6
        ) from error


@dataclasses.dataclass(frozen=True)
class TemplateFile:
    path: Path
    raw: bytes | None
    backups: Path

    @property
    def sha(self) -> str:
        return sha256_bytes(self.raw) if self.raw is not None else "missing"

    @property
    def version(self) -> str:
        # Bind the revision to the file, including when it has not been created yet.
        return sha256_bytes(f"{self.path}\n{self.sha}".encode())

    def previous(self, sha: str) -> Path:
        return _path(self.backups, f"{sha}.bak")

    def info(self, fallback: str = "") -> dict:
        return {
            "path": str(self.path),
            "text": _decode(self.raw, self.path) if self.raw is not None else fallback,
            "saved": self.raw is not None,
            "version": self.version,
            "canRestore": self.raw is not None and self.previous(self.sha).is_file(),
        }


class ProjectTemplates:
    def __init__(self, context: ProjectContext):
        self.context = context
        section = newnote._section(context.profile, "templates")
        material = context.resolved.material_dir
        self.root = _path(material, section.get("dir") or "templates")
        names = section.get("files") or {}
        self.paths = {
            key: _path(self.root, names.get(key) or f"{key}.md") for key in KINDS
        }
        for path in self.paths.values():
            if path.suffix.casefold() != ".md":
                raise ResearchError(
                    "TEMPLATE_PATH", f"{path.name} must be a Markdown file."
                )
        self.state = project_state_dir(context.entry.id)
        self.selection_path = _path(self.state, "template-sources.toml")
        raw = _read(self.selection_path)
        try:
            self.choices = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ResearchError(
                "TEMPLATE_SELECTION_INVALID",
                "Could not read template-sources.toml.",
                exit_code=6,
            ) from error
        if any(
            key not in KINDS or scope not in SCOPES
            for key, scope in self.choices.items()
        ):
            raise ResearchError(
                "TEMPLATE_SELECTION_INVALID",
                "Invalid selection in template-sources.toml.",
                exit_code=6,
            )

    def references(self) -> dict[str, Path]:
        configured = set(self.paths.values())
        return {
            path.relative_to(self.root).as_posix(): path
            for path in sorted(self.root.rglob("*"))
            if path.suffix.casefold() == ".md"
            and path not in configured
            and not path.is_symlink()
            and path.is_file()
            and path.resolve().is_relative_to(self.root.resolve())
        }

    def file(self, ident: str, scope: str) -> TemplateFile:
        if scope == "global" and ident in KINDS:
            root = _path(config_home(), GLOBAL_DIR)
            path = _path(root, f"{ident}.md")
            backup_root = _path(root, ".previous")
        elif scope == "project":
            path = self.paths.get(ident) or self.references().get(ident)
            if path is None:
                raise ResearchError(
                    "TEMPLATE_NOT_FOUND", "Template file not found.", exit_code=3
                )
            backup_root = _path(self.state, "template-backups")
        else:
            raise ResearchError(
                "TEMPLATE_SCOPE", "Select a project or global template."
            )
        backups = _path(backup_root, sha256_bytes(str(path).encode()))
        return TemplateFile(path, _read(path), backups)

    def source(self, kind: str, project: TemplateFile) -> str:
        return self.choices.get(kind) or (
            "project" if project.raw is not None else "global"
        )

    def selection_version(self, kind: str, project: TemplateFile) -> str:
        source = self.source(kind, project)
        return sha256_bytes(f"{project.path}\n{kind}\n{source}".encode())

    def select(self, kind: str, scope: str) -> None:
        self.choices[kind] = scope
        text = "".join(
            f"{k} = {json.dumps(v)}\n" for k, v in sorted(self.choices.items())
        )
        atomic_bytes(self.selection_path, text.encode())

    def catalog(self) -> dict:
        templates = []
        for kind in KINDS:
            project = self.file(kind, "project")
            shared = self.file(kind, "global").info(newnote.BUILTIN_TEMPLATES[kind])
            source = self.source(kind, project)
            templates.append(
                {
                    "id": kind,
                    "source": source,
                    "effectiveSource": "global" if project.raw is None else source,
                    "variables": list(_example_subs(self.context, kind)),
                    "selectionVersion": self.selection_version(kind, project),
                    "files": {
                        "project": project.info(shared["text"]),
                        "global": shared,
                    },
                }
            )
        return {
            "projectName": self.context.entry.name,
            "templates": templates,
            "references": [
                {"id": ident, "files": {"project": self.file(ident, "project").info()}}
                for ident in self.references()
            ],
        }


def active_text(context: ProjectContext, kind: str) -> str:
    store = ProjectTemplates(context)
    if store.choices.get(kind) != "global":
        project = store.file(kind, "project")
        if project.raw is not None:
            return _decode(project.raw, project.path)
    shared = store.file(kind, "global")
    return (
        newnote.BUILTIN_TEMPLATES[kind]
        if shared.raw is None
        else _decode(shared.raw, shared.path)
    )


def catalog(project: str) -> dict:
    return ProjectTemplates(open_project(project)).catalog()


def _invalid(message: str) -> ResearchError:
    return ResearchError("TEMPLATE_INVALID", message)


def _example_subs(context: ProjectContext, kind: str) -> dict[str, str]:
    plan_id = "EXAMPLE-9100A"
    plan = f"{plan_id}--Example"
    title = 'Example "A"'
    stage = "S2"
    ev = f"EV--{plan_id} Example"
    appendix = f"appendix--{ev}"
    extra = {"title": title, "plan": plan, "plan_id": plan_id}
    if kind == "plan":
        extra.update(id=plan_id, plan="")
    elif kind == "exec":
        extra.update(id=f"{plan_id}-{stage}", stage=stage)
    elif kind == "res":
        extra.update(id=f"RES-{plan_id}")
    else:
        extra.update(ev=ev, appendix=appendix)
    return newnote.template_subs(context, "2026-01-02", **extra)


def _sample(context: ProjectContext, kind: str, text: str, scope: str) -> str:
    profile = context.profile
    subs = _example_subs(context, kind)
    try:
        rendered, left = newnote.render_template_text(profile, kind, text, subs)
    except ResearchError as error:
        raise _invalid(error.message) from error
    if left:
        variables = ", ".join("{{" + name + "}}" for name in left)
        raise _invalid(f"Unknown template variables: {variables}.")
    note = parse_note(Path("example.md"), "example.md", rendered)
    if note.fm_error:
        raise _invalid(note.fm_error)
    if note.type != KINDS[kind]:
        raise _invalid(f"The YAML type must be {KINDS[kind]}.")
    plan_link = f"[[{subs['plan']}]]"
    required = {
        "plan": {"id": subs.get("id"), "title": subs["title"], "status": "draft"},
        "exec": {
            "id": subs.get("id"),
            "plan": plan_link,
            "stage": subs.get("stage"),
            "status": "draft",
        },
        "ev": {
            "plan": plan_link,
            "appendix": f"[[{subs.get('appendix')}]]",
            "status": "rendered",
        },
        "appendix": {"plan": plan_link, "parent": f"[[{subs.get('ev')}]]"},
        "res": {"id": subs.get("id"), "plan": plan_link, "status": "draft"},
    }[kind]
    for key, value in required.items():
        if note.get(key) != value:
            raise _invalid(f"The example YAML field {key} must render as {value!r}.")
    if kind == "exec" and rules_section(note.body, profile) is None:
        raise _invalid(f"Include the rules section headed {profile.heading_rules!r}.")
    if kind == "ev":
        section = section_text(
            note.body,
            lambda heading: any(key in heading for key in profile.heading_labels),
        )
        if section is None or not markdown_tables(section):
            raise _invalid(
                f"Include a label table under {profile.heading_labels[0]!r}."
            )
    if scope == "global" and kind in {"exec", "ev"}:
        variable = "{{rules_heading}}" if kind == "exec" else "{{label_heading}}"
        # Check the localized section outside code fences.
        rendered, _ = newnote.render_template_text(
            profile,
            kind,
            text,
            subs
            | {
                variable[2:-2]: variable,
            },
        )
        if section_text(rendered, lambda heading: variable in heading) is None:
            raise _invalid(
                f"Use {variable} in the heading for per-project localization."
            )
    return note.text


def _validate(
    context: ProjectContext, file: TemplateFile, ident: str, scope: str, text: str
) -> str:
    if ident in KINDS:
        return _sample(context, ident, text, scope)
    note = parse_note(file.path, ident, text)
    if note.fm_error:
        raise _invalid(note.fm_error)
    if file.raw is not None:
        old = parse_note(file.path, ident, _decode(file.raw, file.path))
        for key in ("id", "record_id"):
            if old.get(key) and note.get(key) != old.get(key):
                raise _invalid(
                    f"Keep the existing {key} so links to this reference still work."
                )
    return text


def _expect(path: Path, actual: str, expected: str) -> None:
    if actual != expected:
        raise cas_mismatch(path, expected, actual)


def _save_file(file: TemplateFile, raw: bytes, fallback: bytes | None) -> None:
    if raw == file.raw:
        return
    previous = file.raw if file.raw is not None else fallback
    backing = file.previous(sha256_bytes(raw))
    # Associate the backup with the committed bytes. A failed replace keeps the
    # old file and its corresponding backup usable, even after an interruption.
    if previous is not None and previous != raw:
        atomic_bytes(backing, previous)
    if file.raw is None:
        _create_no_clobber(file.path, raw)
    else:
        mode = file.path.stat().st_mode & 0o777
        temp = _write_temp(file.path, raw, mode)
        try:
            current = _read(file.path)
            actual = sha256_bytes(current) if current is not None else "missing"
            _expect(file.path, actual, file.sha)
            _replace_with_retry(temp, file.path)
            _fsync_directory(file.path.parent)
        finally:
            temp.unlink(missing_ok=True)
    if file.backups.is_dir():
        for path in file.backups.glob("*.bak"):
            if path != backing:
                with suppress(OSError):
                    path.unlink()


def write(
    project: str, action: str, ident: str, scope: str, expected: str, text: str | None
) -> dict:
    context = open_project(project)
    lock = (
        WriteContext("templates", "templates-global", config_home())
        if scope == "global" and action != "select"
        else WriteContext.for_project(context)
    )
    try:
        with write_lock(lock):
            store = ProjectTemplates(open_project(project))
            file = store.file(ident, scope)
            if action == "select":
                if ident not in KINDS:
                    raise _invalid("Select a source for a document template.")
                local = store.file(ident, "project")
                _expect(
                    store.selection_path,
                    store.selection_version(ident, local),
                    expected,
                )
                if scope == "project" and file.raw is None:
                    raise _invalid("Save a project template before selecting it.")
                if file.raw is not None:
                    _validate(
                        store.context, file, ident, scope, _decode(file.raw, file.path)
                    )
                store.select(ident, scope)
            elif action in {"save", "restore"}:
                _expect(file.path, file.version, expected)
                if action == "restore":
                    previous = _read(file.previous(file.sha))
                    if previous is None:
                        raise ResearchError(
                            "TEMPLATE_NO_BACKUP",
                            "There is no previous saved version.",
                            exit_code=3,
                        )
                    # Legacy versions may predate validation.
                    replacement = previous
                else:
                    assert text is not None
                    _validate(store.context, file, ident, scope, text)
                    raw = file.raw or b""
                    fmt = FileFormat(raw.startswith(b"\xef\xbb\xbf"), b"\r\n" in raw)
                    replacement = encode_note(text.replace("\r\n", "\n"), fmt)
                if (
                    scope == "project"
                    and ident in KINDS
                    and file.raw is None
                    and ident not in store.choices
                ):
                    # Save the draft independently of its use for new documents.
                    store.select(ident, "global")
                fallback = (
                    newnote.BUILTIN_TEMPLATES[ident].encode()
                    if scope == "global"
                    else None
                )
                _save_file(file, replacement, fallback)
            else:
                raise ResearchError(
                    "TEMPLATE_ACTION", "Choose save, restore or select."
                )
            return store.catalog()
    except OSError as error:
        raise ResearchError(
            "TEMPLATE_SAVE_FAILED",
            f"Could not save template settings: {error}",
            exit_code=6,
        ) from error
    except UnicodeEncodeError as error:
        raise _invalid("Use valid UTF-8 text in the template.") from error


def preview(view, ident: str, scope: str, text: str) -> str:
    file = ProjectTemplates(view.context).file(ident, scope)
    return _validate(view.context, file, ident, scope, text)
