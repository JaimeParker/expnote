"""Read-only HTTP surface of the research layer (GUI pages and `/api/research`).

Registry driven: nothing here is bound to an expnote workspace. Every endpoint is a
GET; no handler writes the vault. Responses carry the research CSP.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from expnote.research import entities
from expnote.research.bundle import (
    build_bundle,
    byte_size,
    fit_sections,
    parse_sections,
)
from expnote.research.checks import run_checks
from expnote.research.cli import _parse_levels, check_data
from expnote.research.doctor import doctor_data, run_doctor
from expnote.research.envelope import (
    CommandResult,
    failure_envelope,
    success_envelope,
)
from expnote.research.errors import ResearchError
from expnote.research.home import build_home, display_strings, plan_rows
from expnote.research.index import IMAGE_EXTS, refresh_index
from expnote.research.registry import (
    ProjectContext,
    load_registry,
    open_project,
    registry_path,
    revalidate,
)
from expnote.research.render import (
    LinkResolver,
    find_attachment,
    render_markdown,
)
from expnote.research.search import SearchFilters, search_notes
from expnote.research.todos import all_todos, filter_todos
from expnote.research.view import ProjectView, open_view

STATIC_DIR = Path(__file__).parent / "static"
ASSET_TYPES = {".js": "text/javascript", ".css": "text/css"}
CSP = "default-src 'self'; img-src 'self' data:"
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "bmp": "image/bmp",
}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
REFRESH_INTERVAL_SECONDS = 2.0
BACKLINK_LIMIT = 100
STATUS_BY_EXIT = {2: 422, 3: 404, 5: 409, 6: 503}
STATUS_BY_CODE = {"IMAGE_UNSUPPORTED_TYPE": 415, "IMAGE_TOO_LARGE": 413}


def research_available() -> bool:
    """True when the project registry names at least one project."""
    try:
        return bool(load_registry().projects)
    except ResearchError:
        return False


def _json(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status,
        headers={**SECURITY_HEADERS, "Cache-Control": "no-store"},
    )


def _failure(command: str, error: ResearchError) -> JSONResponse:
    status = STATUS_BY_CODE.get(error.code)
    if status is None:
        status = 409 if error.code.startswith("AMBIGUOUS") else None
    status = status or STATUS_BY_EXIT.get(error.exit_code, 400)
    return _json(failure_envelope(command, error), status)


def _guarded(command: str):
    """Turn a ResearchError into the failure envelope with a matching HTTP status."""

    def decorate(handler):
        @functools.wraps(handler)
        def wrapper(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            except ResearchError as error:
                return _failure(command, error)

        return wrapper

    return decorate


def _ok(
    command: str,
    data: dict,
    view: ProjectView | None = None,
    warnings: list[dict] | None = None,
    **meta,
) -> JSONResponse:
    full_meta = view.meta(**meta) if view is not None else meta
    result = CommandResult(data, warnings or [], full_meta)
    return _json(success_envelope(command, result))


class ResearchService:
    """Opens views for requests; refreshes the index at most every few seconds."""

    def __init__(
        self,
        *,
        run_app: bool,
        clock: Callable[[], datetime] | None = None,
        refresh_interval: float = REFRESH_INTERVAL_SECONDS,
    ):
        self.run_app = run_app
        self.clock = clock
        self.refresh_interval = refresh_interval
        self._lock = threading.Lock()
        self._last_refresh: dict[str, float] = {}

    @contextlib.contextmanager
    def view(self, project_id: str, refresh: bool = False) -> Iterator[ProjectView]:
        context = open_project(project_id)
        stats = self._refresh(context, force=refresh)
        view = open_view(context=context, refresh=False, clock=self.clock)
        if stats is not None:
            view.index.stats = stats
        try:
            yield view
        finally:
            view.close()

    def ensure_fresh(self, project_id: str, refresh: bool = False) -> None:
        self._refresh(open_project(project_id), force=refresh)

    def _refresh(self, context: ProjectContext, force: bool):
        key = context.entry.id
        with self._lock:
            due = time.monotonic() - self._last_refresh.get(key, float("-inf"))
            if not force and due < self.refresh_interval:
                return None
            stats = refresh_index(context)
            self._last_refresh[key] = time.monotonic()
            return stats


def _project_summary(entry) -> dict:
    row = {
        "id": entry.id,
        "name": entry.name,
        "workspaces": list(entry.workspaces),
        "vaultOk": True,
        "error": None,
    }
    try:
        revalidate(entry)
    except ResearchError as error:
        row["vaultOk"] = False
        row["error"] = {
            "code": error.code,
            "message": error.message,
            "remediation": error.remediation,
        }
    return row


def build_router(service: ResearchService) -> APIRouter:
    router = APIRouter()
    api = "/api/research"

    # ---- registry -------------------------------------------------------------------

    @router.get(f"{api}/projects")
    @_guarded("projects")
    def projects() -> JSONResponse:
        registry = load_registry()
        rows = [
            {
                **_project_summary(registry.projects[i]),
                "active": registry.active_project == i,
            }
            for i in sorted(registry.projects)
        ]
        data = {
            "projects": rows,
            "features": {"runApp": service.run_app},
        }
        return _ok("projects", data, registry=str(registry_path()))

    # ---- dashboard ------------------------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/home")
    @_guarded("home")
    def home(project_id: str, refresh: bool = False) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            data, warnings = build_home(view)
            snap = view.snapshot
            data["deviations"] = {
                "open": entities.list_deviations(view, status="open"),
                "total": len(snap.deviations),
            }
            data["decisions"]["layers"] = list(view.profile.decision_layers)
            return _ok("home", data, view, warnings)

    # ---- plans ----------------------------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/plans")
    @_guarded("plans")
    def plans(
        project_id: str,
        status: str | None = None,
        legacy: bool | None = None,
        include_archived: bool = False,
        refresh: bool = False,
    ) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            rows = plan_rows(
                view,
                status=status,
                legacy=legacy,
                include_archived=include_archived,
            )
            return _ok("plans", {"plans": rows}, view, total=len(rows))

    @router.get(f"{api}/projects/{{project_id}}/plans/{{plan_id}}")
    @_guarded("plan")
    def plan(project_id: str, plan_id: str, refresh: bool = False) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            page = entities.find_plan(view, plan_id)
            data = {
                "plan": entities.plan_core(view, page, False),
                "frontmatter": page.fm,
                "gates": entities.plan_gates(view, page),
                "html": render_markdown(page.body, view, run_app=service.run_app),
                "display": display_strings(view),
            }
            return _ok("plan", data, view)

    @router.get(f"{api}/projects/{{project_id}}/plans/{{plan_id}}/bundle")
    @_guarded("plan bundle")
    def bundle(
        project_id: str,
        plan_id: str,
        depth: int = 1,
        sections: str | None = None,
        include_body: bool = False,
        max_bytes: int | None = None,
        cursor: str | None = None,
        refresh: bool = False,
    ) -> JSONResponse:
        if depth not in (1, 2):
            raise ResearchError("USAGE_BAD_OPTION", "depth is 1 or 2", exit_code=2)
        chosen = parse_sections(sections)
        with service.view(project_id, refresh) as view:
            data, warnings = build_bundle(
                view,
                plan_id,
                depth=depth,
                include_body=include_body,
                sections=chosen,
            )
            total = byte_size(data)
            next_section = None
            if max_bytes is not None:  # the GUI wants it all; agents can bound it
                data, next_section = fit_sections(data, max_bytes, cursor)
            return _ok(
                "plan bundle",
                data,
                view,
                warnings,
                bytes=total,
                truncated=next_section is not None,
                nextCursor=next_section,
                sections=list(data),
            )

    # ---- decisions ------------------------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/decisions")
    @_guarded("decisions")
    def decisions(
        project_id: str,
        plan: str | None = None,
        status: str | None = None,
        layer: str | None = None,
        since: str | None = None,
        refresh: bool = False,
    ) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            rows = entities.list_decisions(
                view, plan=plan, status=status, layer=layer, since=since
            )
            data = {
                "decisions": rows,
                "layers": list(view.profile.decision_layers),
                "plans": [str(p.get("id")) for p in entities.plan_pages(view)],
            }
            return _ok("decisions", data, view, total=len(rows))

    @router.get(f"{api}/projects/{{project_id}}/decisions/{{ident}}")
    @_guarded("decision")
    def decision(project_id: str, ident: str, refresh: bool = False) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            data = entities.decision_detail(view, ident, include_body=True)
            item = data["decision"]
            resolver = LinkResolver(view, service.run_app)
            body = item.get("body")
            item["html"] = (
                render_markdown(body, view, run_app=service.run_app) if body else ""
            )
            for key in ("decisionText", "landsText"):
                if item.get(key):
                    item[key + "Html"] = render_markdown(
                        item[key], view, run_app=service.run_app
                    )
            for ref in data["refsIn"]:
                ref["href"] = resolver.note(ref["stem"]).href
            return _ok("decision", data, view)

    # ---- evidence -------------------------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/ev")
    @_guarded("ev")
    def ev_list(
        project_id: str,
        plan: str | None = None,
        status: str | None = None,
        label_state: str | None = None,
        refresh: bool = False,
    ) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            if plan:
                entities.find_plan(view, plan)
            rows = [
                entities.ev_item(view, n) for n in entities.notes_of(view, "evidence")
            ]
            rows.sort(key=lambda r: (str(r["plan"]), r["stem"]))
            rows = [
                r
                for r in rows
                if (not plan or r["plan"] == plan)
                and (not status or r["status"] == status)
                and (not label_state or r["labelState"] == label_state)
            ]
            return _ok(
                "ev",
                {"ev": rows, "display": display_strings(view)},
                view,
                total=len(rows),
            )

    @router.get(f"{api}/projects/{{project_id}}/ev/{{stem}}")
    @_guarded("ev show")
    def ev_show(project_id: str, stem: str, refresh: bool = False) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            data = entities.ev_detail(view, stem)
            note = entities.find_by_id(view, "evidence", stem)
            data["ev"]["html"] = render_markdown(
                note.body, view, run_app=service.run_app
            )
            data["display"] = display_strings(view)
            return _ok("ev show", data, view)

    # ---- todos, search, check, doctor ------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/todos")
    @_guarded("todos")
    def todos(
        project_id: str,
        owner: str | None = None,
        kind: str | None = None,
        plan: str | None = None,
        source: str = "all",
        include_done: bool = False,
        refresh: bool = False,
    ) -> JSONResponse:
        if source not in ("derived", "manual", "all"):
            raise ResearchError(
                "USAGE_BAD_OPTION", "source is derived, manual or all", exit_code=2
            )
        if owner not in (None, "user", "agent"):
            raise ResearchError(
                "USAGE_BAD_OPTION", "owner is user or agent", exit_code=2
            )
        with service.view(project_id, refresh) as view:
            items, warnings = all_todos(view, source=source, include_done=include_done)
            items = filter_todos(items, owner=owner, kind=kind, plan=plan)
            todo_file = view.profile.todo_file
            data = {
                "todos": items,
                "manualFile": {
                    "path": view.vault_rel(todo_file) if todo_file else None,
                    "sha256": view.file_sha256(todo_file) if todo_file else None,
                },
            }
            return _ok("todos", data, view, warnings, total=len(items))

    @router.get(f"{api}/projects/{{project_id}}/search")
    @_guarded("search")
    def search(
        project_id: str,
        q: str = "",
        type: str | None = None,  # noqa: A002 - query parameter name
        plan: str | None = None,
        status: str | None = None,
        images: bool = False,
        all_vault: bool = False,
        limit: int = 50,
        cursor: str | None = None,
        refresh: bool = False,
    ) -> JSONResponse:
        filters = SearchFilters(type, plan, status, None, None, images, all_vault)
        with service.view(project_id, refresh) as view:
            found = search_notes(
                view, q, filters, limit=max(1, min(limit, 200)), cursor=cursor
            )
            next_cursor = found.pop("_nextCursor")
            total = found.pop("total")
            resolver = LinkResolver(view, service.run_app)
            for hit in found["hits"]:
                hit["href"] = (
                    None if hit["type"] == "image" else resolver.note(hit["stem"]).href
                )
                prefix = f"{hit['path']}:{hit['line']}: "
                snippet = hit["snippet"]
                has_prefix = snippet.startswith(prefix)
                hit["text"] = snippet[len(prefix) :] if has_prefix else ""
            return _ok("search", found, view, total=total, nextCursor=next_cursor)

    @router.get(f"{api}/projects/{{project_id}}/check")
    @_guarded("check")
    def check(
        project_id: str,
        plan: str | None = None,
        level: str | None = None,
        code: str | None = None,
        refresh: bool = False,
    ) -> JSONResponse:
        levels = _parse_levels(level)
        with service.view(project_id, refresh) as view:
            result = run_checks(view.snapshot, plan)
            codes = {c for c in (code or "").split(",") if c}
            data, _ = check_data(result, levels=levels, codes=codes)
            return _ok("check", data, view)

    @router.get(f"{api}/projects/{{project_id}}/doctor")
    @_guarded("doctor")
    def doctor(
        project_id: str, fix_list: bool = False, refresh: bool = False
    ) -> JSONResponse:
        service.ensure_fresh(project_id, refresh)
        checks, meta = run_doctor(project_id, refresh=False)
        return _ok("doctor", doctor_data(checks, fix_list), None, None, **meta)

    # ---- notes and images ---------------------------------------------------------

    @router.get(f"{api}/projects/{{project_id}}/note/{{stem}}")
    @_guarded("note")
    def note(project_id: str, stem: str, refresh: bool = False) -> JSONResponse:
        with service.view(project_id, refresh) as view:
            resolver = LinkResolver(view, service.run_app)
            row = resolver.row(stem)
            if row is None:
                raise ResearchError(
                    "NOTE_NOT_FOUND",
                    f"no note named {stem!r}",
                    exit_code=3,
                    remediation="search for it from the project page",
                )
            stored = view.conn.execute(
                "SELECT fm_json, body FROM notes WHERE file_id = ?", (row["file_id"],)
            ).fetchone()
            outline = view.conn.execute(
                "SELECT level, text FROM headings WHERE file_id = ? AND level <= 3 "
                "ORDER BY line",
                (row["file_id"],),
            ).fetchall()
            inbound = view.conn.execute(
                "SELECT DISTINCT n.stem, n.type FROM links l "
                "JOIN notes n ON n.file_id = l.src_file_id "
                "WHERE l.resolved_file_id = ? ORDER BY n.stem LIMIT ?",
                (row["file_id"], BACKLINK_LIMIT),
            ).fetchall()
            target = resolver.note(row["stem"])
            data = {
                "note": {
                    "stem": row["stem"],
                    "path": row["rel"],
                    "type": row["type"],
                    "entityId": row["entity_id"],
                    "status": row["status"],
                    "title": row["title"],
                    "sha256": row["sha256"],
                    "frontmatter": json.loads(stored["fm_json"]),
                    "outline": [
                        {"level": r["level"], "text": r["text"]} for r in outline
                    ],
                },
                "html": render_markdown(stored["body"], view, run_app=service.run_app),
                "entityHref": target.href if target.kind != "note" else None,
                "backlinks": [
                    {
                        "stem": r["stem"],
                        "type": r["type"],
                        "href": resolver.note(r["stem"]).href,
                    }
                    for r in inbound
                ],
            }
            return _ok("note", data, view)

    @router.get(f"{api}/projects/{{project_id}}/image/{{name}}")
    def image(project_id: str, name: str, request: Request, refresh: bool = False):
        try:
            with service.view(project_id, refresh) as view:
                return _image_response(view, name, request)
        except ResearchError as error:
            return _failure("image", error)

    return router


def _not_found(message: str) -> ResearchError:
    return ResearchError("IMAGE_NOT_FOUND", message, exit_code=3)


def _image_response(view: ProjectView, name: str, request: Request) -> Response:
    """A vault image: resolved through the index, confined to the vault root."""
    if not name or "/" in name or "\\" in name or "\x00" in name or ".." in name:
        raise _not_found("not an image name")
    found = find_attachment(view, name)
    if found is None:
        raise _not_found(f"no image named {name!r}")
    rel, indexed_sha = found
    extension = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    if extension not in IMAGE_EXTS:
        raise ResearchError(
            "IMAGE_UNSUPPORTED_TYPE",
            f"{name!r} is not an image the viewer serves",
            exit_code=2,
        )
    root = view.context.resolved.vault_root.resolve()
    try:
        path = (root / rel).resolve(strict=True)
    except OSError:
        raise _not_found(f"image {name!r} is not readable") from None
    if not path.is_relative_to(root) or not path.is_file():
        raise _not_found(f"image {name!r} is not inside the vault")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ResearchError(
            "IMAGE_TOO_LARGE", f"{name!r} is larger than 25 MB", exit_code=2
        )
    payload = path.read_bytes()
    digest = indexed_sha or hashlib.sha256(payload).hexdigest()
    headers = {
        "Cache-Control": "private, max-age=60",
        "ETag": f'"{digest}"',
        "X-Content-Type-Options": "nosniff",
        # An SVG opened on its own must not run scripts in the GUI origin.
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(payload, media_type=IMAGE_TYPES[extension], headers=headers)


def build_pages_router() -> APIRouter:
    """Static GUI assets under `/research/` and the deep-link redirect."""
    router = APIRouter()
    assets = {p.name: p for p in STATIC_DIR.glob("*") if p.suffix in ASSET_TYPES}

    @router.get("/research")
    def research_redirect() -> RedirectResponse:
        return RedirectResponse("/research/")

    @router.get("/research/")
    def research_index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "research.html",
            media_type="text/html",
            headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"},
        )

    @router.get("/research/p/{rest:path}")
    def research_deep_link(rest: str) -> RedirectResponse:
        tail = "/".join(quote(part, safe="") for part in rest.split("/"))
        return RedirectResponse(f"/research/#/p/{tail}")

    @router.get("/research/{asset}")
    def research_asset(asset: str):
        path = assets.get(asset)
        if path is None:
            return Response("not found", status_code=404, headers=SECURITY_HEADERS)
        return FileResponse(
            path,
            media_type=ASSET_TYPES[path.suffix],
            headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"},
        )

    return router


def mount_research(
    app: FastAPI,
    *,
    run_app: bool,
    clock: Callable[[], datetime] | None = None,
) -> ResearchService:
    """Add the research GUI and API to `app` (reads the registry only)."""
    service = ResearchService(run_app=run_app, clock=clock)
    app.state.research = service
    app.include_router(build_pages_router())
    app.include_router(build_router(service))
    return service


def create_research_app(clock: Callable[[], datetime] | None = None) -> FastAPI:
    """An app with the research GUI only (no expnote workspace needed)."""
    app = FastAPI(title="expnote research", docs_url=None, redoc_url=None)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/research/")

    mount_research(app, run_app=False, clock=clock)
    return app
