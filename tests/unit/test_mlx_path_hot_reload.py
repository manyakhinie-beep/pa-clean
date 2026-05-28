"""
Tests for live ``mlx_model_path`` refresh.

User-reported bug: «настройки mlx_model_path: ui и .env не подхватывают
изменение пути к модели». Three failure modes covered:

  1. ``MLXEngine._model_path`` was captured at ``__init__`` and never
     refreshed from ``settings.mlx_model_path`` — UI changes were ignored.
  2. ``MLXEngine._loaded`` was sticky — once a model loaded, no path
     change would force a reload.
  3. The legacy ``POST /settings`` endpoint only wrote to ``.env`` and
     printed «применится после перезапуска». UI changes went stale.

After the fix:
  * ``model_path`` is a property that reads ``settings.mlx_model_path``
    on every access.
  * ``_loaded`` is a property that detects path-drift (loaded path ≠
    current path) and returns False, forcing reload.
  * ``reload()`` releases the cached model + tokenizer.
  * ``PATCH /api/v1/rules/settings`` calls ``engine.reload()`` when
    ``mlx_model_path`` changes.
  * ``POST /settings`` applies new values to live ``settings`` AND
    triggers ``engine.reload()`` when ``mlx_model_path`` changes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ----------------------------------------------------------------------
# MLXEngine — path property + reload
# ----------------------------------------------------------------------


def test_model_path_reads_settings_live(monkeypatch):
    """``engine.model_path`` reflects current settings, not creation-time."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/initial/path")
    engine = MLXEngine()
    assert engine.model_path == "/initial/path"

    # Change settings AFTER engine creation — value must update
    monkeypatch.setattr(settings, "mlx_model_path", "/changed/path")
    assert engine.model_path == "/changed/path", (
        "engine.model_path must follow live settings"
    )


def test_override_path_wins_over_settings(monkeypatch):
    """Explicit ``MLXEngine(model_path=…)`` ignores settings."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/settings/path")
    engine = MLXEngine(model_path="/explicit/path")
    assert engine.model_path == "/explicit/path"

    monkeypatch.setattr(settings, "mlx_model_path", "/other/settings/path")
    assert engine.model_path == "/explicit/path", (
        "override path must not pick up settings changes"
    )


def test_legacy_underscore_attr_still_works(monkeypatch):
    """Older callers use ``engine._model_path`` — keep alias working."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/foo")
    engine = MLXEngine()
    assert engine._model_path == "/foo"


