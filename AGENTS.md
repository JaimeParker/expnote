# expnote Agent Instructions

## Project Shape

- `expnote` is a generic experiment-record CLI.
- SQLite is the source of truth.
- Markdown is a generated human-facing projection.
- Framework integrations such as `rl-garden`, W&B, MLflow, and Obsidian-specific
  behavior belong in adapters or projection code, not in the core schema by
  default.

## Development Rules

- Make surgical changes that trace directly to the requested behavior.
- Prefer the smallest implementation that passes focused tests.
- Do not add future-facing abstractions, configuration, or integrations unless
  they are required by the task.
- Preserve user-authored Markdown outside expnote managed blocks.
- Keep command output deterministic, especially for `--json`.
- Mutating CLI commands should write audit events to `.expnote/events.jsonl`.

## Testing

- Add or update tests before changing behavior when fixing bugs or expanding CLI
  guarantees.
- Use `CliRunner` tests for CLI behavior and temporary directories for workspaces.
- Verify soft-deleted records are hidden from normal list/query/projection flows.
- Run focused tests while iterating, then run the full suite:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

Run lint before handoff:

```bash
RUFF_CACHE_DIR=/tmp/expnote-ruff-cache ruff check .
```
