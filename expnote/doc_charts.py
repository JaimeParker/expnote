from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_MAX_POINTS = 2000
MAX_POINTS_LIMIT = 20000
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300


class DocChartError(RuntimeError):
    def __init__(self, reason: str, message: str, details: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details


@dataclass(frozen=True)
class DocChartContext:
    doc_id: str
    chart_dir: Path


def doc_chart_context(state_dir: Path, doc_id: str) -> DocChartContext:
    return DocChartContext(
        doc_id=doc_id,
        chart_dir=state_dir / "doc-assets" / doc_id,
    )


def chart_manifest(ctx: DocChartContext) -> list[dict[str, Any]]:
    path = ctx.chart_dir / "charts.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DocChartError("invalid_manifest", str(exc)) from exc
    if not isinstance(data, list):
        raise DocChartError(
            "invalid_manifest",
            "charts.json must contain a JSON array.",
        )
    charts: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise DocChartError(
                "invalid_manifest",
                f"chart entry {index} must be a JSON object.",
            )
        chart_id = item.get("id")
        if not isinstance(chart_id, str) or not chart_id:
            raise DocChartError(
                "invalid_manifest",
                f"chart entry {index} must include a non-empty string id.",
            )
        charts.append(item)
    return charts


def chart_summary(ctx: DocChartContext) -> dict[str, Any]:
    try:
        charts = chart_manifest(ctx)
    except DocChartError as exc:
        return _error(exc)
    return {
        "available": True,
        "charts": [
            {
                "id": str(chart["id"]),
                "title": str(chart.get("title") or chart["id"]),
                "type": str(chart.get("type") or "series"),
            }
            for chart in charts
        ],
    }


def render_chart(
    ctx: DocChartContext,
    chart_id: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    try:
        chart = _chart_by_id(ctx, chart_id)
        chart_type = chart.get("type") or "series"
        if chart_type == "series":
            return _series_chart(ctx, chart)
        if chart_type == "python":
            return _python_chart(ctx, chart, refresh=refresh)
        raise DocChartError(
            "unsupported_chart_type",
            f"unsupported chart type: {chart_type}",
        )
    except DocChartError as exc:
        return _error(exc, chart_id=chart_id)


def resolve_asset(ctx: DocChartContext, relative_path: str) -> Path:
    return _asset_path(ctx, relative_path)


def _chart_by_id(ctx: DocChartContext, chart_id: str) -> dict[str, Any]:
    for chart in chart_manifest(ctx):
        if chart.get("id") == chart_id:
            return chart
    raise DocChartError("chart_not_found", f"chart not found: {chart_id}")


def _series_chart(ctx: DocChartContext, chart: dict[str, Any]) -> dict[str, Any]:
    source = _required_str(chart, "source")
    x_key = _required_str(chart, "x")
    y_keys = chart.get("y")
    if isinstance(y_keys, str):
        y_keys = [y_keys]
    valid_y_keys = isinstance(y_keys, list) and all(
        isinstance(item, str) for item in y_keys
    )
    if not valid_y_keys:
        raise DocChartError(
            "invalid_manifest",
            "series chart y must be a string array.",
        )
    source_path = _asset_path(ctx, source)
    suffix = source_path.suffix.lower()
    if suffix == ".csv":
        values = _read_csv_series(source_path, x_key, y_keys)
    elif suffix == ".npz":
        values = _read_npz_series(source_path, x_key, y_keys)
    else:
        raise DocChartError(
            "unsupported_source",
            "series chart source must be a .csv or .npz file.",
        )
    max_points = _max_points(chart.get("max_points"))
    original_points = len(values[x_key])
    indexes = _sample_indexes(original_points, max_points)
    traces = [
        {
            "type": "scatter",
            "mode": "lines",
            "name": y_key,
            "x": [values[x_key][index] for index in indexes],
            "y": [values[y_key][index] for index in indexes],
        }
        for y_key in y_keys
    ]
    return {
        "available": True,
        "id": str(chart["id"]),
        "title": str(chart.get("title") or chart["id"]),
        "type": "series",
        "original_points": original_points,
        "returned_points": len(indexes),
        "plotly": {
            "data": traces,
            "layout": {"title": str(chart.get("title") or chart["id"])},
            "config": {"responsive": True, "displaylogo": False},
        },
    }


def _read_csv_series(
    path: Path,
    x_key: str,
    y_keys: list[str],
) -> dict[str, list[float]]:
    if not path.exists():
        raise DocChartError("source_not_found", f"source file not found: {path.name}")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = [x_key, *y_keys]
        fieldnames = reader.fieldnames or []
        missing = [column for column in columns if column not in fieldnames]
        if missing:
            raise DocChartError("missing_column", "missing CSV column: " + missing[0])
        values = {column: [] for column in columns}
        for row_number, row in enumerate(reader, start=2):
            for column in columns:
                values[column].append(_number(row.get(column), column, row_number))
    _require_equal_lengths(values)
    return values


def _read_npz_series(
    path: Path,
    x_key: str,
    y_keys: list[str],
) -> dict[str, list[float]]:
    if not path.exists():
        raise DocChartError("source_not_found", f"source file not found: {path.name}")
    try:
        import numpy as np
    except ImportError as exc:
        raise DocChartError(
            "numpy_not_installed",
            "The numpy Python package is required to read .npz chart sources.",
        ) from exc
    data = np.load(path)
    columns = [x_key, *y_keys]
    missing = [column for column in columns if column not in data.files]
    if missing:
        raise DocChartError("missing_array", "missing NPZ array: " + missing[0])
    values: dict[str, list[float]] = {}
    for column in columns:
        array = data[column]
        if len(array.shape) != 1:
            raise DocChartError(
                "invalid_array",
                f"NPZ array must be one-dimensional: {column}",
            )
        values[column] = [
            _number(item, column, index + 1)
            for index, item in enumerate(array)
        ]
    _require_equal_lengths(values)
    return values


def _python_chart(
    ctx: DocChartContext,
    chart: dict[str, Any],
    *,
    refresh: bool,
) -> dict[str, Any]:
    script = _asset_path(ctx, _required_str(chart, "script"))
    png = _asset_path(ctx, _required_str(chart, "png"))
    plotly = _asset_path(ctx, _required_str(chart, "plotly"))
    cached = png.exists() and plotly.exists()
    if refresh or not cached:
        _run_python_chart(ctx, script, _timeout_seconds(chart.get("timeout_seconds")))
        cached = False
    if not png.exists():
        raise DocChartError(
            "missing_output",
            f"Python chart did not create {png.name}.",
        )
    if not plotly.exists():
        raise DocChartError(
            "missing_output",
            f"Python chart did not create {plotly.name}.",
        )
    try:
        plotly_payload = json.loads(plotly.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "available": True,
            "id": str(chart["id"]),
            "title": str(chart.get("title") or chart["id"]),
            "type": "python",
            "cached": cached,
            "png_url": _asset_url(ctx.doc_id, _relative_asset(ctx, png)),
            "plotly_error": {
                "reason": "invalid_plotly_json",
                "message": str(exc),
            },
        }
    if not isinstance(plotly_payload, dict) or not isinstance(
        plotly_payload.get("data"), list
    ):
        return {
            "available": True,
            "id": str(chart["id"]),
            "title": str(chart.get("title") or chart["id"]),
            "type": "python",
            "cached": cached,
            "png_url": _asset_url(ctx.doc_id, _relative_asset(ctx, png)),
            "plotly_error": {
                "reason": "invalid_plotly_json",
                "message": "Plotly JSON must be an object with a data array.",
            },
        }
    return {
        "available": True,
        "id": str(chart["id"]),
        "title": str(chart.get("title") or chart["id"]),
        "type": "python",
        "cached": cached,
        "png_url": _asset_url(ctx.doc_id, _relative_asset(ctx, png)),
        "plotly": plotly_payload,
    }


def _run_python_chart(
    ctx: DocChartContext,
    script: Path,
    timeout_seconds: int,
) -> None:
    if not script.exists():
        raise DocChartError("script_not_found", f"script not found: {script.name}")
    try:
        result = subprocess.run(
            [sys.executable, str(script.name)],
            cwd=ctx.chart_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocChartError(
            "python_timeout",
            f"Python chart timed out after {timeout_seconds}s.",
            _clip_output((exc.stdout or "") + "\n" + (exc.stderr or "")),
        ) from exc
    if result.returncode != 0:
        raise DocChartError(
            "python_failed",
            f"Python chart exited with status {result.returncode}.",
            _clip_output(result.stdout + "\n" + result.stderr),
        )


def _asset_path(ctx: DocChartContext, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise DocChartError(
            "invalid_path",
            f"asset path must stay inside {ctx.doc_id}.",
        )
    resolved = (ctx.chart_dir / path).resolve(strict=False)
    chart_dir = ctx.chart_dir.resolve(strict=False)
    if not resolved.is_relative_to(chart_dir):
        raise DocChartError(
            "invalid_path",
            f"asset path must stay inside {ctx.doc_id}.",
        )
    return resolved


def _relative_asset(ctx: DocChartContext, path: Path) -> str:
    return str(path.relative_to(ctx.chart_dir.resolve(strict=False)))


def _asset_url(doc_id: str, relative_path: str) -> str:
    return (
        f"/api/docs/{quote(doc_id, safe='')}/assets/"
        f"{quote(relative_path, safe='/')}"
    )


def _required_str(chart: dict[str, Any], key: str) -> str:
    value = chart.get(key)
    if not isinstance(value, str) or not value:
        raise DocChartError("invalid_manifest", f"chart {key} must be a string.")
    return value


def _number(value: Any, column: str, row_number: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DocChartError(
            "non_numeric_value",
            f"non-numeric value in {column} at row {row_number}.",
        ) from exc


def _require_equal_lengths(values: dict[str, list[float]]) -> None:
    lengths = {len(items) for items in values.values()}
    if len(lengths) > 1:
        raise DocChartError("length_mismatch", "chart series lengths do not match.")


def _max_points(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_POINTS
    try:
        points = int(value)
    except (TypeError, ValueError) as exc:
        raise DocChartError(
            "invalid_manifest",
            "max_points must be an integer.",
        ) from exc
    if points < 1:
        raise DocChartError("invalid_manifest", "max_points must be at least 1.")
    return min(points, MAX_POINTS_LIMIT)


def _timeout_seconds(value: Any) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise DocChartError(
            "invalid_manifest",
            "timeout_seconds must be an integer.",
        ) from exc
    if timeout < 1:
        raise DocChartError("invalid_manifest", "timeout_seconds must be at least 1.")
    return min(timeout, MAX_TIMEOUT_SECONDS)


def _sample_indexes(size: int, limit: int) -> list[int]:
    if size <= limit:
        return list(range(size))
    if limit == 1:
        return [0]
    return [round(index * (size - 1) / (limit - 1)) for index in range(limit)]


def _clip_output(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... truncated ..."


def _error(
    exc: DocChartError,
    *,
    chart_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "available": False,
        "reason": exc.reason,
        "message": exc.message,
    }
    if chart_id is not None:
        data["id"] = chart_id
    if exc.details:
        data["details"] = exc.details
    return data
