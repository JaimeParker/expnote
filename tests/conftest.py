from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_expnote_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".xdg-data"))
