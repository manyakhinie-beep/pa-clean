"""
Pin-тест: каждый дефолтный системный промпт должен содержать прямую
инструкцию «отвечать только на русском языке» — пользователь явно
потребовал её во всех промптах.

Захватывает не только три tool-prompts (summarize / draft / delegate),
но и системные промпты chat / daily-brief / meeting-prep / reports —
если кто-то добавит новый promоt без этой строки, тест упадёт.
"""

from __future__ import annotations

import re


_RU_RULE_RE = re.compile(
    r"[Оо]твечай только на русском",
    re.UNICODE,
)


# ----------------------------------------------------------------------
# Tool prompts (user-editable defaults)
# ----------------------------------------------------------------------


def test_default_summarize_system_requires_russian():
    from personal_assistant.services.tool_prompts import DEFAULT_SUMMARIZE_SYSTEM
    assert _RU_RULE_RE.search(DEFAULT_SUMMARIZE_SYSTEM), (
        "DEFAULT_SUMMARIZE_SYSTEM must explicitly require Russian-only output"
    )


def test_default_draft_system_requires_russian():
    from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM
    assert _RU_RULE_RE.search(DEFAULT_DRAFT_SYSTEM)


def test_default_delegate_system_requires_russian():
    from personal_assistant.services.tool_prompts import DEFAULT_DELEGATE_SYSTEM
    assert _RU_RULE_RE.search(DEFAULT_DELEGATE_SYSTEM)


# ----------------------------------------------------------------------
# Other system prompts compiled at import time
# ----------------------------------------------------------------------


def test_reports_system_prompt_requires_russian():
    from personal_assistant.reports.generator import _SYSTEM_PROMPT
    assert _RU_RULE_RE.search(_SYSTEM_PROMPT)


def test_daily_brief_system_string_requires_russian():
    """``_build_insight`` defines the system string inline — read the
    source to verify the rule is present."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[3]
        / "src" / "personal_assistant" / "services" / "daily_brief_service.py"
    ).read_text(encoding="utf-8")
    # Look in the vicinity of «деловой ассистент. Дай краткий приоритет»
    snippet_start = src.find("Дай краткий приоритет на день")
    assert snippet_start > 0
    nearby = src[snippet_start : snippet_start + 250]
    assert _RU_RULE_RE.search(nearby), (
        "daily_brief_service: краткий приоритет system prompt must require Russian"
    )


def test_meeting_prep_system_string_requires_russian():
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[3]
        / "src" / "personal_assistant" / "services" / "meeting_prep_service.py"
    ).read_text(encoding="utf-8")
    snippet_start = src.find("Готовишь краткий брифинг")
    assert snippet_start > 0
    nearby = src[snippet_start : snippet_start + 400]
    assert _RU_RULE_RE.search(nearby), (
        "meeting_prep_service: брифинг system prompt must require Russian"
    )


# ----------------------------------------------------------------------
# Chat system prompt (build at runtime by context_builder)
# ----------------------------------------------------------------------


def test_chat_system_prompt_includes_russian_only_line():
    """``ContextBuilder._make_system_prompt`` is composed from a parts
    list; verify the Russian-only line is appended."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[3]
        / "src" / "personal_assistant" / "mlx_server" / "context_builder.py"
    ).read_text(encoding="utf-8")
    assert "Отвечай только на русском языке" in src, (
        "context_builder._make_system_prompt must append a Russian-only line"
    )
