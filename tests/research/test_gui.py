"""The read-only research GUI: API, rendering, security and mounting."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from minivault import (
    EV_PATH,
    IMAGES_REL,
    build_vault,
    make_workspace,
    snapshot_tree,
    write,
)
from readkit import data, index_rows, register, run, vault_id
from test_checks_schema3 import decision_file

from expnote.research.web_routes import create_research_app, research_available
from expnote.web import create_app

GOLDEN = Path(__file__).parent / "golden" / "home.json"
FIXED_NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
P = "/api/research/projects/off2on"
RICH_NOTE = """---
type: theory
plan: "[[PLAN-900A--demo]]"
description: 富文本
---
# 富文本

链接 [[PLAN-900A--demo]]、别名 [[PLAN-900A--demo|演示计划]]、缺失 [[没有这个笔记]]。
决策 [[O2O-DEC-0125--批准]]，旧式 [[决策记录]] #2、#3 与 [[决策记录|总表]] #5。
运行 [[abcd1234]]，文档 [[doc00001]]，未知 [[zzzz9999]]。

![[o2o_demo.png|200]] ![[不存在.png]]

| 名称 | 链接 |
|---|---|
| 行一 | [[PLAN-900A--demo\\|表内别名]] |

> 引用 a<b

```
<script>fence()</script> [[PLAN-900A--demo]]
```
"""
XSS_NOTE = """---
type: theory
---
# 注入

<script>alert('x')</script>

<img src=x onerror=alert(1)>

<div onclick="evil()">块</div>

