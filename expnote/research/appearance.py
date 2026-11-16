"""Load and save the local user's Research appearance settings."""

from __future__ import annotations

import json
import tomllib

from expnote.research.errors import ResearchError
from expnote.research.layout import atomic_bytes
from expnote.workspace import config_home

FILENAME = "research-layout.toml"
DEFAULTS = {
    "ui_font_size": 15,
    "prose_font_size": 17,
    "density": "comfortable",
    "theme": "system",
    "home_layout": "default",
}
FONT_RANGES = {"ui_font_size": (13, 20), "prose_font_size": (14, 24)}
CHOICES = {
    "density": ("comfortable", "compact"),
    "theme": ("system", "light", "dark"),
    "home_layout": ("default", "task-first", "single-column"),
}


def _valid(key: str, value: object) -> bool:
    if key in FONT_RANGES:
        low, high = FONT_RANGES[key]
        return type(value) is int and low <= value <= high
    return isinstance(value, str) and value in CHOICES[key]


def load_layout() -> dict:
    path = config_home() / FILENAME
    result = {"defaults": dict(DEFAULTS), "settings": dict(DEFAULTS)}
    try:
        with path.open("rb") as source:
            values = tomllib.load(source)
    except FileNotFoundError:
        return result
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        result["warning"] = f"Could not read {FILENAME}. Using the defaults."
        return result

    invalid = []
    for key in DEFAULTS.keys() & values.keys():
        if _valid(key, values[key]):
            result["settings"][key] = values[key]
        else:
            invalid.append(key)
    if invalid:
        fields = ", ".join(sorted(invalid))
        result["warning"] = (
            f"Using defaults for {fields} in {FILENAME}."
        )
    return result


def save_layout(settings: dict) -> dict:
    if settings.keys() != DEFAULTS.keys():
        raise ResearchError("LAYOUT_INVALID", "Provide the five appearance settings.")
    for key in DEFAULTS:
        if not _valid(key, settings[key]):
            raise ResearchError(
                "LAYOUT_INVALID", f"Invalid appearance value for {key}."
            )
    # Each value is an integer or one of the known ASCII strings.
    text = "".join(f"{key} = {json.dumps(settings[key])}\n" for key in DEFAULTS)
    try:
        atomic_bytes(config_home() / FILENAME, text.encode("utf-8"))
    except OSError as error:
        raise ResearchError(
            "LAYOUT_SAVE_FAILED",
            f"Could not save {FILENAME}: {error.strerror or error}",
            exit_code=6,
        ) from error
    return {"settings": settings, "defaults": dict(DEFAULTS)}
