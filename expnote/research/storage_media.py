"""Resolve local document references and plan image copies for either location."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from expnote.research.edits import _code_and_comment_mask, edit_frontmatter
from expnote.research.errors import ResearchError
from expnote.research.index import IMAGE_EXTS
from expnote.research.model import parse_note
from expnote.research.storage_paths import vault_assets_dir
from expnote.research.textkit import INLINE_CODE_RE, fold

WIKI = re.compile(r"(?P<embed>!)?\[\[(?P<target>[^\[\]\n]+)\]\]")
MARKDOWN = re.compile(
    r"(?P<embed>!)?\[(?P<label>(?:\\.|[^\[\]\n\\])*+)\]"
    r"\((?P<target><[^>\n]+>|[^)\s]+)(?P<title>\s+[\"'][^\n]*?[\"'])?\)"
)
LINK = re.compile(f"(?:{WIKI.pattern})|(?:{MARKDOWN.pattern.replace('?P<', '?P<md_')})")


def sha_file(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def is_image(name: str) -> bool:
    return PurePosixPath(name).suffix.lower().lstrip(".") in IMAGE_EXTS


def wiki_parts(raw: str) -> tuple[str, str, str]:
    parts = re.split(r"(\\?\|)", raw, maxsplit=1)
    return (
        parts[0],
        parts[1] if len(parts) > 1 else "",
        parts[2] if len(parts) > 2 else "",
    )


def local_target(raw: str) -> str | None:
    raw = raw.strip().removeprefix("<").removesuffix(">")
    try:
        url = urlsplit(raw)
    except ValueError:
        return None
    return unquote(url.path) if not (url.scheme or url.netloc) else None


def rewrite_links(text, rewrite, *, frontmatter=False):
    lines = text.split("\n")
    excluded = _code_and_comment_mask(lines)
    in_properties = bool(lines and lines[0] == "---")
    for i, line in enumerate(lines):
        if in_properties and not frontmatter:
            if i and line in {"---", "..."}:
                in_properties = False
            continue
        if excluded[i]:
            continue
        code = [m.span() for m in INLINE_CODE_RE.finditer(line)]
        protected = [m.span() for m in re.finditer(r"%%.*?%%|<!--.*?-->", line)]
        spans = code + protected
        lines[i] = LINK.sub(
            lambda match, spans=spans: (
                match[0]
                if any(start <= match.start() < end for start, end in spans)
                else rewrite(match)
            ),
            line,
        )
    return "\n".join(lines)


class ImageCopies:
    def __init__(self, resolved, files):
        self.resolved = resolved
        self.files = defaultdict(list)
        for path in files:
            if is_image(path.name):
                self.files[fold(path.name)].append(path)
        self.copies: dict[Path, tuple[Path, str]] = {}
        self.reads: dict[Path, str] = {}
        self.missing: set[str] = set()
        self.references: dict[Path, dict[Path, Path]] = defaultdict(dict)

    def _hash(self, path: Path) -> str:
        if path not in self.reads:
            sha = sha_file(path)
            if not sha:
                raise ResearchError("IMAGE_MISSING", str(path), exit_code=5)
            self.reads[path] = sha
        return self.reads[path]

    def find(self, name: str, source: Path) -> Path | None:
        resolved = self.resolved
        candidates = self.files.get(fold(PurePosixPath(name).name), [])
        root = (
            resolved.material_dir
            if source.is_relative_to(resolved.material_dir)
            else resolved.vault_root
        )
        if "/" in name:
            possible = {(source.parent / name).resolve(), (root / name).resolve()}
            exact = [
                path
                for path in candidates
                if path in possible or fold(resolved.logical_path(path)) == fold(name)
            ]
            suffix = [
                path
                for path in candidates
                if fold(resolved.logical_path(path)).endswith("/" + fold(name))
            ]
            candidates = exact or suffix
        preferred = [path for path in candidates if path.is_relative_to(root)]
        matches = preferred or candidates
        if not matches:
            self.missing.add(f"{source.name}: {name}")
            return None
        paths = sorted(
            matches,
            key=lambda path: (
                path.parent != source.parent,
                path.parent != resolved.images_dir,
                str(path),
            ),
        )
        if len(paths) > 1 and len({self._hash(path) for path in paths}) > 1:
            raise ResearchError("IMAGE_AMBIGUOUS", f"{source}: {name}", exit_code=5)
        return paths[0]

    def destination(self, source: Path, location: str) -> Path:
        resolved = self.resolved
        sha = self._hash(source)
        root = resolved.vault_root if location == "vault" else resolved.material_dir
        if source.is_relative_to(root):
            return source
        directory = (
            vault_assets_dir(resolved) if location == "vault" else resolved.images_dir
        )
        if not directory.resolve().is_relative_to(root.resolve()):
            raise ResearchError("IMAGE_PATH", str(directory), exit_code=5)
        choices = self.files.get(fold(source.name), [])
        for path in sorted(choices):
            allowed = resolved.project_dir if location == "vault" else directory
            if path.is_relative_to(allowed) and self._hash(path) == sha:
                return path
        target = directory / source.name
        existing = [
            path for path in choices if fold(path.as_posix()) == fold(target.as_posix())
        ]
        if existing or (target in self.copies and self.copies[target][1] != sha):
            target = target.with_stem(f"{source.stem}-{sha[:16]}")
        existing_sha = (
            self.copies[target][1] if target in self.copies else sha_file(target)
        )
        if target.is_symlink() or (existing_sha is not None and existing_sha != sha):
            raise ResearchError("IMAGE_CONFLICT", str(target), exit_code=5)
        if existing_sha is None:
            self.copies[target] = (source, sha)
            self.files[fold(target.name)].append(target)
            self.reads[target] = sha
        return target

    def rewrite(self, text: str, source: Path, location: str, document=None) -> str:
        def relocate(name):
            image = self.find(name, source)
            if image is None:
                return None
            target = self.destination(image, location)
            self.references[source][image] = target
            return target

        def embed(match):
            if match["embed"]:
                name, sep, label = wiki_parts(match["target"])
                bare, _, anchor = name.partition("#")
                if is_image(bare) and (where := relocate(bare)):
                    suffix = f"#{anchor}" if anchor else ""
                    link = self.resolved.logical_path(where)
                    return f"![[{link}{suffix}{sep}{label}]]"
            if match["md_embed"]:
                raw = match["md_target"]
                name = local_target(raw)
                if name and is_image(name) and (where := relocate(name)):
                    dest = quote(
                        os.path.relpath(where, (document or source).parent), safe="/._-"
                    )
                    return f"![{match['md_label']}]({dest}{match['md_title'] or ''})"
            return match[0]

        note = parse_note(source, source.name, text)
        figures = note.get("figures")
        output = rewrite_links(text, embed)
        if isinstance(figures, list):
            replaced = [
                self.resolved.logical_path(image)
                if isinstance(item, str)
                and is_image(item)
                and (image := relocate(item))
                else item
                for item in figures
            ]
            if replaced != figures:
                output = edit_frontmatter(
                    output, [("figures", json.dumps(replaced, ensure_ascii=False))]
                )
        return output

    def referenced(self, source: Path) -> dict[Path, str]:
        return {
            path: self.reads[path]
            for path in self.references.get(source, {})
            if path not in self.copies
        }