[坏链接](javascript:alert(1)) [好链接](https://example.org/a)
![远程](http://example.org/p.png)

行内 <b onmouseover=x>粗</b> 与 `<i>代码</i>`。
"""
TODO_FILE = (
    "---\ntype: todo-list\n---\n# 待办\n\n"
    "- [ ] 用户事项 [[别的笔记]] [id:: T-0007] [plan:: PLAN-900A]\n"
    "- [x] 已完成事项 [id:: T-0003]\n"
)


@pytest.fixture
def world(tmp_path):
    root, proj = build_vault(tmp_path)
    write(proj / "theory/理论.md", RICH_NOTE)
    write(proj / "theory/注入.md", XSS_NOTE)
    write(
        proj / "ops/执行日志.md",
        "---\ntype: execution-log\n---\n# 日志\n\n[[PLAN-900A--demo]]\n",
    )
    write(
        proj / "decisions/O2O-DEC-0125--批准.md", decision_file(125, status="proposed")
    )
    write(proj / "待办.md", TODO_FILE)
    make_workspace(
        "w1",
        runs=[
            {
                "id": "abcd1234",
                "topic": "主题",
                "status": "finished",
                "result": "终点 0.1",
            }
        ],
        docs=[
            {
                "id": "doc00001",
                "title": "分析一",
                "body": "# 分析\n\n结论。",
                "runs": ["abcd1234"],
            }
        ],
    )
    register(root, proj, "--workspace", "w1")
    return root, proj


def make_client(*, run_app: bool = False) -> TestClient:
    from expnote.research.web_routes import mount_research

    if run_app:
        from fastapi import FastAPI

        app = FastAPI()
        mount_research(app, run_app=True, clock=lambda: FIXED_NOW)
    else:
        app = create_research_app(clock=lambda: FIXED_NOW)
    app.state.research.refresh_interval = 0
    return TestClient(app)


@pytest.fixture
def client(world):
    return make_client()


def ok(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, (path, response.text[:300])
    body = response.json()
    assert body["ok"] is True and body["schemaVersion"] == 1
    return body


def note_html(client, stem):
    return ok(client, f"{P}/note/{quote(stem)}")["data"]["html"]


# ---- endpoints ---------------------------------------------------------------


def test_every_get_endpoint_answers_with_the_envelope_and_csp(client):
    paths = [
        "/api/research/projects",
        f"{P}/home",
        f"{P}/plans",
        f"{P}/plans/PLAN-900A",
        f"{P}/plans/PLAN-900A/bundle",
        f"{P}/decisions",
        f"{P}/decisions/O2O-DEC-0125",
        f"{P}/decisions/2",
        f"{P}/ev",
        f"{P}/ev/{quote('EV--PLAN-900A 演示')}",
        f"{P}/todos",
        f"{P}/search?q=PLAN-900A",
        f"{P}/check",
        f"{P}/doctor",
        f"{P}/note/{quote('理论')}",
        f"{P}/image/o2o_demo.png",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text[:200])
        assert "default-src" in response.headers["content-security-policy"], path
        if "/image/" not in path:
            assert response.headers["content-security-policy"] == (
                "default-src 'self'; img-src 'self' data:"
            )
            body = response.json()
            assert body["ok"], path
            if path != "/api/research/projects":
                assert body["meta"]["project"] == "off2on", path


def test_projects_lists_health_and_features(client):
    body = ok(client, "/api/research/projects")["data"]
    assert body["features"] == {"runApp": False}
    [project] = body["projects"]
    assert project["id"] == "off2on" and project["vaultOk"] and project["active"]
    assert project["workspaces"] == ["w1"]


def test_plans_endpoint_matches_the_cli(client, world):
    api = ok(client, f"{P}/plans")["data"]["plans"]
    cli = data("plan", "list")["plans"]
    assert api == cli
    closed = ok(client, f"{P}/plans", status="closed")["data"]["plans"]
    assert [p["id"] for p in closed] == ["PLAN-900A"]


def test_plan_page_payload_equals_plan_bundle_facts(client, world):
    shown = ok(client, f"{P}/plans/PLAN-900A")
    bundle = ok(client, f"{P}/plans/PLAN-900A/bundle")
    cli_bundle = run("plan", "bundle", "PLAN-900A")["data"]
    cli_show = data("plan", "show", "PLAN-900A")
    assert bundle["data"] == cli_bundle
    assert shown["data"]["plan"] == cli_show["plan"] == bundle["data"]["plan"]
    assert shown["data"]["gates"] == cli_show["gates"] == bundle["data"]["gates"]
    assert shown["data"]["frontmatter"] == cli_show["frontmatter"]
    assert "人写正文" in shown["data"]["html"]
    assert {"related", "analysisDocs", "runs", "execPlans", "evidence"} <= set(
        bundle["data"]
    )
    assert bundle["meta"]["truncated"] is False


def test_bundle_endpoint_can_be_bounded_and_sectioned(client):
    body = ok(client, f"{P}/plans/PLAN-900A/bundle", sections="plan,gates")
    assert list(body["data"]) == ["plan", "gates"]
    small = ok(client, f"{P}/plans/PLAN-900A/bundle", max_bytes=1000)
    assert small["meta"]["truncated"] is True and small["meta"]["nextCursor"]
    bad = client.get(f"{P}/plans/PLAN-900A/bundle", params={"depth": 3})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "USAGE_BAD_OPTION"


def test_decisions_filters_detail_and_refs(client):
    rows = ok(client, f"{P}/decisions")["data"]
    assert rows["layers"] == ["研究", "工程"] and "PLAN-900A" in rows["plans"]
    assert {d["number"] for d in rows["decisions"]} >= {2, 125}
    proposed = ok(client, f"{P}/decisions", status="proposed")["data"]["decisions"]
    assert [d["id"] for d in proposed] == ["O2O-DEC-0125"]
    detail = ok(client, f"{P}/decisions/2")["data"]
    assert detail["decision"]["number"] == 2 and detail["decision"]["decisionTextHtml"]
    refs = detail["refsIn"]
    assert refs and all("href" in r for r in refs)
    filed = ok(client, f"{P}/decisions/O2O-DEC-0125")["data"]["decision"]
    assert len(filed["sha256"]) == 64 and "<h2" in filed["html"]
    assert client.get(f"{P}/decisions/999").status_code == 404


def test_ev_endpoints_carry_sha_tables_and_figures(client):
    listing = ok(client, f"{P}/ev")["data"]
    assert (
        listing["ev"][0]["labelState"] == "filled" and "label_cn" in listing["display"]
    )
    stem = listing["ev"][0]["stem"]
    ev = ok(client, f"{P}/ev/{quote(stem)}")["data"]["ev"]
    assert len(ev["sha256"]) == 64 and ev["figures"] == ["o2o_demo.png"]
    assert ev["labelTable"]["rows"] and all(
        r["filled"] for r in ev["labelTable"]["rows"]
    )
    assert ev["labelTable"]["header"][0]
    assert ev["html"]
    assert ev["appendix"]["stem"].startswith("附表")
    assert client.get(f"{P}/ev/nope").status_code == 404


def test_ev_table_cells_render_inline_markdown_without_raw_html(world):
    root, proj = world
    path = proj / EV_PATH
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "| orig | 0.1 |",
            "| orig | `x=1`<br>**粗** [[PLAN-900A--demo\\|计划]] "
            "<script>alert(1)</script> |",
        ),
        encoding="utf-8",
    )
    ev = ok(make_client(), f"{P}/ev/{quote('EV--PLAN-900A 演示')}")["data"]["ev"]
    cell = ev["endpointTable"]["rowsHtml"][0][1]
    assert "<code>x=1</code><br>" in cell and "<strong>粗</strong>" in cell
    assert 'href="#/p/off2on/plan/PLAN-900A"' in cell
    assert "<script" not in cell and "&lt;script" in cell
    assert ev["endpointTable"]["rows"][0][1].startswith("`x=1`")  # raw text stays
    values = ev["labelTable"]["rows"][0]
    assert len(values["valuesHtml"]) == len(values["values"]) and values["keyHtml"]


def test_todos_search_check_doctor_note(client, world):
    todos = ok(client, f"{P}/todos")["data"]
    kinds = {t["kind"] for t in todos["todos"]}
    assert {"decision-proposed", "manual"} <= kinds
    assert todos["todos"][0]["owner"] == "user"
    assert todos["manualFile"]["path"].endswith("待办.md")
    assert len(todos["manualFile"]["sha256"]) == 64
    assert client.get(f"{P}/todos", params={"source": "x"}).status_code == 422
    found = ok(client, f"{P}/search", q="PLAN-900A", type="plan")
    hit = found["data"]["hits"][0]
    assert hit["href"] == "#/p/off2on/plan/PLAN-900A" and "PLAN-900A" in hit["text"]
    assert found["data"]["matchMode"] == "fts"
    assert ok(client, f"{P}/search", q="演")["data"]["matchMode"] == "like"
    assert client.get(f"{P}/search").status_code == 422
    check = ok(client, f"{P}/check", level="FAIL,WARN")["data"]
    assert check["summary"] == run("check")["data"]["summary"]
    assert all(f["level"] in ("FAIL", "WARN") for f in check["findings"])
    doctor = ok(client, f"{P}/doctor")["data"]
    assert {c["id"] for c in doctor["checks"]} >= {"index", "untyped-plans"}
    note = ok(client, f"{P}/note/{quote('理论')}")["data"]
    assert note["note"]["type"] == "theory" and note["note"]["outline"]
    assert note["backlinks"] == [] and note["entityHref"] is None
    plan_note = ok(client, f"{P}/note/{quote('PLAN-900A--demo')}")["data"]
    assert plan_note["entityHref"] == "#/p/off2on/plan/PLAN-900A"
    assert {b["stem"] for b in plan_note["backlinks"]} >= {"理论", "执行日志"}
    assert client.get(f"{P}/note/nothing-here").status_code == 404


def test_errors_use_the_failure_envelope_with_remediation(client, world):
    missing = client.get("/api/research/projects/nope/home")
    body = missing.json()
    assert missing.status_code == 404 and body["ok"] is False
    assert body["error"]["code"] == "PROJECT_NOT_FOUND" and body["error"]["remediation"]
    plan = client.get(f"{P}/plans/PLAN-NOPE")
    assert plan.status_code == 404 and plan.json()["error"]["code"] == "PLAN_NOT_FOUND"
    root, proj = world
    write(proj / "plans/PLAN-900A--zweite.md", "---\ntype: plan\nid: PLAN-900A\n---\n")
    ambiguous = client.get(f"{P}/plans/PLAN-900A", params={"refresh": True})
    assert ambiguous.status_code == 409
    assert ambiguous.json()["error"]["code"] == "AMBIGUOUS_ID"


def test_vault_unavailable_is_a_503_envelope(world, tmp_path):
    root, _ = world
    client = make_client()
    root.rename(tmp_path / "moved")
    response = client.get(f"{P}/home")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VAULT_UNAVAILABLE"
    listing = ok(client, "/api/research/projects")["data"]["projects"][0]
    assert listing["vaultOk"] is False and listing["error"]["remediation"]


# ---- golden dashboard --------------------------------------------------------


def normalized_home(body: dict) -> dict:
    data = json.loads(json.dumps(body["data"]))
    for volatile in ("builtAt", "path", "ageSeconds", "filesChanged", "refreshedNow"):
        data["index"].pop(volatile, None)
    summary = data["doctor"]["summary"]  # "recently modified" flips ok <-> info
    summary["ok"] += summary.pop("info")
    return data


def test_home_payload_matches_the_golden_file(client):
    actual = normalized_home(ok(client, f"{P}/home"))
    if os.environ.get("EXPNOTE_UPDATE_GOLDEN"):
        text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True)
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "run once with EXPNOTE_UPDATE_GOLDEN=1 to create it"
    assert actual == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_home_covers_the_dashboard_contract(client):
    home = ok(client, f"{P}/home")["data"]
    assert home["plans"]["total"] == 2 and "closed" in home["plans"]["byStatus"]
    assert [d["id"] for d in home["decisions"]["awaiting"]] == ["O2O-DEC-0125"]
    assert home["todos"]["items"][0]["owner"] == "user"
    assert home["todos"]["manual"] == 1 and home["todos"]["derived"] >= 1
    assert home["runs"]["workspaces"] == ["w1"]
    assert [r["id"] for r in home["runs"]["recent"]] == ["abcd1234"]
    assert home["index"]["built"] and home["doctor"]["flags"] == []
    cli = run("check")["data"]
    for level in ("fail", "warn", "info"):
        assert home["findings"][level] == (
            cli["summary"][level] + cli["newCodesSummary"][level]
        )
    assert home["deviations"]["open"][0]["id"] == "D-02"
    assert home["display"]["label_cn"]["filled"]


# ---- rendering ---------------------------------------------------------------


def test_wikilinks_embeds_run_links_and_hub_refs(world):
    client = make_client()
    html = note_html(client, "理论")
    assert (
        '<a class="wl plan" href="#/p/off2on/plan/PLAN-900A">PLAN-900A--demo</a>'
        in html
    )
    assert ">演示计划</a>" in html and ">表内别名</a>" in html
    assert 'href="#/p/off2on/decision/O2O-DEC-0125"' in html
    assert (
        '<span class="wl missing" title="unresolved link">没有这个笔记</span>' in html
    )
    assert 'href="#/p/off2on/decisions">决策记录</a>' in html
    for number in ("2", "3", "5"):
        assert f'href="#/p/off2on/decision/{number}">#{number}</a>' in html
    assert ">总表</a>" in html
    assert 'src="/api/research/projects/off2on/image/o2o_demo.png"' in html
    assert (
        'width="200"' in html and 'class="wl missing" title="image not found"' in html
    )
    assert '<span class="wl run-plain">abcd1234</span>' in html  # no run app here
    assert '<span class="wl doc-plain">doc00001</span>' in html
    assert ">zzzz9999</span>" in html and "wl missing" in html
    assert "<th>名称</th>" in html or "<th>名称" in html
    assert "<blockquote>" in html and "a&lt;b" in html
    assert "&lt;script&gt;fence()&lt;/script&gt; [[PLAN-900A--demo]]" in html
    assert "<h1" in html and "frontmatter" not in html and "type: theory" not in html


def test_run_and_doc_links_go_to_the_run_app_when_present(world):
    html = note_html(make_client(run_app=True), "理论")
    assert '<a class="wl run" href="/#/run/abcd1234">abcd1234</a>' in html
    assert '<a class="wl doc" href="/#/doc/doc00001">doc00001</a>' in html


def test_raw_html_is_neutralised(world):
    client = make_client()
    html = note_html(client, "注入")
    for needle in (
        "<script",
        "<img",
        "<div",
        "<b ",
        "onerror=alert(1)>",
        'href="javascript',
    ):
        assert needle not in html, needle
    assert (
        "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
        or "&lt;script&gt;" in html
    )
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'class="wl missing" title="link target is not allowed here">坏链接' in html
    assert (
        'href="https://example.org/a" rel="noopener noreferrer" target="_blank"' in html
    )
    assert 'href="http://example.org/p.png"' in html and "<img" not in html
    assert "<code>&lt;i&gt;代码&lt;/i&gt;</code>" in html
    assert " style=" not in html


def test_plan_and_decision_bodies_are_escaped_too(world):
    root, proj = world
    write(
        proj / "plans/PLAN-900B--other.md",
        "---\ntype: plan\nid: PLAN-900B\n---\n# 计划\n\n<script>alert(1)</script>\n",
    )
    client = make_client()
    plan = ok(client, f"{P}/plans/PLAN-900B", refresh=True)["data"]["html"]
    assert "<script" not in plan and "&lt;script&gt;" in plan


# ---- image endpoint ----------------------------------------------------------


def test_image_endpoint_serves_cached_vault_images(client):
    response = client.get(f"{P}/image/o2o_demo.png")
    assert response.status_code == 200 and response.content == b"png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=60"
    assert response.headers["x-content-type-options"] == "nosniff"
    etag = response.headers["etag"]
    assert re.fullmatch(r'"[0-9a-f]{64}"', etag)
    again = client.get(f"{P}/image/o2o_demo.png", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_image_endpoint_confines_requests_to_indexed_vault_images(world, tmp_path):
    root, proj = world
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"secret")
    (root / IMAGES_REL / "o2o_link.png").symlink_to(outside / "secret.png")
    write(root / "40 Resources/note.txt", "text attachment")
    write(root / "40 Resources/paper.pdf", "pdf")
    write(root / "40 Resources/vector.svg", "<svg/>")
    client = make_client()
    ok(client, f"{P}/home", refresh=True)  # index the new files
    indexed = index_rows(
        vault_id(), "SELECT rel FROM files WHERE rel LIKE ?", "%o2o_link.png"
    )
    assert indexed, "the symlink must be indexed or this case tests nothing"
    cases = {
        "../../etc/passwd": {404},
        "..%2F..%2Fetc%2Fpasswd": {404},
        "%2e%2e%2fsecret.png": {404},
        "%2Fetc%2Fpasswd": {404},
        "..\\secret.png": {404},
        "..": {404},
        "o2o_link.png": {404},  # symlink leaving the vault
        "note.txt": {415},
        "paper.pdf": {415},
        "理论.md": {404},  # a note, not an attachment
        "missing.png": {404},
    }
    for name, expected in cases.items():
        response = client.get(f"{P}/image/{name}")
        assert response.status_code in expected, (name, response.status_code)
        assert response.status_code != 200
    svg = client.get(f"{P}/image/vector.svg")
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in svg.headers["content-security-policy"]


# ---- static pages ------------------------------------------------------------


def test_pages_and_assets_are_served_with_csp(client):
    page = client.get("/research/")
    assert page.status_code == 200 and "research.js" in page.text
    assert page.headers["content-security-policy"] == (
        "default-src 'self'; img-src 'self' data:"
    )
    assert client.get("/research", follow_redirects=False).status_code == 307
    for asset, kind in (("research.js", "javascript"), ("research.css", "css")):
        response = client.get(f"/research/{asset}")
        assert response.status_code == 200 and kind in response.headers["content-type"]
        assert "default-src" in response.headers["content-security-policy"]
    assert client.get("/research/nope.js").status_code == 404
    assert client.get("/research/../pyproject.toml").status_code == 404
    deep = client.get("/research/p/off2on/plan/PLAN-900A", follow_redirects=False)
    assert deep.status_code == 307
    assert deep.headers["location"] == "/research/#/p/off2on/plan/PLAN-900A"
    root = client.get("/", follow_redirects=False)
    assert root.headers["location"] == "/research/"


def test_static_assets_have_no_external_or_inline_code():
    static = Path(__file__).parents[2] / "expnote/research/static"
    for path in static.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!www\.w3\.org)", text), path.name
        assert "unpkg" not in text and "googleapis" not in text, path.name
        if path.suffix == ".html":
            assert not re.search(r"<script(?![^>]*\bsrc=)", text)
            assert " style=" not in text and "onclick=" not in text
        if path.suffix == ".js":
            assert 'setAttribute("style"' not in text and "style:" not in text
            assert not re.search(r"<[a-z][^>]* style=", text)


# ---- read-only guarantee -----------------------------------------------------


def test_no_endpoint_changes_a_file_in_the_vault(world):
    root, proj = world
    client = make_client()
    before = snapshot_tree(root)
    paths = [
        "/research/",
        "/api/research/projects",
        f"{P}/home",
        f"{P}/plans",
        f"{P}/plans/PLAN-900A",
        f"{P}/plans/PLAN-900A/bundle?depth=2&include_body=true",
        f"{P}/decisions",
        f"{P}/decisions/2",
        f"{P}/decisions/O2O-DEC-0125",
        f"{P}/ev",
        f"{P}/ev/{quote('EV--PLAN-900A 演示')}",
        f"{P}/todos",
        f"{P}/search?q=PLAN",
        f"{P}/search?q=a&images=true",
        f"{P}/check",
        f"{P}/doctor?refresh=true",
        f"{P}/note/{quote('理论')}",
        f"{P}/image/o2o_demo.png",
        f"{P}/home?refresh=true",
    ]
    for path in paths:
        assert client.get(path).status_code == 200, path
    for method in ("post", "put", "patch", "delete"):
        for path in (f"{P}/decisions/2", f"{P}/todos", f"{P}/ev/x/labels"):
            assert getattr(client, method)(path).status_code in (404, 405), (
                method,
                path,
            )
    assert snapshot_tree(root) == before


# ---- mounting ----------------------------------------------------------------


def has_run_app(app) -> bool:
    """The run SPA lives at `/`; the research-only app redirects it."""
    return TestClient(app).get("/", follow_redirects=False).status_code == 200


def serves(app, path: str) -> bool:
    """True when the app has a route for the GET path (any status but 404)."""
    return TestClient(app).get(path).status_code != 404


def test_create_app_mounts_research_only_when_projects_exist(tmp_path, world):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    app = create_app(workspace)
    assert serves(app, "/research/") and has_run_app(app)
    plain = create_app(workspace, research=False)
    assert not serves(plain, "/research/") and has_run_app(plain)
    assert 'href="/research/"' in TestClient(app).get("/").text
    assert 'href="/research/"' not in TestClient(plain).get("/").text


def test_create_app_without_registered_projects_has_no_research(tmp_path):
    assert research_available() is False
    app = create_app(tmp_path)
    assert not serves(app, "/research/")
    assert "<!--research-nav-->" not in TestClient(app).get("/").text


def test_web_command_serves_research_only_without_a_workspace(world, monkeypatch):
    from typer.testing import CliRunner

    from expnote.cli import app as cli_app

    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: served.update(app=app, **kw))
    result = CliRunner().invoke(cli_app, ["web", "--no-open", "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert "/research/" in result.output
    only = served["app"]
    assert serves(only, "/research/") and serves(only, "/api/research/projects")
    assert not has_run_app(only) and served["port"] == 8123
    served.clear()
    refused = CliRunner().invoke(cli_app, ["web", "--no-open", "--no-research"])
    assert refused.exit_code != 0 and not served  # nothing to serve at all


def test_web_command_serves_both_or_disables_research(world, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from expnote.cli import app as cli_app

    workspace = tmp_path / "ws-dir"
    assert (
        CliRunner()
        .invoke(cli_app, ["init", "--workspace-dir", str(workspace)])
        .exit_code
        == 0
    )
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: served.update(app=app))
    both = CliRunner().invoke(
        cli_app, ["web", "--no-open", "--workspace-dir", str(workspace)]
    )
    assert both.exit_code == 0, both.output
    assert serves(served["app"], "/research/") and has_run_app(served["app"])
    off = CliRunner().invoke(
        cli_app,
        ["web", "--no-open", "--no-research", "--workspace-dir", str(workspace)],
    )
    assert off.exit_code == 0, off.output
    assert has_run_app(served["app"]) and not serves(served["app"], "/research/")
