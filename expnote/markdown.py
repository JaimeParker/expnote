from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from expnote.db import (
    paths_for,
    read_config,
    readonly_transaction,
    row_to_dict,
    transaction,
)
from expnote.links import render_obsidian_run_links

MANAGED_START = "<!-- expnote:managed:start -->"
MANAGED_END = "<!-- expnote:managed:end -->"
ANALYSIS_START = "<!-- expnote:analysis:start -->"
ANALYSIS_END = "<!-- expnote:analysis:end -->"
DOC_BODY_START = "<!-- expnote:doc-body:start -->"
DOC_BODY_END = "<!-- expnote:doc-body:end -->"
MOC_TABLE_START = "<!-- expnote:moc-table:start -->"
MOC_TABLE_END = "<!-- expnote:moc-table:end -->"


def sync_markdown(
    root: Path,
    state_dir: Path | None = None,
    *,
    pull_analysis: bool = False,
    pull_docs: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    config = read_config(root, state_dir=state_dir)
    notes_dir = root / config["notes_dir"]
    docs_dir = root / config["docs_dir"]
    index_path = _auto_index_path(root, state_dir, config)
    notes_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_auto_index_target(index_path)

    with readonly_transaction(root, state_dir=state_dir) as conn:
        topics = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT topics.*, mocs.title AS moc_title
                FROM topics JOIN mocs ON mocs.id = topics.moc_id
                WHERE topics.deleted_at IS NULL AND mocs.deleted_at IS NULL
                ORDER BY mocs.title ASC, topics.created_at DESC, topics.title DESC
                """
            )
        ]
        runs_by_topic: dict[str, list[dict[str, Any]]] = {}
        for topic in topics:
            runs_by_topic[topic["id"]] = [
                row_to_dict(row, include_internal=True)
                for row in conn.execute(
                    """
                    SELECT * FROM runs
                    WHERE topic_id = ? AND deleted_at IS NULL
                    ORDER BY started_at ASC, id ASC
                    """,
                    (topic["id"],),
                )
            ]
        docs = [
            row_to_dict(row, include_internal=True)
            for row in conn.execute(
                """
                SELECT docs.*, mocs.title AS moc_title
                FROM docs JOIN mocs ON docs.moc_id = mocs.id
                WHERE docs.deleted_at IS NULL AND mocs.deleted_at IS NULL
                ORDER BY docs.updated_at DESC, docs.id DESC
                """
            )
        ]
        related_docs_by_run = _related_docs_by_run(conn)
        for doc in docs:
            doc["runs"] = _doc_related_runs(conn, str(doc["id"]))
        for runs in runs_by_topic.values():
            for run in runs:
                run["related_docs"] = related_docs_by_run.get(str(run["id"]), [])
        active_run_ids = _active_run_ids(conn)
        benchmarks = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT * FROM benchmarks
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC, id DESC
                """
            )
        ]
        for benchmark in benchmarks:
            benchmark["tasks"] = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM benchmark_tasks
                    WHERE benchmark_id = ? AND deleted_at IS NULL
                    ORDER BY position ASC, created_at ASC
                    """,
                    (benchmark["id"],),
                )
            ]
            benchmark["algos"] = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM benchmark_algos
                    WHERE benchmark_id = ? AND deleted_at IS NULL
                    ORDER BY position ASC, created_at ASC
                    """,
                    (benchmark["id"],),
                )
            ]
            benchmark["cells"] = [
                row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        benchmark_cells.task_id,
                        benchmark_cells.algo_id,
                        benchmark_cells.run_id,
                        runs.status,
                        runs.result
                    FROM benchmark_cells
                    JOIN runs ON runs.id = benchmark_cells.run_id
                    WHERE benchmark_cells.benchmark_id = ?
                        AND benchmark_cells.deleted_at IS NULL
                        AND runs.deleted_at IS NULL
                    """,
                    (benchmark["id"],),
                )
            ]

    moc_content = _render_moc(topics, runs_by_topic, active_run_ids)
    _write_managed_file(index_path, moc_content)

    written_runs = 0
    pulled_analysis = 0
    for runs in runs_by_topic.values():
        for run in runs:
            run_path = notes_dir / f"{_safe_filename(run['id'])}.md"
            run = _resolve_analysis(
                root,
                state_dir,
                run,
                run_path,
                pull_analysis=pull_analysis,
                force=force,
            )
            rendered_analysis = _link_runs(str(run["analysis"]), active_run_ids)
            _write_managed_file(run_path, _render_run_note(run, active_run_ids))
            _set_analysis_hash(root, state_dir, str(run["id"]), rendered_analysis)
            if run.get("_analysis_pulled"):
                pulled_analysis += 1
            written_runs += 1

    written_docs = 0
    pulled_docs = 0
    for doc in docs:
        doc_path = docs_dir / f"{_safe_filename(doc['id'])}.md"
        doc = _resolve_doc_body(
            root,
            state_dir,
            doc,
            doc_path,
            pull_docs=pull_docs,
            force=force,
        )
        rendered_body = _doc_body_rendered_text(
            _link_runs(str(doc.get("body") or ""), active_run_ids)
        )
        _write_managed_file(doc_path, _render_doc_note(doc, active_run_ids))
        _set_doc_body_hash(root, state_dir, str(doc["id"]), rendered_body)
        if doc.get("_body_pulled"):
            pulled_docs += 1
        written_docs += 1

    written_benchmarks = 0
    for benchmark in benchmarks:
        benchmark_path = notes_dir / f"{_safe_filename(benchmark['id'])}.md"
        _write_managed_file(
            benchmark_path, _render_benchmark_note(benchmark)
        )
        written_benchmarks += 1

    return {
        "index": str(index_path),
        "benchmark_notes": written_benchmarks,
        "moc": str(index_path),
        "run_notes": written_runs,
        "doc_notes": written_docs,
        "pulled_analysis": pulled_analysis,
        "pulled_docs": pulled_docs,
    }


def _render_moc(
    topics: list[dict[str, Any]],
    runs_by_topic: dict[str, list[dict[str, Any]]],
    active_run_ids: set[str],
) -> str:
    lines = [
        "# Experiment MOC",
        "",
        "This section is generated by expnote. Write long-form analysis in run notes.",
        "",
    ]
    current_moc_id = None
    for topic in topics:
        if topic.get("moc_id") != current_moc_id:
            current_moc_id = topic.get("moc_id")
            lines.extend([f"## {topic.get('moc_title') or current_moc_id}", ""])
        lines.extend([f"### {topic['title']}", ""])
        if topic["summary"]:
            lines.extend([topic["summary"], ""])
        lines.extend(
            [
                "| # | run | purpose | relation | result | status |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for index, run in enumerate(runs_by_topic.get(topic["id"], []), start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        f"[[{run['id']}]]",
                        _cell(_link_runs(run["purpose"], active_run_ids)),
                        _cell(_link_runs(run["relation"], active_run_ids)),
                        _cell(_link_runs(run["result"], active_run_ids)),
                        _cell(run["status"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_run_note(run: dict[str, Any], active_run_ids: set[str]) -> str:
    metadata = run.get("metadata", {})
    analysis = _link_runs(str(run.get("analysis") or ""), active_run_ids)
    related_docs = run.get("related_docs", [])
    lines = [
        f"# {run['id']}",
        "",
        f"- status: `{run['status']}`",
        f"- started_at: `{run['started_at']}`",
        "",
        "## Purpose",
        "",
        _link_runs(run["purpose"], active_run_ids) or "_TBD_",
        "",
        "## Relation",
        "",
        _link_runs(run["relation"], active_run_ids) or "_TBD_",
        "",
        "## Result",
        "",
        _link_runs(run["result"], active_run_ids) or "_TBD_",
        "",
        "## Metadata",
        "",
    ]
    if metadata:
        lines.extend(
            f"- `{key}`: {_metadata_value(value)}"
            for key, value in sorted(metadata.items())
        )
    else:
        lines.append("_No metadata recorded._")
    if related_docs:
        lines.extend(["", "## Related Docs", ""])
        lines.extend(
            f"- [[{doc['doc_id']}]] {doc['title']}" for doc in related_docs
        )
    lines.extend(
        [
            "",
            "## Analysis",
            "",
            ANALYSIS_START,
            "",
            analysis or "Write analysis here.",
            "",
            ANALYSIS_END,
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_doc_note(doc: dict[str, Any], active_run_ids: set[str]) -> str:
    metadata = doc.get("metadata", {})
    lines = [
        f"# {doc['title']}",
        "",
        f"- id: `{doc['id']}`",
        f"- moc: {doc['moc_title']}",
        f"- updated_at: `{doc['updated_at']}`",
        "",
        "## Metadata",
        "",
    ]
    if metadata:
        lines.extend(
            f"- `{key}`: {_metadata_value(value)}"
            for key, value in sorted(metadata.items())
        )
    else:
        lines.append("_No metadata recorded._")
    lines.extend(
        [
            "",
            "## Related Runs",
            "",
            _render_doc_runs_table(doc.get("runs", []), active_run_ids).rstrip(),
            "",
            "## Body",
            "",
            DOC_BODY_START,
            "",
            _doc_body_rendered_text(
                _link_runs(str(doc.get("body") or ""), active_run_ids)
            ),
            "",
            DOC_BODY_END,
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_doc_runs_table(rows: list[dict[str, Any]], active_run_ids: set[str]) -> str:
    lines = [
        "| # | run | role | note | status | result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"[[{row['run_id']}]]",
                    _cell(_link_runs(row["role"], active_run_ids)),
                    _cell(_link_runs(row["note"], active_run_ids)),
                    _cell(row["status"]),
                    _cell(_link_runs(row["result"], active_run_ids)),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _render_benchmark_note(benchmark: dict[str, Any]) -> str:
    tasks = benchmark.get("tasks", [])
    algos = benchmark.get("algos", [])
    cell_lookup = {
        (cell["task_id"], cell["algo_id"]): cell for cell in benchmark.get("cells", [])
    }
    lines = [
        f"# {benchmark['title']}",
        "",
        f"- id: `{benchmark['id']}`",
        f"- updated_at: `{benchmark['updated_at']}`",
        "",
    ]
    if benchmark["summary"]:
        lines.extend([benchmark["summary"], ""])
    lines.extend(["## Matrix", ""])
    if not tasks or not algos:
        lines.append("_No tasks/algos recorded yet._")
        return "\n".join(lines).rstrip() + "\n"
    header = ["task"] + [str(algo["title"]) for algo in algos]
    lines.append("| " + " | ".join(_cell(value) for value in header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for task in tasks:
        row = [_cell(str(task["title"]))]
        for algo in algos:
            cell = cell_lookup.get((task["id"], algo["id"]))
            if cell is None:
                row.append("—")
                continue
            row.append(_cell(f"[[{cell['run_id']}]]"))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines).rstrip() + "\n"


def sync_moc_section(
    root: Path,
    state_dir: Path | None,
    moc_path: str,
    section: str,
) -> dict[str, Any]:
    path = root / moc_path
    _ensure_curated_moc_target(path)
    rows = _moc_section_rows(root, state_dir, moc_path, section)
    with readonly_transaction(root, state_dir=state_dir) as conn:
        active_run_ids = _active_run_ids(conn)
    table = _render_moc_table(rows, active_run_ids)
    _write_moc_section_table(path, section, table)
    return {"moc_path": str(path), "section": section, "rows": len(rows)}


def diff_moc_section(
    root: Path,
    state_dir: Path | None,
    moc_path: str,
    section: str,
) -> dict[str, Any]:
    path = root / moc_path
    rows = _moc_section_rows(root, state_dir, moc_path, section)
    with readonly_transaction(root, state_dir=state_dir) as conn:
        active_run_ids = _active_run_ids(conn)
    expected = _render_moc_table(rows, active_run_ids).strip()
    observed = _extract_moc_table(path, section).strip()
    expected_ids = [str(row["run_id"]) for row in rows]
    observed_ids = _run_ids_from_table(observed)
    conflicts = _curated_moc_conflicts(path)
    return {
        "moc_path": str(path),
        "section": section,
        "conflicts": conflicts,
        "changed": expected != observed,
        "expected": expected_ids,
        "observed": observed_ids,
        "missing": [run_id for run_id in expected_ids if run_id not in observed_ids],
        "stale": [run_id for run_id in observed_ids if run_id not in expected_ids],
    }


def projection_conflicts(
    root: Path,
    state_dir: Path | None = None,
) -> list[dict[str, str]]:
    conflicts = []
    config = read_config(root, state_dir=state_dir)
    auto_index = _auto_index_path(root, state_dir, config)
    conflicts.extend(_auto_index_conflicts(auto_index))
    with readonly_transaction(root, state_dir=state_dir) as conn:
        moc_paths = [
            str(row["moc_path"])
            for row in conn.execute(
                """
                SELECT DISTINCT moc_path FROM moc_entries
                WHERE deleted_at IS NULL
                ORDER BY moc_path ASC
                """
            )
        ]
    for moc_path in moc_paths:
        conflicts.extend(_curated_moc_conflicts(root / moc_path))
    return conflicts


def ensure_curated_moc_target(path: Path) -> None:
    _ensure_curated_moc_target(path)


def _auto_index_path(
    root: Path,
    state_dir: Path | None,
    config: dict[str, str],
) -> Path:
    if "index_path" in config:
        return paths_for(root, state_dir).state_dir / config["index_path"]
    return root / config["moc_path"]


def _resolve_analysis(
    root: Path,
    state_dir: Path | None,
    run: dict[str, Any],
    run_path: Path,
    *,
    pull_analysis: bool,
    force: bool,
) -> dict[str, Any]:
    existing_analysis = _extract_analysis(run_path)
    if existing_analysis is None:
        return run

    current_hash = _hash_text(existing_analysis)
    rendered_hash = str(run.get("analysis_rendered_hash") or "")
    if rendered_hash and current_hash != rendered_hash:
        if pull_analysis:
            run = dict(run)
            run["analysis"] = existing_analysis
            run["_analysis_pulled"] = True
            with transaction(root, state_dir=state_dir) as conn:
                conn.execute(
                    "UPDATE runs SET analysis = ?, updated_at = ? WHERE id = ?",
                    (existing_analysis, _now_for_update(), run["id"]),
                )
            return run
        if not force:
            raise RuntimeError(
                f"{run_path} has changed Analysis. Use --pull-analysis or --force."
            )
    return run


def _set_analysis_hash(
    root: Path,
    state_dir: Path | None,
    run_id: str,
    analysis: str,
) -> None:
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            "UPDATE runs SET analysis_rendered_hash = ? WHERE id = ?",
            (_hash_text(analysis or "Write analysis here."), run_id),
        )


def _resolve_doc_body(
    root: Path,
    state_dir: Path | None,
    doc: dict[str, Any],
    doc_path: Path,
    *,
    pull_docs: bool,
    force: bool,
) -> dict[str, Any]:
    existing_body = _extract_doc_body(doc_path)
    if existing_body is None:
        return doc

    current_hash = _hash_text(existing_body)
    rendered_hash = str(doc.get("body_rendered_hash") or "")
    if rendered_hash and current_hash != rendered_hash:
        if pull_docs:
            doc = dict(doc)
            doc["body"] = existing_body
            doc["_body_pulled"] = True
            with transaction(root, state_dir=state_dir) as conn:
                conn.execute(
                    "UPDATE docs SET body = ?, updated_at = ? WHERE id = ?",
                    (existing_body, _now_for_update(), doc["id"]),
                )
            return doc
        if not force:
            raise RuntimeError(
                f"{doc_path} has changed document body. Use --pull-docs or --force."
            )
    return doc


def _set_doc_body_hash(
    root: Path,
    state_dir: Path | None,
    doc_id: str,
    body: str,
) -> None:
    with transaction(root, state_dir=state_dir) as conn:
        conn.execute(
            "UPDATE docs SET body_rendered_hash = ? WHERE id = ?",
            (_hash_text(_doc_body_rendered_text(body)), doc_id),
        )


def _extract_analysis(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(
        re.escape(ANALYSIS_START) + r"\n?(.*?)\n?" + re.escape(ANALYSIS_END),
        path.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    if match is None:
        return None
    return _strip_marker_padding(match.group(1))


def _extract_doc_body(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(
        re.escape(DOC_BODY_START) + r"\n?(.*?)\n?" + re.escape(DOC_BODY_END),
        path.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    if match is None:
        return None
    return _strip_marker_padding(match.group(1))


def _doc_body_rendered_text(body: str) -> str:
    return body or "Write document body here."


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_for_update() -> str:
    from expnote.db import now_iso

    return now_iso()


def _moc_section_rows(
    root: Path,
    state_dir: Path | None,
    moc_path: str,
    section: str,
) -> list[dict[str, Any]]:
    with readonly_transaction(root, state_dir=state_dir) as conn:
        return [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT
                    moc_entries.id,
                    moc_entries.moc_path,
                    moc_entries.section,
                    moc_entries.run_id,
                    moc_entries.position,
                    runs.purpose,
                    runs.relation,
                    runs.result,
                    runs.status
                FROM moc_entries
                JOIN runs ON runs.id = moc_entries.run_id
                WHERE
                    moc_entries.moc_path = ?
                    AND moc_entries.section = ?
                    AND moc_entries.deleted_at IS NULL
                    AND runs.deleted_at IS NULL
                ORDER BY moc_entries.position ASC, moc_entries.created_at ASC
                """,
                (moc_path, section),
            )
        ]


