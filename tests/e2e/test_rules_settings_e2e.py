"""E2E test for the Rules-tab AI-tool settings flow.

Exercises the full FastAPI app in-process (matching the project's TestClient
convention): change a setting via the API, confirm it is persisted to
``config.json`` and reflected on the next read — the same round-trip the
"Правила" tab performs in the browser.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Full-app TestClient with the settings overlay redirected to a temp file."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.server import app

    snapshot = settings.editable_dict()
    orig_path = settings._config_path
    settings._config_path = tmp_path / "config.json"

    yield TestClient(app, raise_server_exceptions=False), tmp_path / "config.json"

    for key, value in snapshot.items():
        setattr(settings, key, value)
    settings._config_path = orig_path


def test_rules_tab_saves_mlx_settings(client):
    """The instruction's acceptance scenario: edit temperature -> save -> persisted."""
    tc, cfg_file = client

    # The tab loads current settings + schema.
    got = tc.get("/api/v1/rules/settings")
    assert got.status_code == 200
    assert "mlx_temperature" in got.json()["settings"]

    # User edits the MLX temperature and saves.
    saved = tc.patch("/api/v1/rules/settings", json={"mlx_temperature": 0.7})
    assert saved.status_code == 200

    # Persisted to config.json on disk.
    config = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert config["mlx_temperature"] == 0.7

    # And reflected on the next read (no restart).
    again = tc.get("/api/v1/rules/settings")
    assert again.json()["settings"]["mlx_temperature"] == 0.7


def test_rules_tab_round_trip_multiple_fields(client):
    tc, cfg_file = client
    resp = tc.patch(
        "/api/v1/rules/settings",
        json={"mail_auto_draft": True, "calendar_default_duration": 45, "e2e_test_mode": True},
    )
    assert resp.status_code == 200
    config = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert config["mail_auto_draft"] is True
    assert config["calendar_default_duration"] == 45
    assert config["e2e_test_mode"] is True


def test_rules_tab_rejects_invalid_value(client):
    tc, _ = client
    resp = tc.patch("/api/v1/rules/settings", json={"mlx_temperature": 5.0})
    assert resp.status_code == 400


def test_rules_tab_rejects_unknown_setting(client):
    tc, _ = client
    resp = tc.patch("/api/v1/rules/settings", json={"totally_made_up": 1})
    assert resp.status_code == 400
