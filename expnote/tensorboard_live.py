from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


class TensorboardLiveError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _read_tensorboard_scalars(
    path: str,
    *,
    samples: int,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    log_dir = _resolve_tensorboard_dir(path, run_id=run_id)
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
        if tag.startswith("hparam/"):
            continue
        label = f"{entry['run']}: {tag}" if multi_run else tag
        metric = series.setdefault(label, {"x": [], "y": [], "tag": tag})
        metric["x"].append(float(entry["step"]))
        metric["y"].append(value)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for label in sorted(series):
        values = series[label]
        if len(values["x"]) < 2 or len(values["y"]) < 2:
            continue
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


def load_cached_tensorboard_chart(
    cache_dir: Path, run_id: str
) -> dict[str, Any] | None:
    cache_path = _cache_path(cache_dir, run_id)
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["cached"] = True
    return data


def fetch_tensorboard_charts(
    path: str,
    *,
    samples: int = 0,
    run_id: str | None = None,
    status: str | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    cacheable = cache_dir is not None and run_id is not None and status == "finished"
    if cacheable and not force:
        cached = load_cached_tensorboard_chart(cache_dir, run_id)
        if cached is not None:
            return cached

    source = _resolve_tensorboard_dir(path, run_id=run_id)
    entries = _read_tensorboard_scalars(str(source), samples=samples)
    groups = group_tensorboard_scalars(entries)
    data = {
        "available": True,
        "cached": False,
        "source": str(source),
        "samples": samples,
        "groups": groups,
    }
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


def _resolve_tensorboard_dir(path: str, *, run_id: str | None) -> Path:
    root = Path(path)
    if run_id:
        child = root / run_id
        if child.is_dir():
            return child
    return root


def _cache_path(cache_dir: Path, run_id: str) -> Path:
    digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.json"


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
