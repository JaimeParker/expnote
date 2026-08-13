from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_fields_from_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") != 3:
        raise ValueError(
            "unsupported rl-garden config schema "
            f"(schema_version={config.get('schema_version')!r}, expected 3)"
        )

    inputs = config.get("inputs", {})
    derived = config.get("derived", {})
    selection = config.get("selection", {})
    run_id = str(derived.get("run_name") or inputs.get("exp_name") or "")
    if not run_id:
        raise ValueError(
            "rl-garden config has no derived.run_name or inputs.exp_name"
        )

    metadata = {
        "adapter": "rlgarden",
        "training_phase": str(selection.get("training_phase", "")),
        "algorithm": str(selection.get("algorithm", "")),
    }
    for key in [
        "env_id",
        "env_backend",
        "obs_mode",
        "control_mode",
        "seed",
        "log_dir",
        "num_offline_steps",
        "num_online_steps",
        "offline_dataset",
        "wandb_project",
        "wandb_group",
        "wandb_entity",
    ]:
        value = inputs.get(key)
        if value is not None:
            metadata[key] = str(value)

    return {
        "run_id": run_id,
        "purpose": "",
        "relation": "",
        "result": "",
        "metadata": metadata,
    }

