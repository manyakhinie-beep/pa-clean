"""
Pin-тесты на PyInstaller+PyWebView сборочный путь.

Запуск реальной сборки требует macOS + PyInstaller + 10 минут — это
работа CI, не unit-теста.  Здесь только статическая валидация
spec/launcher/build-script/workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_PYI = _REPO / "packaging" / "pyinstaller"


# ----------------------------------------------------------------------
# Файлы существуют
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "packaging/pyinstaller/launcher.py",
        "packaging/pyinstaller/PaClean.spec",
        "scripts/build_pyinstaller.sh",
        ".github/workflows/build-pyinstaller.yml",
    ],
)
def test_pyinstaller_artifact_exists(relpath: str):
    p = _REPO / relpath
    assert p.exists(), f"PyInstaller path artifact missing: {relpath}"
    assert p.stat().st_size > 0


def test_build_script_is_executable():
    p = _REPO / "scripts" / "build_pyinstaller.sh"
    mode = p.stat().st_mode
    assert mode & 0o100, f"chmod +x missing on {p}"


# ----------------------------------------------------------------------
# launcher.py — поведенческий контракт
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # PyWebView нативное окно (а не системный браузер)
        "import webview",
        "webview.create_window",
        "webview.start",
        # gui="cocoa" явно — иначе на CI автодетект ломается
        '"cocoa"',
        # FastAPI в фоновом потоке, не блокирующий main
        "threading.Thread",
        "uvicorn.Server",
        # Graceful shutdown по закрытию окна
        "server.should_exit",
        # Логи в стандартное macOS-место
        "Library/Logs/PaClean",
        # Vault / config / env defaults
        "PA_VAULT_PATH",
        'Path.home() / "Library" / "Application Support" / "PaClean"',
    ],
)
def test_launcher_has_critical_step(marker: str):
    src = (_PYI / "launcher.py").read_text(encoding="utf-8")
    assert marker in src, f"launcher.py missing critical piece: {marker!r}"


def test_launcher_waits_for_server_before_opening_window():
    """Если PyWebView откроет окно до того как FastAPI ответит — WKWebView
    покажет «cannot connect» вместо UI.  Должен быть polling/wait."""
    src = (_PYI / "launcher.py").read_text(encoding="utf-8")
    assert "urllib.request" in src
    assert "deadline" in src or "for _ in range" in src or "while" in src


# ----------------------------------------------------------------------
# PaClean.spec — структура и обязательные блоки
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # Главные PyInstaller-этапы
        "Analysis(",
        "PYZ(",
        "EXE(",
        "COLLECT(",
        "BUNDLE(",
        # arm64-only
        'target_arch="arm64"',
        # Не console-приложение (GUI)
        "console=False",
        # Info.plist — TCC strings для Mail/Calendar
        "NSAppleEventsUsageDescription",
        "LSMinimumSystemVersion",
        '"14.0"',
        # PyWebView + Cocoa в hidden imports
        '"webview"',
        '"WebKit"',
        # WebUI ассеты вшиты
        "webui_dist",
        # personal_assistant модули в hidden imports
        '"personal_assistant.app_launcher"',
        '"personal_assistant.mlx_server.server"',
    ],
)
def test_spec_contains_marker(marker: str):
    src = (_PYI / "PaClean.spec").read_text(encoding="utf-8")
    assert marker in src, f"PaClean.spec missing: {marker!r}"


def test_spec_excludes_dev_dependencies():
    """pytest/ruff/mypy НЕ должны попадать в production-бандл —
    +30-50 MB веса без пользы."""
    src = (_PYI / "PaClean.spec").read_text(encoding="utf-8")
    for dev in ("pytest", "ruff", "mypy"):
        assert f'"{dev}"' in src, f"spec must exclude {dev}"


# ----------------------------------------------------------------------
# build_pyinstaller.sh — шаги
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        # Платформа: проверка arm64 через uname -m
        '"$(uname -m)" == "arm64"',
        "Apple Silicon",
        # WebUI guard
        "webui/dist/index.html",
        # uv venv build-окружение
        "uv venv --python 3.13",
        # PyInstaller + PyWebView + pyobjc
        "pyinstaller>=6.10",
        "pywebview>=5.0",
        "pyobjc-framework-WebKit",
        # Сам PyInstaller вызов
        "pyinstaller --clean --noconfirm PaClean.spec",
        # ad-hoc подпись (без Developer ID)
        "codesign --force --deep --sign -",
        # Финальный location
        "dist/PaClean.app",
    ],
)
def test_build_script_contains(marker: str):
    src = (_REPO / "scripts" / "build_pyinstaller.sh").read_text(encoding="utf-8")
    assert marker in src, f"build_pyinstaller.sh missing: {marker!r}"


def test_build_script_does_not_require_rust():
    """Главное преимущество PyInstaller path-а — отсутствие Rust.
    Если кто-то добавит cargo/rustup — мы потеряли смысл этой ветки."""
    src = (_REPO / "scripts" / "build_pyinstaller.sh").read_text(encoding="utf-8")
    for forbidden in ("cargo install", "rustup", "rustc"):
        for line in src.splitlines():
            if line.strip().startswith("#"):
                continue
            assert forbidden not in line, (
                f"build_pyinstaller.sh must NOT reference Rust toolchain: "
                f"found {forbidden!r}"
            )


# ----------------------------------------------------------------------
# CI workflow
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        "macos-14",                    # arm64 runner
        "astral-sh/setup-uv@v5",       # совр. uv
        "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24",
        "./scripts/build_pyinstaller.sh",
        "kill -0",                     # портабельный smoke без GNU timeout
        "PaClean-pyinstaller-arm64",   # имя артефакта
    ],
)
def test_workflow_contains(marker: str):
    src = (
        _REPO / ".github" / "workflows" / "build-pyinstaller.yml"
    ).read_text(encoding="utf-8")
    assert marker in src, f"build-pyinstaller.yml missing: {marker!r}"
