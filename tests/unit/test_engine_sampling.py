"""Unit tests for MLX sampling-parameter resolution.

These verify the engine is *config-driven* (reads settings.mlx_* defaults) and
that top_p is plumbed through whichever mlx_lm API version is present. They run
without MLX — the sampler branch is exercised via a patched ``_make_sampler``.
"""

from __future__ import annotations

import personal_assistant.mlx_server.engine as eng
from personal_assistant import config as cfg_mod
from personal_assistant.mlx_server.engine import _apply_sampling_kwargs, _resolve_sampling


def test_resolve_uses_config_defaults_when_none(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "mlx_max_tokens", 999)
    monkeypatch.setattr(cfg_mod.settings, "mlx_temperature", 0.55)
    monkeypatch.setattr(cfg_mod.settings, "mlx_top_p", 0.9)
    assert _resolve_sampling(None, None, None) == (999, 0.55, 0.9)


def test_resolve_explicit_args_override_config(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "mlx_max_tokens", 999)
    monkeypatch.setattr(cfg_mod.settings, "mlx_temperature", 0.55)
    monkeypatch.setattr(cfg_mod.settings, "mlx_top_p", 0.9)
    assert _resolve_sampling(128, 0.0, 0.5) == (128, 0.0, 0.5)


def test_resolve_temperature_zero_is_respected(monkeypatch):
    # 0.0 is falsy but is a valid temperature — must not fall back to config.
    monkeypatch.setattr(cfg_mod.settings, "mlx_temperature", 0.7)
    _, temp, _ = _resolve_sampling(None, 0.0, None)
    assert temp == 0.0


def test_apply_sampling_legacy_temp_api():
    kwargs: dict = {}
    _apply_sampling_kwargs({"temp", "max_tokens"}, kwargs, 0.4, 0.8)
    assert kwargs == {"temp": 0.4}  # no top_p key when API lacks it


def test_apply_sampling_temperature_and_top_p_api():
    kwargs: dict = {}
    _apply_sampling_kwargs({"temperature", "top_p"}, kwargs, 0.4, 0.8)
    assert kwargs == {"temperature": 0.4, "top_p": 0.8}


def test_apply_sampling_sampler_api(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(eng, "_make_sampler", lambda temp, top_p: sentinel)
    kwargs: dict = {}
    _apply_sampling_kwargs({"prompt", "max_tokens"}, kwargs, 0.4, 0.8)
    assert kwargs["sampler"] is sentinel


def test_apply_sampling_sampler_unavailable(monkeypatch):
    monkeypatch.setattr(eng, "_make_sampler", lambda temp, top_p: None)
    kwargs: dict = {}
    _apply_sampling_kwargs({"prompt"}, kwargs, 0.4, 0.8)
    assert "sampler" not in kwargs
    assert "temp" not in kwargs and "temperature" not in kwargs
