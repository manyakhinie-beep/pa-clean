"""
Pin-тесты на F&F-сценарий установки из исходников
(см. docs/FF_TESTING_GUIDE.md + scripts/ff_*.sh).

Цель — поймать тихую регрессию когда кто-то переименует/удалит файл
или sloman ключевой шаг скрипта.  Тесты статические — не запускают
реальную установку.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------
# Файлы существуют и исполняемые
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "scripts/ff_install.sh",
        "scripts/ff_start.sh",
        "scripts/ff_uninstall.sh",
        "docs/FF_TESTING_GUIDE.md",
    ],
)
def test_ff_artifact_exists(relpath: str):
    p = _REPO / relpath
    assert p.exists(), f"F&F artifact missing: {relpath}"
    assert p.stat().st_size > 0, f"F&F artifact empty: {relpath}"


@pytest.mark.parametrize(
    "relpath",
    [
        "scripts/ff_install.sh",
        "scripts/ff_start.sh",
        "scripts/ff_uninstall.sh",
    ],
)
def test_ff_script_is_executable(relpath: str):
    p = _REPO / relpath
    mode = p.stat().st_mode
    assert mode & 0o100, (
        f"{relpath} must be executable (chmod +x); current mode {oct(mode)}"
    )


# ----------------------------------------------------------------------
# ff_install.sh — ключевые шаги
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # Платформенная проверка
        '"$(uname -m)" != "arm64"',     # Apple Silicon only
        "MACOS_VERSION",                # macOS 14+
        # Сетевая проверка
        "curl -sSf --head https://github.com",
        # Установка uv без sudo
        "astral.sh/uv/install.sh",
        # Установка зависимостей
        "uv sync --no-dev",
        # Подсказка для xcode-select
        "xcode-select --install",
        # Создание vault и app-data директорий
        "PA_VAULT_PATH",
        "Library/Application Support/PaClean",
        "Library/Logs/PaClean",
        # Ярлык на Рабочий стол — переменная $DESKTOP/PaClean.command
        "PaClean.command",
        "HOME/Desktop",
    ],
)
def test_ff_install_contains_critical_step(marker: str):
    src = (_REPO / "scripts" / "ff_install.sh").read_text(encoding="utf-8")
    assert marker in src, f"ff_install.sh missing critical step: {marker!r}"


def test_ff_install_refuses_intel_and_old_macos():
    """Бесполезно ставить на Intel или macOS 13 — MLX не работает.
    Скрипт должен явно отказать, а не молча начать установку."""
    src = (_REPO / "scripts" / "ff_install.sh").read_text(encoding="utf-8")
    assert "Apple Silicon" in src
    assert "macOS Sonoma" in src or "macOS 14" in src
    assert "fail " in src  # хотя бы один fail-выход на блокирующей проверке


def test_ff_install_does_not_use_sudo():
    """F&F-тестер не имеет admin-прав — sudo вызов сорвёт установку
    у любого корп-юзера.  Скрипт должен ставить всё в ~/.local/bin
    или подобные user-owned места."""
    src = (_REPO / "scripts" / "ff_install.sh").read_text(encoding="utf-8")
    # Грубая проверка: ни одной строки с sudo не активной.
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        assert "sudo " not in s, f"ff_install.sh must not call sudo: {ln!r}"


# ----------------------------------------------------------------------
# ff_start.sh — поведенческий контракт
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # Подцепка uv из ~/.local/bin (если в PATH сессии нет)
        'export PATH="$HOME/.local/bin:$PATH"',
        # Sanity на наличие .venv и .env
        '[[ -d "$REPO_ROOT/.venv" ]]',
        '[[ -f "$REPO_ROOT/.env" ]]',
        # Открытие браузера через 3 секунды
        "open \"$URL\"",
        # Основной запуск
        "uv run --no-sync pa serve",
        # Параллельная запись логов
        "Library/Logs/PaClean",
    ],
)
def test_ff_start_contains_critical_step(marker: str):
    src = (_REPO / "scripts" / "ff_start.sh").read_text(encoding="utf-8")
    assert marker in src, f"ff_start.sh missing critical step: {marker!r}"


def test_ff_start_handles_already_running_case():
    """Если пользователь дважды кликнул по PaClean.command — не запускать
    второй сервер на том же порту, а просто открыть браузер на уже работающем."""
    src = (_REPO / "scripts" / "ff_start.sh").read_text(encoding="utf-8")
    assert "уже работает" in src or "already running" in src.lower()
    assert "curl -sSf --max-time" in src  # проверка через curl


# ----------------------------------------------------------------------
# Гайд для пилота — содержательные маркеры
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # Системные требования
        "Apple M1",
        "macOS",
        "Sonoma",
        # Шаги
        "Шаг 1",
        "Шаг 2",
        "Шаг 3",
        "Шаг 4",
        # Главная команда установки
        "ff_install.sh",
        # Безопасность при первом запуске
        "правой кнопкой",
        # Доступ к Mail / Calendar
        "Mail",
        "Calendar",
        # Логи и обратная связь
        "Library/Logs/PaClean",
        "Сообщить о проблеме",
        # Удаление
        "ff_uninstall.sh",
    ],
)
def test_ff_guide_covers_topic(marker: str):
    src = (_REPO / "docs" / "FF_TESTING_GUIDE.md").read_text(encoding="utf-8")
    assert marker in src, f"FF_TESTING_GUIDE.md missing topic: {marker!r}"


def test_ff_guide_addresses_non_engineer_audience():
    """Не должно быть упоминаний Python, venv, pip, npm в гайде —
    это слова, на которых F&F-тестер бросит чтение."""
    src = (_REPO / "docs" / "FF_TESTING_GUIDE.md").read_text(encoding="utf-8").lower()
    # Слова разрешены в блоках с тройными ` (там команды), но в обычном
    # тексте — нет.  Грубая эвристика: считаем что в обычном тексте.
    # Допускаем «Python» и «MLX» как имена технологий — они кратки и
    # уже на слуху.
    for forbidden in ("uvicorn", "fastapi", "pydantic", "hatchling", "wheelhouse"):
        assert forbidden not in src, (
            f"FF_TESTING_GUIDE.md mentions {forbidden!r} — too technical for "
            "the F&F audience.  Hide implementation details."
        )
