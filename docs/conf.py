"""Sphinx configuration for the expnote documentation."""

import os
import tomllib
from pathlib import Path

metadata_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
with metadata_path.open("rb") as metadata_file:
    metadata = tomllib.load(metadata_file)["project"]

project = metadata["name"]
release = metadata["version"]
root_doc = "index"
extensions = ["myst_parser", "sphinx_copybutton", "sphinx_design"]
templates_path = ["_templates"]
myst_heading_anchors = 3
# Publish the tracked guides and tutorials. Local design notes live under design/.
exclude_patterns = ["_build", "README.md", "design"]
nitpicky = True

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["css/expnote.css"]
html_title = project
# Sphinx indexes Chinese words with jieba and stems English terms in this mode.
html_search_language = "zh"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
html_sidebars = {
    "**": ["expnote-sidebar-header.html", "expnote-sidebar-nav.html"],
}
html_theme_options = {
    "navbar_start": [],
    "navbar_center": [],
    "navbar_end": [],
    "navbar_persistent": [],
    "article_header_start": [],
    "secondary_sidebar_items": {"**": ["page-toc"], "index": [], "zh/index": []},
    "github_url": "https://github.com/JaimeParker/expnote",
    "primary_sidebar_end": [],
    "show_nav_level": 2,
    "navigation_depth": 3,
    "collapse_navigation": False,
}
html_show_sourcelink = False


def set_sidebar_context(app, pagename, templatename, context, doctree):
    """Resolve page-level language links before Sphinx renders the sidebar."""
    chinese = pagename.startswith("zh/")
    english_page = pagename.removeprefix("zh/")
    chinese_page = f"zh/{english_page}"
    pages = app.env.found_docs
    context.update(
        sidebar_home="zh/index" if chinese else "index",
        sidebar_english=english_page if english_page in pages else "index",
        sidebar_chinese=chinese_page if chinese_page in pages else "zh/index",
        sidebar_is_chinese=chinese,
        theme_search_bar_text="搜索文档…" if chinese else "Search docs…",
    )
    if chinese:
        context["language"] = "zh-CN"


def setup(app):
    app.connect("html-page-context", set_sidebar_context)
