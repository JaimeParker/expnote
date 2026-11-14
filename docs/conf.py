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
extensions = ["myst_parser", "sphinx_copybutton"]
myst_heading_anchors = 3
# Publish the tracked guides and tutorials. Local design notes live under design/.
exclude_patterns = ["_build", "README.md", "design"]
nitpicky = True

html_theme = "pydata_sphinx_theme"
html_title = project
# Sphinx indexes Chinese words with jieba and stems English terms in this mode.
html_search_language = "zh"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
html_theme_options = {"github_url": "https://github.com/JaimeParker/expnote"}
html_show_sourcelink = False


def set_page_language(app, pagename, templatename, context, doctree):
    """Mark the Chinese pages for browsers and assistive technology."""
    if pagename.startswith("zh/"):
        context["language"] = "zh-CN"


def setup(app):
    app.connect("html-page-context", set_page_language)
