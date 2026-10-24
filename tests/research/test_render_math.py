"""LaTeX math is kept away from Markdown and handed to the client as escaped TeX."""

from __future__ import annotations

import pytest
from minivault import build_vault, make_context

from expnote.research.render import MATH_RE, render_inline_cells, render_markdown
from expnote.research.view import open_view


@pytest.fixture
def view(tmp_path):
    root, proj = build_vault(tmp_path)
    with open_view(context=make_context(root, proj)) as view:
        yield view


def inline(tex: str) -> str:
    return f'<span class="math" data-display="0">{tex}</span>'


def display(tex: str) -> str:
    return f'<span class="math" data-display="1">{tex}</span>'


def test_inline_math_survives_markdown_syntax(view):
    html = render_markdown(
        r"甲 $a*b*c$ 乙 $\{x\} \\ y$ 丙 $[a](b)$ 丁 $a<b$ 戊 $x_{a} + y_{b}$", view
    )
    assert inline("a*b*c") in html and "<em>" not in html
    assert inline(r"\{x\} \\ y") in html
    assert inline("[a](b)") in html and "<a " not in html
    assert inline("a&lt;b") in html
    assert inline("x_{a} + y_{b}") in html


def test_cjk_next_to_dollars_is_math(view):
    html = render_markdown(r"用 离线$Q_{off}$、在线 $Q_t$，按照 $\gamma$ 计算", view)
    assert inline("Q_{off}") in html and inline("Q_t") in html
    assert inline(r"\gamma") in html


@pytest.mark.parametrize(
    "text",
    [
        "set $OUT and $PATH first",
        "costs $5 and $6 later",
        "it is $x $ with a space",
        "it is $ x$ with a space",
        r"a literal \$5 here",
        "`echo $HOME$` in code",
        "spans\n\n$a\n\nb$ a blank line",
    ],
)
def test_dollars_that_are_not_math(view, text):
    assert 'class="math"' not in render_markdown(text, view)


def test_escaped_dollar_is_a_plain_dollar(view):
    html = render_markdown(r"price \$5 and $x$", view)
    assert "price $5 and" in html and inline("x") in html


def test_math_in_fenced_code_is_left_alone(view):
    html = render_markdown("```\n$a*b$\n```\n", view)
    assert 'class="math"' not in html and "$a*b$" in html


def test_display_blocks_on_own_lines_and_inline(view):
    html = render_markdown(
        "前文\n\n$$\n\\boxed{a} \\\\\nb*c*d\n$$\n\n- quote: \"$$\\text{x}$$ 后\"\n",
        view,
    )
    assert display("\\boxed{a} \\\\\nb*c*d") in html and "<em>" not in html
    assert display(r"\text{x}") in html and "<li>" in html


def test_escaped_pipe_in_a_table_row_is_a_pipe(view):
    html = render_markdown("| h | k |\n|---|---|\n| $\\|\\rho\\|$ | $a$ |\n", view)
    assert inline(r"|\rho|") in html and inline("a") in html
    assert html.count("<td>") == 2


def test_unescaped_pipe_splits_the_table_row_like_obsidian(view):
    html = render_markdown("| h | k |\n|---|---|\n| $a | b$ |\n", view)
    assert 'class="math"' not in html and html.count("<td>") == 2


def test_escaped_pipe_outside_tables_is_a_norm(view):
    assert inline(r"\|x\|") in render_markdown(r"norm $\|x\|$", view)


def test_table_cells_render_math(view):
    [cell] = render_inline_cells([r"$k_{\text{rec}}$<br>$C\|D$ **粗**"], view)
    assert inline(r"k_{\text{rec}}") + "<br>" + inline("C|D") in cell
    assert "<strong>粗</strong>" in cell


def test_tex_cannot_inject_markup(view):
    html = render_markdown(r'$<script>alert(1)</script>" onload="x$', view)
    assert "<script" not in html
    assert inline("&lt;script&gt;alert(1)&lt;/script&gt;&quot; onload=&quot;x") in html


def test_a_long_backtick_run_does_not_backtrack():
    # Python-Markdown's own backtick pattern is slow on such runs; this pins ours.
    import time

    start = time.monotonic()
    MATH_RE.sub("", "`" * 20000 + "\n\n" + "`a` $x$ " * 2000)
    assert time.monotonic() - start < 1


def test_display_math_in_a_blockquote_drops_the_markers(view):
    html = render_markdown("> [!note]\n> $$\n> a+b\n> > c\n> $$\n", view)
    assert display("a+b\nc") in html


def test_escaped_backslash_before_math_and_empty_display(view):
    html = render_markdown(r"x \\$a$ and $$ $$ end", view)
    assert inline("a") in html and "$$ $$" in html
