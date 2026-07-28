from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_fields_from_config(config: dict[str, Any]) -> dict[str, Any]:
    args = config.get("args", {})
    run_id = str(config.get("run_name") or args.get("exp_name") or "")
    if not run_id:
        raise ValueError("rl-garden config has no run_name or args.exp_name")

    metadata = {
        "adapter": "rlgarden",
        "training_phase": str(config.get("training_phase", "")),
        "algorithm": str(config.get("algorithm", "")),
    }
    for key in [
        "env_id",
        "env_backend",
        "obs_mode",
        "control_mode",
        "seed",
        "log_dir",
        "total_timesteps",
        "num_offline_steps",
        "offline_dataset_path",
        "wandb_project",
        "wandb_group",
    ]:
        value = args.get(key)
        if value is not None:
            metadata[key] = str(value)

    return {
        "run_id": run_id,
        "purpose": "",
        "relation": "",
        "result": "",
        "metadata": metadata,
    }

