"""Unit tests for the Rules-tab AI-tool settings API (/api/v1/rules/settings)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_assistant.config import EDITABLE_FIELDS, settings
from personal_assistant.webui.rules_settings import router


@pytest.fixture
def client(tmp_path):
    """Mount only the rules-settings router; redirect persistence to a temp file.

    The router uses the module-level ``settings`` singleton, so we snapshot its
    editable values and overlay path, then restore them afterwards to avoid
    leaking state into other tests.
    """
    snapshot = settings.editable_dict()
    orig_path = settings._config_path
    settings._config_path = tmp_path / "config.json"

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), tmp_path / "config.json"

    for key, value in snapshot.items():
        setattr(settings, key, value)
    settings._config_path = orig_path


def test_get_returns_settings_and_schema(client):
    tc, _ = client
    resp = tc.get("/api/v1/rules/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["settings"]) == set(EDITABLE_FIELDS)
    assert "mlx_temperature" in data["schema"]
    assert data["schema"]["mlx_temperature"]["max"] == 2.0


def test_patch_persists_and_applies_immediately(client):
    tc, cfg_file = client
    resp = tc.patch(
        "/api/v1/rules/settings",
        json={"mlx_temperature": 0.7, "mail_auto_draft": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["settings"]["mlx_temperature"] == 0.7

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["mlx_temperature"] == 0.7
    assert saved["mail_auto_draft"] is True

    # Applied to the live singleton without a restart.
    assert settings.mlx_temperature == 0.7
    assert settings.mail_auto_draft is True


def test_patch_rejects_out_of_range(client):
    tc, _ = client
    resp = tc.patch("/api/v1/rules/settings", json={"mlx_temperature": 3.0})
    assert resp.status_code == 400


def test_patch_rejects_unknown_key(client):
    tc, _ = client
    resp = tc.patch("/api/v1/rules/settings", json={"bogus": 1})
    assert resp.status_code == 400


def test_patch_empty_payload_is_400(client):
    tc, _ = client
    resp = tc.patch("/api/v1/rules/settings", json={})
    assert resp.status_code == 400
