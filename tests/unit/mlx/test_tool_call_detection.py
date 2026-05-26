"""
Unit tests for tool call detection in chat_routes.py.

Covers:
  - Standard <tool_call>…</tool_call> format
  - GigaChat <|function_call|>{json} format (no closing tag)
  - _extract_json_object brace-counter
  - Mixed / no tool call
  - JSON extraction correctness
"""

from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# Replicate helpers under test (no FastAPI import needed)
# ---------------------------------------------------------------------------

_STANDARD_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_GIGACHAT_TAG_RE = re.compile(r"<\|function_call\|>\s*(\{)", re.DOTALL)


def _extract_json_object(text: str, start: int = 0):
    """Brace-counting JSON object extractor (mirrors chat_routes.py)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _detect(text: str):
    """Mirror of _detect_and_run_tools extraction logic (no execution)."""
    # Strategy 1: standard
    m1 = _STANDARD_TAG_RE.search(text)
    if m1:
        return m1.group(1)
    # Strategy 2: GigaChat
    m2 = _GIGACHAT_TAG_RE.search(text)
    if m2:
        return _extract_json_object(text, start=m2.start(1))
    return None


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------

class TestExtractJsonObject:
    def test_simple(self):
        assert _extract_json_object('{"a":1}') == '{"a":1}'

    def test_nested(self):
        s = '{"name":"x","args":{"k":"v"}}'
        assert _extract_json_object(s) == s

    def test_trailing_text(self):
        s = '{"a":1} extra text here'
        assert _extract_json_object(s) == '{"a":1}'

    def test_offset(self):
        s = 'prefix {"a":1} suffix'
        # start at index of '{'
        idx = s.index('{')
        assert _extract_json_object(s, start=idx) == '{"a":1}'

    def test_escaped_brace_in_string(self):
        # Brace inside a string value should not count
        s = '{"key":"value with } brace"}'
        assert _extract_json_object(s) == s

    def test_incomplete_returns_none(self):
        assert _extract_json_object('{"a":1') is None

    def test_deep_nesting(self):
        s = '{"a":{"b":{"c":1}}}'
        assert _extract_json_object(s) == s

    def test_real_gigachat_payload(self):
        raw = '{"name": "date_calc", "arguments": {"expression": "сегодня"}}'
        result = _extract_json_object(raw)
        assert result == raw
        data = json.loads(result)
        assert data["name"] == "date_calc"
        assert data["arguments"]["expression"] == "сегодня"


# ---------------------------------------------------------------------------
# Standard <tool_call> format
# ---------------------------------------------------------------------------

class TestStandardToolCall:
    def test_basic_match(self):
        text = '<tool_call>{"name": "date_calc", "arguments": {"expression": "today"}}</tool_call>'
        result = _detect(text)
        assert result is not None
        assert '"date_calc"' in result

    def test_match_with_surrounding_text(self):
        text = 'Some preamble. <tool_call>{"name": "search", "arguments": {"q": "test"}}</tool_call> trailing.'
        result = _detect(text)
        assert result is not None
        assert '"search"' in result

    def test_multiline_json(self):
        text = '<tool_call>{\n  "name": "date_calc",\n  "arguments": {"expression": "tomorrow"}\n}</tool_call>'
        result = _detect(text)
        assert result is not None
        assert '"date_calc"' in result

    def test_whitespace_around_json(self):
        text = '<tool_call>  {"name": "x", "arguments": {}}  </tool_call>'
        result = _detect(text)
        assert result is not None
        assert '"x"' in result


# ---------------------------------------------------------------------------
# GigaChat <|function_call|> format
# ---------------------------------------------------------------------------

class TestGigaChatToolCall:
    def test_basic_match(self):
        text = '<|function_call|>{"name": "date_calc", "arguments": {"expression": "сегодня"}}'
        result = _detect(text)
        assert result is not None
        assert '"date_calc"' in result

    def test_match_with_prefix_text(self):
        text = 'Конечно, сейчас вычислю. <|function_call|>{"name": "date_calc", "arguments": {"expression": "сегодня"}}'
        result = _detect(text)
        assert result is not None
        assert '"date_calc"' in result

    def test_arguments_preserved(self):
        text = '<|function_call|>{"name": "date_calc", "arguments": {"expression": "через 7 дней"}}'
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["name"] == "date_calc"
        assert data["arguments"]["expression"] == "через 7 дней"

    def test_whitespace_after_tag(self):
        text = '<|function_call|>  {"name": "search", "arguments": {"q": "встреча"}}'
        result = _detect(text)
        assert result is not None
        assert '"search"' in result

    def test_multiline_json(self):
        text = '<|function_call|>{\n"name": "date_calc",\n"arguments": {"expression": "завтра"}\n}'
        result = _detect(text)
        assert result is not None
        assert '"date_calc"' in result

    def test_nested_json(self):
        text = '<|function_call|>{"name": "search", "arguments": {"filters": {"date": "2026-05-21", "tags": ["work"]}}}'
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["name"] == "search"
        assert data["arguments"]["filters"]["date"] == "2026-05-21"

    def test_cyrillic_in_arguments(self):
        text = '<|function_call|>{"name": "date_calc", "arguments": {"expression": "следующий понедельник"}}'
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["arguments"]["expression"] == "следующий понедельник"

    def test_trailing_text_after_json(self):
        """Brace-counter stops at the closing brace even with trailing text."""
        text = '<|function_call|>{"name": "date_calc", "arguments": {"expression": "сегодня"}} лишний текст'
        result = _detect(text)
        assert result is not None
        # Must be valid JSON (no trailing text captured)
        data = json.loads(result)
        assert data["name"] == "date_calc"

    def test_brace_in_string_value(self):
        """Brace inside a string value must not confuse the parser."""
        text = '<|function_call|>{"name": "x", "arguments": {"expr": "if (a > b) { return a }"}}'
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["name"] == "x"


# ---------------------------------------------------------------------------
# No tool call
# ---------------------------------------------------------------------------

class TestNoToolCall:
    def test_plain_text(self):
        assert _detect("Сегодня 21 мая 2026 года, четверг.") is None

    def test_plain_date_answer(self):
        assert _detect("Сегодня четверг, 21 мая 2026.") is None

    def test_partial_gigachat_tag_no_json(self):
        # Tag present but no JSON after it
        assert _detect("<|function_call|>") is None

    def test_standard_tag_no_braces(self):
        # <tool_call> with plain text (no {…}) — regex requires {
        assert _detect("<tool_call>not json</tool_call>") is None

    def test_wrong_tag(self):
        assert _detect('<function_call>{"name": "x"}</function_call>') is None
        assert _detect('<tool_use>{"name": "x"}</tool_use>') is None

    def test_empty_string(self):
        assert _detect("") is None


# ---------------------------------------------------------------------------
# Standard takes priority when both present
# ---------------------------------------------------------------------------

class TestPriority:
    def test_standard_before_gigachat(self):
        text = (
            '<tool_call>{"name": "first", "arguments": {}}</tool_call> '
            '<|function_call|>{"name": "second", "arguments": {}}'
        )
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["name"] == "first"

    def test_gigachat_only_when_no_standard(self):
        text = 'Пролог. <|function_call|>{"name": "only_one", "arguments": {}}'
        result = _detect(text)
        assert result is not None
        data = json.loads(result)
        assert data["name"] == "only_one"
