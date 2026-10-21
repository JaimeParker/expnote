from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "parity"))


@pytest.fixture(autouse=True)
def _isolated_research_home(tmp_path, monkeypatch):
    # config_home()/data_home() read EXPNOTE_* before XDG_*; pin both to tmp.
    monkeypatch.setenv("EXPNOTE_CONFIG_HOME", str(tmp_path / ".cfg"))
    monkeypatch.setenv("EXPNOTE_DATA_HOME", str(tmp_path / ".data"))