def _doc_related_runs(conn: Any, doc_id: str) -> list[dict[str, Any]]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT
                doc_runs.id,
                doc_runs.doc_id,
                doc_runs.run_id,
                doc_runs.position,
                doc_runs.role,
                doc_runs.note,
                runs.status,
                runs.purpose,
                runs.result
            FROM doc_runs
            JOIN runs ON runs.id = doc_runs.run_id
            WHERE
                doc_runs.doc_id = ?
                AND doc_runs.deleted_at IS NULL
                AND runs.deleted_at IS NULL
            ORDER BY doc_runs.position ASC, doc_runs.created_at ASC
            """,
            (doc_id,),
        )
    ]


def _related_docs_by_run(conn: Any) -> dict[str, list[dict[str, Any]]]:
    related: dict[str, list[dict[str, Any]]] = {}
    rows = conn.execute(
        """
        SELECT
            doc_runs.run_id,
            doc_runs.doc_id,
            doc_runs.position,
            docs.title
        FROM doc_runs
        JOIN docs ON docs.id = doc_runs.doc_id
        JOIN runs ON runs.id = doc_runs.run_id
        WHERE
            doc_runs.deleted_at IS NULL
            AND docs.deleted_at IS NULL
            AND runs.deleted_at IS NULL
        ORDER BY doc_runs.run_id ASC, docs.updated_at DESC, docs.id DESC
        """
    )
    for row in rows:
        related.setdefault(str(row["run_id"]), []).append(row_to_dict(row))
    return related


def _active_run_ids(conn: Any) -> set[str]:
    return {
        str(row["id"])
        for row in conn.execute("SELECT id FROM runs WHERE deleted_at IS NULL")
    }


def _link_runs(value: str, active_run_ids: set[str]) -> str:
    return render_obsidian_run_links(value or "", active_run_ids)


def _render_moc_table(rows: list[dict[str, Any]], active_run_ids: set[str]) -> str:
    lines = [
        "| # | run | purpose | relation | result | status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"[[{row['run_id']}]]",
                    _cell(_link_runs(row["purpose"], active_run_ids)),
                    _cell(_link_runs(row["relation"], active_run_ids)),
                    _cell(_link_runs(row["result"], active_run_ids)),
                    _cell(row["status"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _write_moc_section_table(path: Path, section: str, table: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    block = f"{MOC_TABLE_START}\n\n{table.rstrip()}\n\n{MOC_TABLE_END}"
    if not content:
        path.write_text(
            f"# Experiment MOC\n\n## {section}\n\n{block}\n",
            encoding="utf-8",
        )
        return

    section_match = _find_h2_section(content, section)
    if section_match is None:
        updated = content.rstrip() + f"\n\n## {section}\n\n{block}\n"
        path.write_text(updated, encoding="utf-8")
        return

    start, end = section_match
    section_text = content[start:end]
    marker_re = re.compile(
        re.escape(MOC_TABLE_START) + r".*?" + re.escape(MOC_TABLE_END),
        re.DOTALL,
    )
    if marker_re.search(section_text):
        new_section = marker_re.sub(block, section_text, count=1)
    else:
        heading_end = section_text.find("\n") + 1
        new_section = (
            section_text[:heading_end]
            + "\n"
            + block
            + "\n"
            + section_text[heading_end:]
        )
    path.write_text(content[:start] + new_section + content[end:], encoding="utf-8")


def _extract_moc_table(path: Path, section: str) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    section_match = _find_h2_section(content, section)
    if section_match is None:
        return ""
    section_text = content[section_match[0] : section_match[1]]
    match = re.search(
        re.escape(MOC_TABLE_START) + r"\n?(.*?)\n?" + re.escape(MOC_TABLE_END),
        section_text,
        re.DOTALL,
    )
    return _strip_marker_padding(match.group(1)) if match else ""


def _find_h2_section(content: str, section: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.MULTILINE)
    match = pattern.search(content)
    if match is None:
        return None
    next_match = re.search(r"^##\s+", content[match.end() :], re.MULTILINE)
    end = len(content) if next_match is None else match.end() + next_match.start()
    return match.start(), end


def _run_ids_from_table(table: str) -> list[str]:
    ids = []
    run_column: int | None = None
    for line in table.splitlines():
        cells = _markdown_table_cells(line)
        if not cells or _is_markdown_table_separator(cells):
            continue
        if run_column is None:
            headers = [cell.lower() for cell in cells]
            if "run" in headers:
                run_column = headers.index("run")
            continue
        if len(cells) <= run_column:
            continue
        run_cell = cells[run_column].strip()
        if not run_cell:
            continue
        match = re.fullmatch(r"\[\[([^\]]+)\]\]", run_cell)
        ids.append(match.group(1) if match else run_cell)
    return ids


def _markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or "|" not in stripped[1:]:
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_markdown_table_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _ensure_auto_index_target(path: Path) -> None:
    conflicts = _auto_index_conflicts(path)
    if conflicts:
        raise RuntimeError(_projection_conflict_message(conflicts[0]))


def _ensure_curated_moc_target(path: Path) -> None:
    conflicts = _curated_moc_conflicts(path)
    if conflicts:
        raise RuntimeError(_projection_conflict_message(conflicts[0]))


def _auto_index_conflicts(path: Path) -> list[dict[str, str]]:
    if not _contains_marker(path, MOC_TABLE_START):
        return []
    return [
        {
            "path": str(path),
            "kind": "auto_index_contains_curated_moc_table",
            "message": (
                "auto index path contains expnote:moc-table; use a different "
                "init --index-path or curated moc --moc-path"
            ),
        }
    ]


def _curated_moc_conflicts(path: Path) -> list[dict[str, str]]:
    if not _contains_marker(path, MANAGED_START):
        return []
    return [
        {
            "path": str(path),
            "kind": "curated_moc_contains_auto_index",
            "message": (
                "curated MOC path contains expnote:managed; use a different "
                "moc --moc-path or init --index-path"
            ),
        }
    ]


def _contains_marker(path: Path, marker: str) -> bool:
    return path.exists() and marker in path.read_text(encoding="utf-8")


def _projection_conflict_message(conflict: dict[str, str]) -> str:
    return f"projection conflict at {conflict['path']}: {conflict['message']}"


def _write_managed_file(path: Path, managed_content: str) -> None:
    managed_block = f"{MANAGED_START}\n\n{managed_content.rstrip()}\n\n{MANAGED_END}\n"
    if not path.exists():
        path.write_text(managed_block, encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(managed_block, existing, count=1)
    else:
        updated = managed_block + "\n" + existing
    path.write_text(updated, encoding="utf-8")


def _cell(value: str) -> str:
    return (value or "").replace("\n", "<br>").replace("|", "\\|")


def _metadata_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _strip_marker_padding(value: str) -> str:
    if value.startswith("\n"):
        value = value[1:]
    if value.endswith("\n"):
        value = value[:-1]
    return value


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "run"
