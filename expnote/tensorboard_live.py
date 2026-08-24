from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class TensorboardLiveError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _read_tensorboard_scalars(path: str, *, samples: int) -> list[dict[str, Any]]:
    log_dir = Path(path)
    if not log_dir.is_dir():
        raise TensorboardLiveError(
            "path_not_found",
            f"TensorBoard log directory not found: {path}",
        )

    try:
        from tensorboard.backend.event_processing import event_multiplexer
    except ImportError as exc:
        raise TensorboardLiveError(
            "tensorboard_not_installed",
            "The tensorboard Python package is not installed in this environment.",
        ) from exc

    try:
        em = event_multiplexer.EventMultiplexer(size_guidance={"scalars": samples})
        em.AddRunsFromDirectory(str(log_dir))
        em.Reload()
        entries: list[dict[str, Any]] = []
        for run_name, tags in em.Runs().items():
            for tag in tags.get("scalars", []):
                for event in em.Scalars(run_name, tag):
                    entries.append(
                        {
                            "run": run_name,
                            "tag": tag,
                            "step": event.step,
                            "value": event.value,
                        }
                    )
        return entries
    except TensorboardLiveError:
        raise
    except Exception as exc:
        raise TensorboardLiveError(
            "tensorboard_read_error",
            str(exc) or exc.__class__.__name__,
        ) from exc


def group_tensorboard_scalars(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs = {entry["run"] for entry in entries}
    multi_run = len(runs) > 1

    series: dict[str, dict[str, Any]] = {}
    for entry in entries:
        value = _number(entry.get("value"))
        if value is None:
            continue
        tag = str(entry["tag"])
        label = f"{entry['run']}: {tag}" if multi_run else tag
        metric = series.setdefault(label, {"x": [], "y": [], "tag": tag})
        metric["x"].append(float(entry["step"]))
        metric["y"].append(value)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for label in sorted(series):
        values = series[label]
        group_name = _metric_group(str(values["tag"]))
        grouped.setdefault(group_name, []).append(
            {
                "metric": label,
                "x": values["x"],
                "y": values["y"],
            }
        )

    return [
        {"name": group_name, "charts": charts}
        for group_name, charts in sorted(grouped.items())
    ]


def fetch_tensorboard_charts(path: str, *, samples: int = 1000) -> dict[str, Any]:
    entries = _read_tensorboard_scalars(path, samples=samples)
    groups = group_tensorboard_scalars(entries)
    return {
        "available": True,
        "source": path,
        "samples": samples,
        "groups": groups,
    }


def _metric_group(tag: str) -> str:
    if "/" not in tag:
        return "metrics"
    return tag.split("/", 1)[0] or "metrics"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number
