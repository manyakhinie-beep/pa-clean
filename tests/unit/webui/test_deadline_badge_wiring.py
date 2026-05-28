"""
Pin-тесты на UI-обвязку deadline-бейджа в inbox.js.

Без node/jest в проекте — но мы можем как минимум убедиться, что:
  1. Хелпер ``_deadlineBadgeHtml`` существует и используется в шаблоне.
  2. SCSS содержит классы ``.ib-deadline-pill--{today,tomorrow,week,...}``.
  3. Бейдж включён в badges-ряд карточки.

Цель — поймать тихую регрессию, когда кто-то удаляет хелпер или
переименовывает класс без обновления SCSS / шаблона.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parents[3]
_INBOX_JS = _PROJECT / "webui" / "frontend" / "js" / "inbox.js"
_INBOX_SCSS = _PROJECT / "webui" / "frontend" / "styles" / "components" / "_inbox.scss"


def test_inbox_js_defines_deadline_badge_helper():
    src = _INBOX_JS.read_text(encoding="utf-8")
    assert "function _deadlineBadgeHtml" in src, (
        "_deadlineBadgeHtml helper missing from inbox.js"
    )


def test_inbox_js_invokes_helper_in_card_template():
    src = _INBOX_JS.read_text(encoding="utf-8")
    assert "_deadlineBadgeHtml(item.deadline)" in src, (
        "Card template must call _deadlineBadgeHtml(item.deadline)"
    )
    assert "${deadlineBadge}" in src, (
        "Card template must embed ${deadlineBadge} alongside other badges"
    )


def test_inbox_js_helper_covers_all_grades():
    """Хелпер должен дифференцировать все 5 уровней срочности: overdue,
    today, tomorrow, week, future — иначе цветовая градация работает
    наполовину.  Класс конкатенируется через ``cls += 'overdue'`` и т. п.
    — ищем именно эти присваивания, а не уже собранную строку."""
    src = _INBOX_JS.read_text(encoding="utf-8")
    base = "let cls = 'ib-deadline-pill ib-deadline-pill--'"
    assert base in src, "Helper must build classname from 'ib-deadline-pill--' base"
    for grade in ("overdue", "today", "tomorrow", "week", "future"):
        assert f"cls += '{grade}'" in src, (
            f"_deadlineBadgeHtml does not assign cls += '{grade}'"
        )


def test_scss_defines_all_deadline_pill_variants():
    src = _INBOX_SCSS.read_text(encoding="utf-8")
    assert ".ib-deadline-pill" in src, "Base .ib-deadline-pill class missing"
    for variant in ("overdue", "today", "tomorrow", "week", "future"):
        assert f"&--{variant}" in src, (
            f"SCSS missing colour variant &--{variant}"
        )


def test_helper_returns_nothing_for_missing_deadline():
    """Регресс-страховка: проверка пустой строки и невалидного ISO
    через парсинг кода хелпера (мы не запускаем JS, а ищем условия
    раннего return)."""
    src = _INBOX_JS.read_text(encoding="utf-8")
    # Базовый guard
    assert "if (!iso || typeof iso !== 'string') return ''" in src, (
        "Helper must early-return on null/empty deadline"
    )
    # Невалидная дата (NaN.getTime())
    assert "isNaN(due.getTime())" in src, (
        "Helper must early-return on unparseable ISO"
    )


def test_cache_buster_bumped_after_inbox_change():
    """app.js должен ссылаться на свежую версию inbox.js — иначе
    пользователи продолжат получать закэшированный модуль без бейджа."""
    app_js = (_PROJECT / "webui" / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    # Должен быть импорт inbox.js с ?v=<новая_версия>
    assert "inbox.js?v=2026052821" in app_js, (
        "app.js cache-buster for inbox.js must be bumped to 2026-05-28T21:00 or later"
    )
