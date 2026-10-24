"""The vendored KaTeX files are served under /research/katex/ with an allowlist."""

from __future__ import annotations

from test_gui import make_client


def test_katex_module_and_font_are_served():
    client = make_client()
    module = client.get("/research/katex/katex.mjs")
    assert module.status_code == 200
    assert module.headers["content-type"].startswith("text/javascript")
    assert module.headers["content-security-policy"] == (
        "default-src 'self'; img-src 'self' data:"
    )
    font = client.get("/research/katex/fonts/KaTeX_Main-Regular.woff2")
    assert font.status_code == 200
    assert font.headers["content-type"] == "font/woff2"
    css = client.get("/research/katex/katex.min.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_katex_paths_outside_the_allowlist_are_not_found():
    client = make_client()
    assert client.get("/research/katex/../web_routes.py").status_code == 404
    assert client.get("/research/katex/nope.js").status_code == 404
    assert client.get("/research/katex/LICENSE").status_code == 404
    woff = "/research/katex/fonts/KaTeX_Main-Regular.woff"
    assert client.get(woff).status_code == 404
