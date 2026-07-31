from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from expnote.cli import app

runner = CliRunner()


def _write_config(tmp_path: Path, config: dict[str, object]) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _init_with_topic(tmp_path: Path) -> None:
    assert (
        runner.invoke(
            app, ["init", "--workspace-dir", str(tmp_path / ".expnote")]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            ["topic", "add", "topic", "--workspace-dir", str(tmp_path / ".expnote")],
        ).exit_code
        == 0
    )


def _base_config() -> dict[str, object]:
    return {
        "training_phase": "online",
        "algorithm": "sac",
        "run_name": "StackCube-v1__sac__1__123",
        "args": {
            "env_id": "StackCube-v1",
            "env_backend": "maniskill",
            "obs_mode": "rgbd",
            "seed": 1,
            "log_dir": "runs",
            "wandb_project": "rl-garden",
        },
    }


def test_import_rlgarden_config(tmp_path):
    config_path = _write_config(tmp_path, _base_config())
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "import",
            "rlgarden",
            str(config_path),
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "StackCube-v1__sac__1__123"
    assert data["metadata"]["adapter"] == "rlgarden"
    assert data["metadata"]["env_id"] == "StackCube-v1"


def test_import_rlgarden_uses_exp_name_when_run_name_is_missing(tmp_path):
    config = _base_config()
    config.pop("run_name")
    args = dict(config["args"])
    args["exp_name"] = "fallback-exp"
    config["args"] = args
    config_path = _write_config(tmp_path, config)
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "import",
            "rlgarden",
            str(config_path),
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "fallback-exp"


def test_import_rlgarden_errors_when_no_run_identifier_exists(tmp_path):
    config = _base_config()
    config.pop("run_name")
    config_path = _write_config(tmp_path, config)
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "import",
            "rlgarden",
            str(config_path),
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_import_rlgarden_purpose_override(tmp_path):
    config_path = _write_config(tmp_path, _base_config())
    _init_with_topic(tmp_path)

    result = runner.invoke(
        app,
        [
            "import",
            "rlgarden",
            str(config_path),
            "--workspace-dir",
            str(tmp_path / ".expnote"),
            "--topic",
            "topic",
            "--purpose",
            "manual purpose",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["purpose"] == "manual purpose"


def test_duplicate_rlgarden_import_errors_and_does_not_append_import_event(tmp_path):
    config_path = _write_config(tmp_path, _base_config())
    _init_with_topic(tmp_path)

    args = [
        "import",
        "rlgarden",
        str(config_path),
        "--workspace-dir",
        str(tmp_path / ".expnote"),
        "--topic",
        "topic",
        "--json",
    ]
    assert runner.invoke(app, args).exit_code == 0
    before = (tmp_path / ".expnote" / "events.jsonl").read_text(encoding="utf-8")

    result = runner.invoke(app, args)
    assert result.exit_code != 0
    after = (tmp_path / ".expnote" / "events.jsonl").read_text(encoding="utf-8")
    assert after == before
