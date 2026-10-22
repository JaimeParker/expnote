"""Server-side Markdown for the research GUI.

Vault notes are rendered from the index, never from disk. Raw HTML is never
parsed (the `markdown` package would pass it through): the HTML preprocessor and
inline pattern are removed, so tags in a note are serialised as escaped text.
Obsidian syntax is added on top: wikilinks, embeds, run/doc links and the legacy
decision hub form (`[[hub]] #12, #13`).
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as etree
from urllib.parse import quote

import markdown
from markdown.extensions import Extension
from markdown.extensions.toc import slugify_unicode
from markdown.inlinepatterns import InlineProcessor
from markdown.preprocessors import Preprocessor
from markdown.treeprocessors import Treeprocessor

from expnote.research.extract import decision_hub_pattern, decision_hub_stem
from expnote.research.frontmatter import split_frontmatter
from expnote.research.index import IMAGE_EXTS
from expnote.research.textkit import (
    FENCE_RE,
    HTML_COMMENT_RE,
    fold,
    link_stem,
    link_target,
    nfc,
    normalize_text,
)
from expnote.research.view import ProjectView

SAFE_SCHEMES = ("http://", "https://", "mailto:")
EMBED_WIDTH = re.compile(r"\d{1,4}")
TASK_LINE = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+)\[( |x|X)\](?=\s)", re.M)


def api_base(project_id: str) -> str:
    return f"/api/research/projects/{quote(project_id, safe='')}"


def image_url(project_id: str, name: str) -> str:
    return f"{api_base(project_id)}/image/{quote(name, safe='')}"


def page_url(project_id: str, *parts: str) -> str:
    tail = "".join("/" + quote(p, safe="") for p in parts)
    return f"#/p/{quote(project_id, safe='')}{tail}"


# ---- link resolution (index backed) ----------------------------------------------


def find_attachment(view: ProjectView, name: str) -> tuple[str, str | None] | None:
    """(vault-relative path, sha256) of the attachment called `name`, or None.

    Matches the file name or a path suffix (NFC, case-folded); a hit inside the
    images directory wins, then the shortest path. Absolute and `..` names never match.
    """
    raw = nfc(name.strip().replace("\\", "/"))
    wanted = fold(raw)
    if not wanted or raw.startswith("/") or ".." in raw.split("/"):
        return None
    rows = view.conn.execute(
        "SELECT rel, nrel, sha256 FROM files WHERE kind = 'attachment' "
        "AND is_conflict = 0 AND nrel LIKE ? ESCAPE '\\'",
        ("%" + _like_escape(raw.rsplit("/", 1)[-1]),),
    ).fetchall()
    hits = [
        r
        for r in rows
        if fold(r["nrel"]) == wanted or fold(r["nrel"]).endswith("/" + wanted)
    ]
    if not hits:
        return None
    images = view.snapshot.images_rel + "/"
    hits.sort(key=lambda r: (not r["rel"].startswith(images), len(r["rel"]), r["rel"]))
    return hits[0]["rel"], hits[0]["sha256"]


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclasses.dataclass
class LinkTarget:
    href: str | None
    kind: str  # plan | decision | evidence | note | decisions | run | doc | missing


class LinkResolver:
    """Wikilink target -> GUI route, resolved through the index (cached)."""

    def __init__(self, view: ProjectView, run_app: bool):
        self.view = view
        self.run_app = run_app
        self._notes: dict[str, LinkTarget] = {}
        self.hub = decision_hub_stem(view.profile)
        pattern = view.profile.run_id_regex
        self._run_shape = re.compile(pattern) if pattern else None

    def row(self, name: str):
        """The note called `name` (stem or alias; project first), or None."""
        key = fold(link_stem(link_target(name)))
        conn, prefix = self.view.conn, self.view.prefix + "/"
        columns = (
            "SELECT f.id AS file_id, f.rel, f.sha256, n.stem, n.type, n.entity_id, "
            "n.status, n.title FROM notes n JOIN files f ON f.id = n.file_id "
        )
        rows = conn.execute(
            columns + "WHERE n.cstem = ? AND f.is_conflict = 0", (key,)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                columns + "JOIN aliases a ON a.file_id = n.file_id "
                "WHERE a.alias_cstem = ? AND f.is_conflict = 0",
                (key,),
            ).fetchall()
        rows = sorted(rows, key=lambda r: (not r["rel"].startswith(prefix), r["rel"]))
        return rows[0] if rows else None

    def note(self, target: str) -> LinkTarget:
        stem = link_stem(link_target(target))
        key = fold(stem)
        if key in self._notes:
            return self._notes[key]
        resolved = self._resolve_note(stem, key)
        self._notes[key] = resolved
        return resolved

    def _resolve_note(self, stem: str, key: str) -> LinkTarget:
        view, pid = self.view, self.view.project_id
        row = self.row(stem)
        if row is not None:
            kind = view.profile.legacy_type_map.get(row["type"] or "", row["type"])
            if self.hub and key == fold(self.hub):
                return LinkTarget(page_url(pid, "decisions"), "decisions")
            if kind == "plan" and row["entity_id"] and self._listed_plan(row):
                return LinkTarget(page_url(pid, "plan", row["entity_id"]), "plan")
            if kind == "decision":
                ident = row["entity_id"] or row["stem"]
                return LinkTarget(page_url(pid, "decision", ident), "decision")
            if kind == "evidence":
                return LinkTarget(page_url(pid, "ev", row["stem"]), "evidence")
            return LinkTarget(page_url(pid, "note", row["stem"]), "note")
        decision = self._stored_decision(stem)
        if decision is not None:
            return LinkTarget(page_url(pid, "decision", decision), "decision")
        return self._run_or_missing(stem)

    def _stored_decision(self, stem: str) -> str | None:
        """Id of the store decision a `[[PREFIX-NNNN]]` link names, if there is one."""
        snap = self.view.snapshot
        if not snap.is_stored_decision(stem):
            return None
        return stem.strip().upper()

    def _listed_plan(self, row) -> bool:
        archived = set(row["rel"].split("/")[:-1]) & self.view.profile.archive_dir_names
        regex = self.view.profile.plan_id_regex
        shaped = not regex or re.fullmatch(regex, row["entity_id"]) is not None
        return shaped and not archived

    def _run_or_missing(self, stem: str) -> LinkTarget:
        if self._run_shape is None or not self._run_shape.fullmatch(stem):
            return LinkTarget(None, "missing")
        found = self.view.conn.execute(
            "SELECT resolution FROM links WHERE target_stem = ? "
            "AND resolution IN ('run', 'doc') LIMIT 1",
            (stem,),
        ).fetchone()
        if found is None:
            return LinkTarget(None, "missing")
        kind = found["resolution"]
        if not self.run_app:
            return LinkTarget(None, kind)
        return LinkTarget(f"/#/{kind}/{quote(stem, safe='')}", kind)


# ---- the Markdown extension ---------------------------------------------------------


def _chip(text: str, kind: str, title: str | None = None) -> etree.Element:
    el = etree.Element("span")
    el.set("class", f"wl {kind}")
    if title:
        el.set("title", title)
    el.text = text
    return el


def _link(text: str, target: LinkTarget, title: str | None = None) -> etree.Element:
    if target.href is None:
        missing = target.kind == "missing"
        return _chip(
            text,
            "missing" if missing else f"{target.kind}-plain",
            title or ("unresolved link" if missing else None),
        )
    el = etree.Element("a")
    el.set("href", target.href)
    el.set("class", f"wl {target.kind}")
    if title:
        el.set("title", title)
    el.text = text
    return el


def _split_alias(inner: str) -> tuple[str, str | None]:
    parts = re.split(r"\\?\|", inner, maxsplit=1)
    alias = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return parts[0].strip(), alias


class WikilinkPattern(InlineProcessor):
    def __init__(self, resolver: LinkResolver):
        super().__init__(r"(?<!!)\[\[([^\[\]\n]+?)\]\]")
        self.resolver = resolver

    def handleMatch(self, m, data):  # noqa: N802 - markdown API
        raw, alias = _split_alias(m.group(1))
        name = link_target(raw)
        heading = raw.split("#", 1)[1] if "#" in raw else ""
        label = alias or (f"{name} › {heading}" if heading else name)
        return _link(label, self.resolver.note(name)), m.start(0), m.end(0)


class EmbedPattern(InlineProcessor):
    def __init__(self, resolver: LinkResolver):
        super().__init__(r"!\[\[([^\[\]\n]+?)\]\]")
        self.resolver = resolver

    def handleMatch(self, m, data):  # noqa: N802
        view = self.resolver.view
        raw, size = _split_alias(m.group(1))
        name = raw.split("#", 1)[0].strip()
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension in IMAGE_EXTS:
            found = find_attachment(view, name)
            if found is None:
                return _chip(name, "missing", "image not found"), m.start(0), m.end(0)
            el = etree.Element("img")
            el.set("src", image_url(view.project_id, found[0].rsplit("/", 1)[-1]))
            el.set("alt", name)
            el.set("loading", "lazy")
            el.set("class", "embed")
            if size and EMBED_WIDTH.fullmatch(size):
                el.set("width", size)
            return el, m.start(0), m.end(0)
        if extension:
            return _chip(name, "file", "attachment"), m.start(0), m.end(0)
        link = _link(name, self.resolver.note(name), "embedded note (shown as a link)")
        return link, m.start(0), m.end(0)


class HubRefPattern(InlineProcessor):
    """`[[hub]] #12, #13` -> the hub link and one decision link per number."""

    def __init__(self, resolver: LinkResolver, pattern: re.Pattern):
        super().__init__(pattern.pattern)
        self.resolver = resolver

    def handleMatch(self, m, data):  # noqa: N802
        project_id = self.resolver.view.project_id
        wrapper = etree.Element("span")
        wrapper.set("class", "hubref")
        piped = re.match(r"\\?\|(.*)", m.group("inner"), re.S)
        alias = piped.group(1).strip() if piped else None
        hub = _link(
            alias or self.resolver.hub or "",
            LinkTarget(page_url(project_id, "decisions"), "decisions"),
        )
        hub.tail = " "
        wrapper.append(hub)
        previous = hub
        for part in re.split(r"(#\d+)", m.group("nums")):
            if re.fullmatch(r"#\d+", part):
                previous = etree.SubElement(wrapper, "a")
                previous.set("href", page_url(project_id, "decision", part[1:]))
                previous.set("class", "wl decision")
                previous.text = part
            elif part:
                previous.tail = (previous.tail or "") + part
        return wrapper, m.start(0), m.end(0)


class StripComments(Preprocessor):
    """Drop `<!-- ... -->` (Obsidian hides them) and draw task boxes, outside code."""

    @staticmethod
    def _prose(text: str) -> str:
        text = HTML_COMMENT_RE.sub("", text)
        return TASK_LINE.sub(
            lambda m: m.group(1) + ("\u2610" if m.group(2) == " " else "\u2611"), text
        )

    def run(self, lines):
        text = "\n".join(lines)
        out, last = [], 0
        for fence in FENCE_RE.finditer(text):
            out.append(self._prose(text[last : fence.start()]))
            out.append(fence.group(0))
            last = fence.end()
        out.append(self._prose(text[last:]))
        return "".join(out).split("\n")


class SanitizeLinks(Treeprocessor):
    """Only http(s), mailto and in-page links survive; remote images become links."""

    def run(self, root):
        for el in list(root.iter()):
            if el.tag == "a":
                self._anchor(el)
            elif el.tag == "img":
                self._image(el)
        return root

    @staticmethod
    def _anchor(el) -> None:
        href = (el.get("href") or "").strip()
        lowered = href.lower()
        if lowered.startswith(SAFE_SCHEMES):
            el.set("target", "_blank")
            el.set("rel", "noopener noreferrer")
        elif not href.startswith("#") and "class" not in el.attrib:
            del el.attrib["href"]
            el.tag = "span"
            el.set("class", "wl missing")
            el.set("title", "link target is not allowed here")

    @staticmethod
    def _image(el) -> None:
        if "class" in el.attrib:  # produced by EmbedPattern
            return
        src = (el.get("src") or "").strip()
        alt = el.get("alt") or src
        el.attrib.clear()
        el.text = alt
        if src.lower().startswith(SAFE_SCHEMES):
            el.tag = "a"
            el.set("href", src)
            el.set("target", "_blank")
            el.set("rel", "noopener noreferrer")
        else:
            el.tag = "span"
            el.set("class", "wl missing")
            el.set("title", "image outside the vault is not shown")


class ObsidianExtension(Extension):
    def __init__(self, resolver: LinkResolver):
        super().__init__()
        self.resolver = resolver

    def extendMarkdown(self, md):  # noqa: N802
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.preprocessors.register(StripComments(md), "strip_comments", 30)
        # Above `escape` (180) so a table-escaped pipe is still a pipe here.
        hub = decision_hub_pattern(self.resolver.view.profile)
        if hub is not None:
            md.inlinePatterns.register(
                HubRefPattern(self.resolver, hub), "hub_ref", 188
            )
        md.inlinePatterns.register(EmbedPattern(self.resolver), "embed", 187)
        md.inlinePatterns.register(WikilinkPattern(self.resolver), "wikilink", 186)
        md.treeprocessors.register(SanitizeLinks(md), "sanitize_links", 0)


def strip_frontmatter(text: str) -> str:
    text = normalize_text(text)
    try:
        fm_text, body = split_frontmatter(text)
    except Exception:  # noqa: BLE001 - unparsable frontmatter: show the text as is
        return text
    return body if fm_text is not None else text


def _engine(view: ProjectView, run_app: bool) -> markdown.Markdown:
    return markdown.Markdown(
        extensions=[
            "fenced_code",
            "sane_lists",
            "tables",
            "toc",
            ObsidianExtension(LinkResolver(view, run_app)),
        ],
        extension_configs={
            "toc": {"slugify": slugify_unicode, "anchorlink": False},
            "tables": {"use_align_attribute": True},
        },
        output_format="html",
    )


def render_markdown(text: str, view: ProjectView, *, run_app: bool = False) -> str:
    """HTML for a note body (frontmatter removed if still present)."""
    return _engine(view, run_app).convert(strip_frontmatter(text))


BREAK_RE = re.compile(r"<br\s*/?>", re.I)
ONE_PARAGRAPH = re.compile(r"^<p>(.*)</p>$", re.S)


def render_inline_cells(
    cells: list[str], view: ProjectView, *, run_app: bool = False
) -> list[str]:
    """Inline HTML for table cells: links, code, bold; a literal `<br>` is a line break.

    Goes through the same sanitising engine as note bodies, so raw tags stay escaped;
    the only tag added here is the `<br>` that separates rendered segments.
    """
    engine = _engine(view, run_app)
    rendered = []
    for cell in cells:
        segments = []
        for segment in BREAK_RE.split(cell):
            engine.reset()
            html = engine.convert(segment.strip()) if segment.strip() else ""
            single = ONE_PARAGRAPH.match(html)
            lone = single and "<p>" not in html[3:]
            segments.append(single.group(1) if lone else html)
        rendered.append("<br>".join(segments))
    return rendered
