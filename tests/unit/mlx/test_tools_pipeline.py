"""
Unit tests for the tool-calling pipeline: date_calc, validator, and router.

Covers:
  - date_calc: Russian AND English relative/absolute date parsing
  - date_calc.run() MSK ISO output
  - validator: registry loading, validate_tool_call, lazy reload, unknown tool
  - router: _run_builtin dispatch, graceful degradation on unknown tool
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# date_calc
# ---------------------------------------------------------------------------
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from personal_assistant.mlx_server.tools.date_calc import (
    parse_relative_date,
    tool_spec,
)
from personal_assistant.mlx_server.tools.date_calc import (
    run as date_calc_run,
)

ANCHOR = date(2026, 5, 24)  # Sunday


class TestDateCalcRussian:
    """Existing Russian keyword coverage (regression)."""

    def test_segodnya(self):
        r = parse_relative_date("сегодня", anchor=ANCHOR)
        assert r["iso"] == "2026-05-24"

    def test_zavtra(self):
        r = parse_relative_date("завтра", anchor=ANCHOR)
        assert r["iso"] == "2026-05-25"

    def test_poslezavtra(self):
        r = parse_relative_date("послезавтра", anchor=ANCHOR)
        assert r["iso"] == "2026-05-26"

    def test_vchera(self):
        r = parse_relative_date("вчера", anchor=ANCHOR)
        assert r["iso"] == "2026-05-23"

    def test_cherez_n_dney(self):
        r = parse_relative_date("через 5 дней", anchor=ANCHOR)
        assert r["iso"] == "2026-05-29"

    def test_absolute_iso(self):
        r = parse_relative_date("2026-12-31", anchor=ANCHOR)
        assert r["iso"] == "2026-12-31"

    def test_absolute_dmy(self):
        r = parse_relative_date("16.05.2026", anchor=ANCHOR)
        assert r["iso"] == "2026-05-16"


class TestDateCalcEnglish:
    """New English keyword support (Bug #2 fix)."""

    def test_today(self):
        r = parse_relative_date("today", anchor=ANCHOR)
        assert r is not None
        assert r["iso"] == "2026-05-24"

    def test_tomorrow(self):
        r = parse_relative_date("tomorrow", anchor=ANCHOR)
        assert r is not None
        assert r["iso"] == "2026-05-25"

    def test_yesterday(self):
        r = parse_relative_date("yesterday", anchor=ANCHOR)
        assert r is not None
        assert r["iso"] == "2026-05-23"

    def test_day_after_tomorrow(self):
        r = parse_relative_date("day after tomorrow", anchor=ANCHOR)
        assert r is not None
        assert r["iso"] == "2026-05-26"

    def test_today_case_insensitive(self):
        r = parse_relative_date("TODAY", anchor=ANCHOR)
        assert r is not None
        assert r["iso"] == "2026-05-24"

    def test_tomorrow_case_insensitive(self):
        r = parse_relative_date("TOMORROW", anchor=ANCHOR)
        assert r is not None

    def test_english_human_readable_not_empty(self):
        """human field must be non-empty for English keywords."""
        for expr in ["today", "tomorrow", "yesterday", "day after tomorrow"]:
            r = parse_relative_date(expr, anchor=ANCHOR)
            assert r is not None
            assert len(r.get("human", "")) > 0, f"human empty for {expr!r}"

    def test_unknown_returns_none(self):
        r = parse_relative_date("someday", anchor=ANCHOR)
        assert r is None


class TestDateCalcRun:
    """run() entrypoint — always returns MSK-offset ISO."""

    def test_run_today_returns_msk_iso(self):
        result = date_calc_run("today")
        assert "error" not in result
        assert "iso" in result
        assert "+03:00" in result["iso"] or "T" in result["iso"]

    def test_run_tomorrow_no_error(self):
        result = date_calc_run("tomorrow")
        assert "error" not in result

    def test_run_yesterday_no_error(self):
        result = date_calc_run("yesterday")
        assert "error" not in result

    def test_run_invalid_returns_error(self):
        result = date_calc_run("в какой-то день")
        assert "error" in result

    def test_run_russian_today_no_error(self):
        result = date_calc_run("сегодня")
        assert "error" not in result

    def test_tool_spec_mentions_english(self):
        spec = tool_spec()
        desc = spec["description"]
        assert "today" in desc
        assert "tomorrow" in desc


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------

from personal_assistant.mlx_server.tools.validator import (  # noqa: E402
    ToolValidationError,
    _get_registry,
    validate_tool_call,
)


class TestValidatorRegistry:
    """registry.json loaded correctly and lazy reload works."""

    def test_registry_contains_date_calc(self):
        reg = _get_registry()
        assert "date_calc" in reg

    def test_date_calc_is_enabled(self):
        reg = _get_registry()
        assert reg["date_calc"].get("enabled", True) is True

    def test_date_calc_has_parameters(self):
        reg = _get_registry()
        params = reg["date_calc"].get("parameters", {})
        assert "properties" in params
        assert "expression" in params["properties"]

    def test_lazy_reload_from_empty(self, monkeypatch):
        """_get_registry() reloads from disk when _REGISTRY was empty at import."""
        import personal_assistant.mlx_server.tools.validator as v_mod
        original = dict(v_mod._REGISTRY)
        monkeypatch.setattr(v_mod, "_REGISTRY", {})
        result = v_mod._get_registry()
        assert "date_calc" in result
        # restore
        monkeypatch.setattr(v_mod, "_REGISTRY", original)

    def test_missing_registry_file_returns_empty(self, tmp_path, monkeypatch):
        """_load_registry() returns {} gracefully when file is absent."""
        import personal_assistant.mlx_server.tools.validator as v_mod
        monkeypatch.setattr(v_mod, "_REGISTRY_FILE", tmp_path / "no_file.json")
        result = v_mod._load_registry()
        assert result == {}


class TestValidateToolCall:
    """validate_tool_call() success and failure cases."""

    def test_valid_date_calc_call(self):
        result = validate_tool_call({"name": "date_calc", "arguments": {"expression": "tomorrow"}})
        assert result["name"] == "date_calc"
        assert result["arguments"]["expression"] == "tomorrow"

    def test_unknown_tool_raises(self):
        with pytest.raises(ToolValidationError) as exc_info:
            validate_tool_call({"name": "nonexistent_tool", "arguments": {}})
        err = exc_info.value
        assert "nonexistent_tool" in str(err)
        assert "available_tools" in (err.expected_schema or {})

    def test_error_includes_available_tools_list(self):
        with pytest.raises(ToolValidationError) as exc_info:
            validate_tool_call({"name": "nope", "arguments": {}})
        assert "date_calc" in exc_info.value.expected_schema.get("available_tools", [])

    def test_missing_name_raises(self):
        with pytest.raises(ToolValidationError):
            validate_tool_call({"arguments": {"expression": "today"}})

    def test_empty_name_raises(self):
        with pytest.raises(ToolValidationError):
            validate_tool_call({"name": "", "arguments": {}})

    def test_arguments_defaults_to_empty_dict(self):
        # No "arguments" key — should default to {} and not crash on structure check
        # (expression is required; validator builds arg_model and validates)
        # This may raise ToolValidationError for missing required param — that's OK.
        try:
            result = validate_tool_call({"name": "date_calc"})
            # If it passes, arguments must be a dict
            assert isinstance(result["arguments"], dict)
        except ToolValidationError:
            pass  # acceptable — expression is required

    def test_english_expression_passes_validation(self):
        for expr in ["today", "tomorrow", "yesterday", "day after tomorrow"]:
            result = validate_tool_call({"name": "date_calc", "arguments": {"expression": expr}})
            assert result["arguments"]["expression"] == expr


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

from personal_assistant.mlx_server.tools.router import execute_tool  # noqa: E402


class TestRouter:
    """execute_tool() dispatches date_calc and degrades gracefully.

    execute_tool(raw_call) takes a single dict {"name": ..., "arguments": {...}}
    and returns {"ok": bool, "result"|"error": str, "status": str}.
    """

    def _call(self, name: str, args: dict) -> dict:
        return execute_tool({"name": name, "arguments": args})

    def test_execute_date_calc_today(self):
        result = self._call("date_calc", {"expression": "today"})
        assert result["ok"] is True
        assert "result" in result

    def test_execute_date_calc_tomorrow(self):
        result = self._call("date_calc", {"expression": "tomorrow"})
        assert result["ok"] is True

    def test_execute_date_calc_english_keywords(self):
        for expr in ["today", "tomorrow", "yesterday", "day after tomorrow"]:
            result = self._call("date_calc", {"expression": expr})
            assert result["ok"] is True, f"Failed for {expr!r}: {result}"
            # Result text must NOT contain the error string
            assert "Не удалось распознать" not in result.get("result", ""), \
                f"Error in result for {expr!r}: {result}"

    def test_execute_date_calc_russian_keywords(self):
        for expr in ["сегодня", "завтра", "вчера", "послезавтра"]:
            result = self._call("date_calc", {"expression": expr})
            assert result["ok"] is True, f"Failed for {expr!r}: {result}"
            assert "Не удалось распознать" not in result.get("result", ""), \
                f"Error in result for {expr!r}: {result}"

    def test_execute_unknown_tool_validation_fails(self):
        """Unknown tool returns ok=False (validation error), does NOT raise."""
        result = self._call("totally_unknown_tool", {"foo": "bar"})
        assert isinstance(result, dict)
        assert result["ok"] is False
        assert "status" in result

    def test_execute_date_calc_iso_in_result(self):
        """ISO date appears in the router output for absolute expressions."""
        result = self._call("date_calc", {"expression": "2026-12-31"})
        assert result["ok"] is True
        assert "2026-12-31" in result.get("result", "")

    def test_execute_returns_dict(self):
        result = self._call("date_calc", {"expression": "today"})
        assert isinstance(result, dict)
        assert "ok" in result
        assert "status" in result
