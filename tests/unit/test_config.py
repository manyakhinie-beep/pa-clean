"""Unit tests for the runtime-editable configuration layer (config.json overlay)."""

from __future__ import annotations

import json

import pytest

from personal_assistant.config import (
    EDITABLE_FIELDS,
    Settings,
    _coerce_and_validate,
)


def test_new_editable_fields_have_defaults(tmp_path):
    s = Settings(config_path=tmp_path / "config.json")
    assert s.mlx_top_p == 1.0
    assert s.mail_auto_draft is False
    assert s.calendar_check_conflicts is True
    assert s.calendar_default_duration == 60
    assert s.e2e_test_mode is False


def test_summary_prompt_is_not_a_config_field():
    # Canonical store for the summarization prompt is tool_prompts.summarize_system,
    # so it must not appear as an editable config setting.
    assert "mail_summary_prompt" not in EDITABLE_FIELDS


def test_editable_dict_covers_full_schema(tmp_path):
    s = Settings(config_path=tmp_path / "config.json")
    assert set(s.editable_dict()) == set(EDITABLE_FIELDS)


def test_update_persists_and_returns_full_dict(tmp_path):
    cfg = tmp_path / "config.json"
    s = Settings(config_path=cfg)
    out = s.update({"mlx_temperature": 0.7, "calendar_default_duration": 45})
    assert out["mlx_temperature"] == 0.7
    assert out["calendar_default_duration"] == 45
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["mlx_temperature"] == 0.7
    assert saved["calendar_default_duration"] == 45


def test_overlay_is_read_on_init(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mlx_temperature": 0.9, "e2e_test_mode": True}), encoding="utf-8")
    s = Settings(config_path=cfg)
    assert s.mlx_temperature == 0.9
    assert s.e2e_test_mode is True


def test_overlay_takes_precedence_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PA_MLX_TEMPERATURE", "0.1")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mlx_temperature": 0.8}), encoding="utf-8")
    s = Settings(config_path=cfg)
    assert s.mlx_temperature == 0.8


def test_update_merges_without_losing_prior_keys(tmp_path):
    cfg = tmp_path / "config.json"
    s = Settings(config_path=cfg)
    s.update({"mlx_temperature": 0.7})
    s.update({"mail_auto_draft": True})
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["mlx_temperature"] == 0.7
    assert saved["mail_auto_draft"] is True


@pytest.mark.parametrize(
    "name,value",
    [
        ("mlx_temperature", 2.5),
        ("mlx_temperature", -0.1),
        ("mlx_top_p", 1.5),
        ("mlx_max_tokens", 0),
        ("calendar_default_duration", 0),
        ("mlx_max_tokens", "not-an-int"),
    ],
)
def test_update_rejects_invalid_values(tmp_path, name, value):
    s = Settings(config_path=tmp_path / "config.json")
    with pytest.raises(ValueError):
        s.update({name: value})


def test_update_rejects_unknown_key(tmp_path):
    s = Settings(config_path=tmp_path / "config.json")
    with pytest.raises(KeyError):
        s.update({"does_not_exist": 1})


def test_update_is_all_or_nothing(tmp_path):
    """A single invalid value must leave both memory and disk untouched."""
    cfg = tmp_path / "config.json"
    s = Settings(config_path=cfg)
    before = s.mlx_temperature
    with pytest.raises(ValueError):
        s.update({"mlx_temperature": 0.7, "mlx_top_p": 5.0})
    assert s.mlx_temperature == before
    assert not cfg.exists()


def test_malformed_overlay_is_ignored(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ not valid json", encoding="utf-8")
    s = Settings(config_path=cfg)
    assert s.mlx_temperature == 0.3  # built-in default survives


def test_bool_coercion_from_strings_and_numbers():
    assert _coerce_and_validate("e2e_test_mode", "yes") is True
    assert _coerce_and_validate("e2e_test_mode", "on") is True
    assert _coerce_and_validate("e2e_test_mode", "0") is False
    assert _coerce_and_validate("e2e_test_mode", 1) is True
    assert _coerce_and_validate("e2e_test_mode", 0) is False
