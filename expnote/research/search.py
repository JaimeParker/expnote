"""Search over the index: trigram FTS, LIKE below three characters, link filters."""

from __future__ import annotations

import dataclasses
import sqlite3

from expnote.research.entities import paginate, truncate
from expnote.research.errors import ResearchError
from expnote.research.index import IMAGE_EXTS
from expnote.research.textkit import fold, link_stem, link_target
from expnote.research.view import ProjectView

TRIGRAM_MIN = 3
SNIPPET_LIMIT = 200


@dataclasses.dataclass
class SearchFilters:
    note_type: str | None = None
    plan: str | None = None
    status: str | None = None
    linked_to: str | None = None
    backlinks_of: str | None = None
    images: bool = False
    whole_vault: bool = False


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_expression(terms: list[str]) -> str:
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _target_clause(view: ProjectView, raw: str) -> tuple[str, list]:
    """SQL (over alias `l` = links) selecting links that point at the named note."""
    name = link_stem(link_target(raw))
    ids = [
        r[0]
        for r in view.conn.execute(
            "SELECT file_id FROM notes WHERE cstem = ?", (fold(name),)
        )
    ]
    marks = ",".join("?" * len(ids)) or "NULL"
    return (
        f"(l.resolved_file_id IN ({marks}) OR l.target_stem = ? COLLATE NOCASE)",
        [*ids, name],
    )


def _first_match_line(text: str, offset: int, terms: list[str]) -> tuple[int, str]:
    """(1-based line in the file, line text) of the first term occurrence."""
    lowered = [t.casefold() for t in terms]
    lines = text.split("\n")
    for index, line in enumerate(lines):
        low = line.casefold()
        if any(t in low for t in lowered):
            return offset + index + 1, line.strip()
    return offset + 1, next((row.strip() for row in lines if row.strip()), "")


def search_notes(
    view: ProjectView,
    query: str,
    filters: SearchFilters,
    *,
    limit: int | None,
    cursor: str | None,
) -> dict:
    """`{matchMode, total, hits}`; `meta.nextCursor` comes from `paginate`."""
    if filters.images:
        return _search_images(view, query, limit, cursor)
    terms = query.split()
    no_text = not terms
    if no_text and not (
        filters.backlinks_of
        or filters.linked_to
        or filters.note_type
        or filters.plan
        or filters.status
    ):
        raise ResearchError(
            "USAGE_EMPTY_SEARCH",
            "give a query or at least one filter",
            exit_code=2,
        )
    where = ["f.is_conflict = 0"]
    params: list = []
    joins = "JOIN notes n ON n.file_id = f.id"
    if no_text:
        mode = "none"
    elif all(len(t) >= TRIGRAM_MIN for t in terms):
        mode = "fts"
        joins = (
            "JOIN fts_notes ON fts_notes.rowid = f.id JOIN notes n ON n.file_id = f.id"
        )
        where.append("fts_notes MATCH ?")
        params.append(_fts_expression(terms))
    else:
        mode = "like"
        for term in terms:
            where.append(
                "(n.body LIKE ? ESCAPE '\\' OR n.stem LIKE ? ESCAPE '\\' "
                "OR n.title LIKE ? ESCAPE '\\')"
            )
            params += [_like(term)] * 3
    if not filters.whole_vault:
        where.append("substr(f.rel, 1, ?) = ?")
        params += [len(view.prefix) + 1, view.prefix + "/"]
    for column, value in (
        ("n.type", filters.note_type),
        ("n.plan_id", filters.plan),
        ("n.status", filters.status),
    ):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    link_filter = filters.backlinks_of or filters.linked_to
    link_select = ""
    if link_filter:
        clause, extra = _target_clause(view, link_filter)
        link_select = ", MIN(l.line) AS link_line"
        joins += " JOIN links l ON l.src_file_id = f.id"
        where.append(clause)
        params += extra
    order = "ORDER BY fts_notes.rank, f.nrel" if mode == "fts" else "ORDER BY f.nrel"
    sql = (
        "SELECT f.id, f.rel, n.stem, n.type, n.entity_id, n.status, n.fm_raw, n.body"
        f"{link_select} FROM files f {joins} WHERE {' AND '.join(where)} "
        f"{'GROUP BY f.id ' if link_filter else ''}{order}"
    )
    try:
        rows = view.conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as error:
        raise ResearchError(
            "USAGE_BAD_QUERY", f"search failed: {error}", exit_code=2
        ) from None
    page, next_cursor = paginate(rows, limit, cursor)
    hits = []
    for row in page:
        offset = row["fm_raw"].count("\n")
        full = row["fm_raw"] + row["body"]
        if link_filter and no_text:
            line = row["link_line"]
            text = full.split("\n")[line - 1].strip()
        elif terms:
            line, text = _first_match_line(row["body"], offset, terms)
        else:
            line, text = 1, ""
        hits.append(
            {
                "stem": row["stem"],
                "path": row["rel"],
                "type": row["type"],
                "entityId": row["entity_id"],
                "status": row["status"],
                "line": line,
                "snippet": f"{row['rel']}:{line}: {truncate(text, SNIPPET_LIMIT)}",
            }
        )
    return {
        "matchMode": mode,
        "total": len(rows),
        "hits": hits,
        "_nextCursor": next_cursor,
    }


def _search_images(
    view: ProjectView, query: str, limit: int | None, cursor: str | None
) -> dict:
    exts = tuple(IMAGE_EXTS)
    rows = [
        r
        for r in view.conn.execute(
            "SELECT f.id, f.rel, (SELECT COUNT(*) FROM links l WHERE "
            "l.resolved_file_id = f.id AND l.kind = 'embed') AS embedded "
            "FROM files f WHERE f.kind = 'attachment' AND f.is_conflict = 0 "
            "ORDER BY f.nrel"
        )
        if r["rel"].rsplit(".", 1)[-1].lower() in exts
        and (not query or query.casefold() in r["rel"].rsplit("/", 1)[-1].casefold())
    ]
    page, next_cursor = paginate(rows, limit, cursor)
    hits = [
        {
            "stem": r["rel"].rsplit("/", 1)[-1],
            "path": r["rel"],
            "type": "image",
            "entityId": None,
            "status": None,
            "line": None,
            "snippet": f"{r['rel']}: embedded by {r['embedded']} note(s)",
            "embeddedBy": r["embedded"],
        }
        for r in page
    ]
    return {
        "matchMode": "name",
        "total": len(rows),
        "hits": hits,
        "_nextCursor": next_cursor,
    }
