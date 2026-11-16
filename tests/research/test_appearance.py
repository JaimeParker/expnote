"""Research appearance preferences through the local web service."""

from __future__ import annotations

import tomllib

import pytest
from fastapi.testclient import TestClient

from expnote.research.web_routes import create_research_app
from expnote.workspace import config_home

URL = "/api/research/layout"
DEFAULT = {
    "ui_font_size": 15,
    "prose_font_size": 17,
    "density": "comfortable",
    "theme": "system",
    "home_layout": "default",
}


@pytest.fixture
def web():
    app = create_research_app()
    client = TestClient(app, base_url="http://localhost:8765")
    return client, {"X-Expnote-Token": app.state.research.write_token}


def test_load_defaults_and_partial_file(web):
    client, _ = web
    path = config_home() / "research-layout.toml"
    response = client.get(URL)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"] == {"settings": DEFAULT, "defaults": DEFAULT}
    assert not path.exists()

    path.parent.mkdir(parents=True)
    path.write_text('ui_font_size = 18\ncontrast = "low"\n', encoding="utf-8")
    partial = client.get(URL).json()["data"]
    assert partial["settings"] == DEFAULT | {"ui_font_size": 18}
    assert "warning" not in partial


@pytest.mark.parametrize("text", [b"theme = [", b"\xff"])
def test_unreadable_config_uses_defaults_and_keeps_original(web, text):
    client, _ = web
    path = config_home() / "research-layout.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes(text)
    data = client.get(URL).json()["data"]
    assert data["settings"] == DEFAULT
    assert "research-layout.toml" in data["warning"]
    assert path.read_bytes() == text


def test_save_reload_and_reset_across_app_instances(web):
    client, headers = web
    payload = {
        **DEFAULT,
        "ui_font_size": 20,
        "prose_font_size": 22,
        "density": "compact",
        "theme": "dark",
        "home_layout": "single-column",
    }
    response = client.post(URL, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["settings"] == payload
    path = config_home() / "research-layout.toml"
    assert tomllib.loads(path.read_text(encoding="utf-8")) == payload
    second_client = TestClient(create_research_app())
    assert second_client.get(URL).json()["data"]["settings"] == payload
    assert client.post(URL, json=DEFAULT, headers=headers).status_code == 200
    assert second_client.get(URL).json()["data"]["settings"] == DEFAULT


def test_invalid_or_unauthorized_save_keeps_previous_file(web):
    client, headers = web
    assert client.post(URL, json=DEFAULT, headers=headers).status_code == 200
    path = config_home() / "research-layout.toml"
    original = path.read_bytes()
    for change in (
        {"ui_font_size": True},
        {"ui_font_size": 12},
        {"prose_font_size": 25},
        {"theme": "blue"},
        {"home_layout": "../../etc"},
        {"path": "another.toml"},
    ):
        response = client.post(URL, json=DEFAULT | change, headers=headers)
        assert response.status_code == 422
    assert client.post(URL, json=DEFAULT).status_code == 403
    foreign = headers | {"Origin": "http://example.test"}
    assert client.post(URL, json=DEFAULT, headers=foreign).status_code == 403
    assert path.read_bytes() == original


def test_unsaved_values_are_not_returned_after_write_failure(web, monkeypatch):
    from expnote.research import appearance

    client, headers = web
    assert client.post(URL, json=DEFAULT, headers=headers).status_code == 200

    def fail(*_args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(appearance, "atomic_bytes", fail)
    response = client.post(URL, json=DEFAULT | {"theme": "dark"}, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LAYOUT_SAVE_FAILED"
    assert client.get(URL).json()["data"]["settings"] == DEFAULT
