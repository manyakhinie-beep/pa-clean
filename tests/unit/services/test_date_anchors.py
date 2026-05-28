"""
Unit tests for ``services.date_anchors`` + integration pins:
дефолтные промпты явно требуют использовать опорные даты, а не
вычислять самостоятельно.

Цель — отлавливать главный класс галлюцинаций LLM в one-shot
вызовах (draft / summarize / delegate): когда модель видит в тексте
«через 2 недели» или «до пятницы» и вынуждена вычислять конкретное
число — она часто ошибается.  Сервер вычисляет всё через
``deadline_extractor``/``date_calc`` и подаёт уже разрешённые даты.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personal_assistant.services.date_anchors import build_date_anchor_block


_NOW = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)  # четверг


# ----------------------------------------------------------------------
# build_date_anchor_block
# ----------------------------------------------------------------------


def test_block_contains_today_with_weekday():
    block = build_date_anchor_block(now=_NOW)
    assert "Сегодня:" in block
    assert "2026-05-28" in block
    assert "(четверг)" in block


def test_block_includes_email_date_when_provided():
    block = build_date_anchor_block(
        email_date="2026-05-27T15:00:00+00:00",
        now=_NOW,
    )
    assert "Дата письма:" in block
    assert "2026-05-27" in block
    assert "(среда)" in block


def test_block_includes_extracted_deadline_from_email_text():
    """Когда в тексте есть «через 2 недели» — extractor решает дату
    относительно даты письма, и эта дата попадает в блок."""
    block = build_date_anchor_block(
        email_date="2026-05-27T10:00:00+00:00",
        email_text="Прошу подготовить материалы через 2 недели.",
        now=_NOW,
    )
    assert "Извлечённый срок:" in block
    assert "2026-06-10" in block  # 27.05 + 14 дней


def test_block_no_deadline_when_email_text_has_no_dates():
    block = build_date_anchor_block(
        email_date="2026-05-27T10:00:00+00:00",
        email_text="Привет, как дела? Просто здороваюсь.",
        now=_NOW,
    )
    assert "Извлечённый срок:" not in block


def test_block_warns_against_inventing_dates():
    """Текст-инструкция модели должен явно запрещать выдумывать даты."""
    block = build_date_anchor_block(now=_NOW)
    assert "НЕ выдумывай" in block
    assert "УТОЧНИТЬ" in block


def test_block_handles_missing_email_date_gracefully():
    """Если даты письма нет, блок всё равно валиден — содержит только
    «Сегодня» и инструкцию."""
    block = build_date_anchor_block(now=_NOW)
    assert "Сегодня:" in block
    assert "Дата письма:" not in block
    assert "Извлечённый срок:" not in block


def test_block_ends_with_double_newline():
    """Чтобы при конкатенации с user-prompt не слипалось — блок
    обязательно заканчивается двумя `\\n`."""
    block = build_date_anchor_block(now=_NOW)
    assert block.endswith("\n\n")


def test_invalid_iso_email_date_silently_ignored():
    """Кривая дата письма не должна крашить — блок просто пропускает
    строку «Дата письма» и продолжает."""
    block = build_date_anchor_block(
        email_date="not-a-date",
        email_text="через 2 недели",
        now=_NOW,
    )
    assert "Сегодня:" in block
    assert "Дата письма:" not in block


# ----------------------------------------------------------------------
# Дефолтные промпты упоминают «Опорные даты» — без этого инструкция
# для модели в user-prompt не имеет эффекта.
# ----------------------------------------------------------------------


def test_summarize_prompt_references_anchor_block():
    from personal_assistant.services.tool_prompts import DEFAULT_SUMMARIZE_SYSTEM
    assert "Опорные даты" in DEFAULT_SUMMARIZE_SYSTEM, (
        "DEFAULT_SUMMARIZE_SYSTEM must reference the «Опорные даты» block"
    )


def test_draft_prompt_references_anchor_block():
    from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM
    assert "Опорные даты" in DEFAULT_DRAFT_SYSTEM


def test_delegate_prompt_references_anchor_block():
    from personal_assistant.services.tool_prompts import DEFAULT_DELEGATE_SYSTEM
    assert "Опорные даты" in DEFAULT_DELEGATE_SYSTEM


# ----------------------------------------------------------------------
# Consumers (draft_reply, summarize, delegate_service) импортируют
# build_date_anchor_block — это контракт интеграции.
# ----------------------------------------------------------------------


def test_draft_reply_imports_anchor_builder():
    src = (
        __import__("personal_assistant.mlx_server.tasks.draft_reply", fromlist=["*"])
        .__dict__
    )
    # Импорт прошёл и build_date_anchor_block доступен в модуле
    assert "build_date_anchor_block" in src


def test_summarize_imports_anchor_builder():
    src = (
        __import__("personal_assistant.mlx_server.tasks.summarize", fromlist=["*"])
        .__dict__
    )
    assert "build_date_anchor_block" in src


def test_chat_system_prompt_already_wires_date_calc_tool():
    """Chat-режим (через context_builder) — отдельная архитектура с
    tool calling.  Регрессионная страховка: системный промпт чата
    по-прежнему инструктирует модель использовать date_calc для
    арифметики и НЕ галлюцинировать имена несуществующих инструментов."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[3]
        / "src" / "personal_assistant" / "mlx_server" / "context_builder.py"
    ).read_text(encoding="utf-8")
    assert "date_calc" in src
    assert "НЕ вызывай" in src or "не вызывай" in src.lower()
