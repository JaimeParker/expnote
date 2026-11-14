# Build and publish the docs

Run from the repository root with Python 3.12, the version used by Read the Docs.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r docs/requirements.txt
python -m sphinx -W -n --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. The English and Simplified Chinese tutorials
use shared snippets under `docs/examples/`. Both sets of pages should be updated
when steps or expected results change. Install `".[research]"` to run the CLI
examples locally. Sphinx only needs the packages in `docs/requirements.txt`.

## Publish on Read the Docs

1. Push the documentation changes to the GitHub repository's default branch.
2. Sign in to [Read the Docs Community](https://app.readthedocs.org/), connect
   GitHub, and add the `JaimeParker/expnote` project.
3. Open **Builds**. Check the `latest` build for `main`, or start it from the
   dashboard if needed. The repository's `.readthedocs.yaml` selects
   `docs/conf.py` and makes Sphinx warnings fail the build.
4. Open the published homepage from the dashboard. Follow **简体中文** to the
   Chinese landing page and check that each tutorial links to its counterpart.
   Both languages are published by the same project.

See [Adding a documentation project](https://docs.readthedocs.com/platform/stable/intro/add-project.html)
and the [configuration reference](https://docs.readthedocs.com/platform/stable/config-file/v2.html).