def test_is_loaded_detects_path_drift(monkeypatch):
    """When ``mlx_model_path`` changes after a model was loaded,
    ``is_loaded`` flips to False — forcing a reload on next call."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/path-a")
    engine = MLXEngine()
    # Simulate a successful load
    engine._model = MagicMock()
    engine._tokenizer = MagicMock()
    engine._loaded_from_path = "/path-a"
    assert engine.is_loaded is True

    # Path drift
    monkeypatch.setattr(settings, "mlx_model_path", "/path-b")
    assert engine.is_loaded is False, (
        "is_loaded must return False when the path drifted from the "
        "loaded model — that's what forces _ensure_loaded to reload"
    )


def test_reload_drops_model_and_tokenizer(monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/foo")
    engine = MLXEngine()
    engine._model = MagicMock()
    engine._tokenizer = MagicMock()
    engine._loaded_from_path = "/foo"
    assert engine.is_loaded is True

    engine.reload()
    assert engine._model is None
    assert engine._tokenizer is None
    assert engine._loaded_from_path is None
    assert engine.is_loaded is False


def test_model_name_reflects_current_settings(monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "/models/Qwen2.5-7B-4bit")
    engine = MLXEngine()
    assert engine.model_name == "Qwen2.5-7B-4bit"

    monkeypatch.setattr(settings, "mlx_model_path", "/models/T-Lite-it-4bit")
    assert engine.model_name == "T-Lite-it-4bit", (
        "model_name must follow live settings, not creation-time snapshot"
    )


def test_model_name_says_not_configured_when_empty(monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.engine import MLXEngine

    monkeypatch.setattr(settings, "mlx_model_path", "")
    engine = MLXEngine()
    assert engine.model_name == "not configured"


# ----------------------------------------------------------------------
# PATCH /api/v1/rules/settings — triggers engine reload
# ----------------------------------------------------------------------


def _client(tmp_path, monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.server import app

    monkeypatch.setattr(settings, "vault_path", tmp_path)
    # ``settings.update()`` writes to ``settings._config_path`` — pin it to
    # the test's tmp_path so we don't try to create directories outside
    # the sandbox (default is project_root/data/config.json which fails
    # in CI / sandboxed environments).
    monkeypatch.setattr(settings, "_config_path", tmp_path / "config.json")
    settings.e2e_test_mode = True
    return TestClient(app)


def test_rules_settings_patch_triggers_engine_reload(tmp_path, monkeypatch):
    """When PATCH /rules/settings changes mlx_model_path, the shared
    engine on state.engine must have .reload() invoked."""
    from personal_assistant.mlx_server import server as _srv

    fake_engine = MagicMock()
    fake_engine.reload = MagicMock()
    monkeypatch.setattr(_srv.state, "engine", fake_engine, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.patch(
        "/api/v1/rules/settings",
        json={"mlx_model_path": "/new/path/to/model"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body.get("mlx_reloaded") is True
    fake_engine.reload.assert_called_once()


def test_rules_settings_patch_no_reload_when_path_unchanged(tmp_path, monkeypatch):
    """If the new path equals the old one, don't bother reloading."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server import server as _srv

    monkeypatch.setattr(settings, "mlx_model_path", "/same/path")
    fake_engine = MagicMock()
    fake_engine.reload = MagicMock()
    monkeypatch.setattr(_srv.state, "engine", fake_engine, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.patch(
        "/api/v1/rules/settings",
        json={"mlx_model_path": "/same/path"},
    )
    assert r.status_code == 200
    assert r.json().get("mlx_reloaded") is False
    fake_engine.reload.assert_not_called()


def test_rules_settings_patch_no_reload_for_unrelated_field(tmp_path, monkeypatch):
    """Changing mlx_temperature must NOT reload the model."""
    from personal_assistant.mlx_server import server as _srv

    fake_engine = MagicMock()
    fake_engine.reload = MagicMock()
    monkeypatch.setattr(_srv.state, "engine", fake_engine, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.patch(
        "/api/v1/rules/settings",
        json={"mlx_temperature": 0.5},
    )
    assert r.status_code == 200, r.text
    fake_engine.reload.assert_not_called()


def test_rules_settings_patch_robust_when_engine_missing(tmp_path, monkeypatch):
    """If state.engine is None, PATCH must still succeed (engine not
    started yet, e.g. on cold server)."""
    from personal_assistant.mlx_server import server as _srv

    monkeypatch.setattr(_srv.state, "engine", None, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.patch(
        "/api/v1/rules/settings",
        json={"mlx_model_path": "/new/path"},
    )
    assert r.status_code == 200
    assert r.json().get("mlx_reloaded") is False


# ----------------------------------------------------------------------
# POST /settings — legacy .env writer must now apply live
# ----------------------------------------------------------------------


def test_post_settings_applies_to_live_settings(tmp_path, monkeypatch):
    """POST /settings must update the in-memory settings.mlx_model_path,
    not just write to .env."""
    from personal_assistant.config import settings
    from personal_assistant.webui import routes as web_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(web_routes, "_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "mlx_model_path", "/old/path")

    c = _client(tmp_path, monkeypatch)
    r = c.post(
        "/settings",
        json={"mlx_model_path": "/new/path/via/legacy/settings"},
    )
    assert r.status_code == 200, r.text
    assert settings.mlx_model_path == "/new/path/via/legacy/settings", (
        "POST /settings must apply to live settings, not only .env"
    )


def test_post_settings_writes_to_env(tmp_path, monkeypatch):
    """Backward compatibility: .env still receives the update so the
    value survives a server restart."""
    from personal_assistant.config import settings
    from personal_assistant.webui import routes as web_routes

    env_file = tmp_path / ".env"
    env_file.touch()
    monkeypatch.setattr(web_routes, "_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "mlx_model_path", "/initial")

    c = _client(tmp_path, monkeypatch)
    c.post("/settings", json={"mlx_model_path": "/persisted/path"})

    content = env_file.read_text(encoding="utf-8")
    assert "PA_MLX_MODEL_PATH" in content
    assert "/persisted/path" in content


def test_post_settings_reloads_engine_on_path_change(tmp_path, monkeypatch):
    """POST /settings changing mlx_model_path must drop the engine model."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server import server as _srv
    from personal_assistant.webui import routes as web_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(web_routes, "_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "mlx_model_path", "/old")

    fake_engine = MagicMock()
    fake_engine.reload = MagicMock()
    monkeypatch.setattr(_srv.state, "engine", fake_engine, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.post("/settings", json={"mlx_model_path": "/brand/new/path"})
    assert r.status_code == 200
    assert r.json().get("mlx_reloaded") is True
    fake_engine.reload.assert_called_once()


def test_post_settings_no_reload_when_path_same(tmp_path, monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server import server as _srv
    from personal_assistant.webui import routes as web_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(web_routes, "_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "mlx_model_path", "/same")

    fake_engine = MagicMock()
    fake_engine.reload = MagicMock()
    monkeypatch.setattr(_srv.state, "engine", fake_engine, raising=False)

    c = _client(tmp_path, monkeypatch)
    r = c.post("/settings", json={"mlx_model_path": "/same"})
    assert r.status_code == 200
    assert r.json().get("mlx_reloaded") is False
    fake_engine.reload.assert_not_called()


def test_post_settings_response_no_longer_says_restart_required(tmp_path, monkeypatch):
    """The misleading «применятся после перезапуска» message is gone:
    the new note explicitly says the values are applied live (only
    vault_path / log_level still need restart for full effect)."""
    from personal_assistant.webui import routes as web_routes
    env_file = tmp_path / ".env"
    monkeypatch.setattr(web_routes, "_ENV_FILE", env_file)

    c = _client(tmp_path, monkeypatch)
    r = c.post("/settings", json={"mlx_model_path": "/x"})
    note = r.json().get("note", "")
    assert "применены к текущему процессу" in note.lower() \
        or "применены" in note.lower()
