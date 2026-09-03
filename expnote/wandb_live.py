from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class WandbRunRef:
    entity: str
    project: str
    run_id: str

    @property
    def path(self) -> str:
        return f"{self.entity}/{self.project}/{self.run_id}"


class WandbLiveError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def parse_wandb_run_url(url: str) -> WandbRunRef:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise WandbLiveError("invalid_url", "wandb_url must be an HTTP URL.")
    host = parsed.netloc.lower()
    if host not in {"wandb.ai", "www.wandb.ai", "app.wandb.ai"}:
        raise WandbLiveError("invalid_url", "wandb_url must point to wandb.ai.")

    parts = [part for part in parsed.path.split("/") if part]
    try:
        runs_index = parts.index("runs")
    except ValueError as exc:
        raise WandbLiveError("invalid_url", "wandb_url is not a W&B run URL.") from exc

    if runs_index < 2 or runs_index + 1 >= len(parts):
        raise WandbLiveError(
            "invalid_url",
            "wandb_url is missing entity, project, or run id.",
        )
    return WandbRunRef(
        entity=parts[runs_index - 2],
        project=parts[runs_index - 1],
        run_id=parts[runs_index + 1],
    )


def fetch_live_wandb_charts(url: str, *, samples: int = 1000) -> dict[str, Any]:
    ref = parse_wandb_run_url(url)
    try:
        import wandb
    except ImportError as exc:
        raise WandbLiveError(
            "wandb_not_installed",
            "The wandb Python package is not installed in this environment.",
        ) from exc

    try:
        run = wandb.Api().run(ref.path)
        rows = run.history(samples=samples, pandas=False, stream="default")
    except Exception as exc:
        raise WandbLiveError(
            "wandb_api_error",
            str(exc) or exc.__class__.__name__,
        ) from exc

    groups = group_wandb_history(rows)
    return {
        "available": True,
        "cached": False,
        "run_path": ref.path,
        "samples": samples,
        "groups": groups,
    }


def fetch_wandb_run_state(url: str) -> str:
    ref = parse_wandb_run_url(url)
    try:
        import wandb
    except ImportError as exc:
        raise WandbLiveError(
            "wandb_not_installed",
            "The wandb Python package is not installed in this environment.",
        ) from exc

    try:
        return wandb.Api().run(ref.path).state
    except Exception as exc:
        raise WandbLiveError(
            "wandb_api_error",
            str(exc) or exc.__class__.__name__,
        ) from exc


def map_wandb_state_to_status(state: str) -> str:
    if state == "finished":
        return "finished"
    if state in {"running", "preempting"}:
        return "running"
    if state in {"crashed", "failed", "killed"}:
        return "failed"
    return state


def load_cached_wandb_chart(cache_dir: Path, run_id: str) -> dict[str, Any] | None:
    cache_path = _cache_path(cache_dir, run_id)
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["cached"] = True
    return data


def fetch_wandb_charts(
    url: str,
    *,
    run_id: str,
    status: str,
    cache_dir: Path,
    samples: int = 1000,
    force: bool = False,
) -> dict[str, Any]:
    cacheable = status == "finished"
    if not force:
        cached = load_cached_wandb_chart(cache_dir, run_id) if cacheable else None
        if cached is not None:
            return cached

    data = fetch_live_wandb_charts(url, samples=samples)
    data["cached"] = False
    if cacheable:
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            **data,
            "cached_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "source_run_id": run_id,
        }
        _cache_path(cache_dir, run_id).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    return data


def wandb_cache_stats(cache_dir: Path) -> dict[str, int]:
    if not cache_dir.exists():
        return {"files": 0, "bytes": 0}
    files = [path for path in cache_dir.glob("*.json") if path.is_file()]
    return {
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def clear_wandb_cache(cache_dir: Path) -> dict[str, int]:
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    return wandb_cache_stats(cache_dir)


def group_wandb_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    series: dict[str, dict[str, list[float]]] = {}
    for index, row in enumerate(rows):
        x_value = _number(row.get("_step"))
        if x_value is None:
            x_value = float(index)
        for key, value in row.items():
            if not _is_metric_key(key):
                continue
            y_value = _number(value)
            if y_value is None:
                continue
            metric = series.setdefault(key, {"x": [], "y": []})
            metric["x"].append(x_value)
            metric["y"].append(y_value)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for metric_name in sorted(series):
        group_name = _metric_group(metric_name)
        if group_name == "system":
            continue
        values = series[metric_name]
        grouped.setdefault(group_name, []).append(
            {
                "metric": metric_name,
                "x": values["x"],
                "y": values["y"],
            }
        )

    return [
        {"name": group_name, "charts": charts}
        for group_name, charts in sorted(grouped.items())
    ]


def _is_metric_key(key: str) -> bool:
    if key == "_step":
        return False
    if key.startswith("_"):
        return False
    if key.startswith("system/") or key.startswith("system."):
        return False
    return True


def _metric_group(metric_name: str) -> str:
    if "/" not in metric_name:
        return "metrics"
    return metric_name.split("/", 1)[0] or "metrics"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _cache_path(cache_dir: Path, run_id: str) -> Path:
    digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.json"
