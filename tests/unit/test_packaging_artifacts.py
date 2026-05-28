"""
Pin-тесты на packaging/ артефакты — гарантируют что Phase 1 файлы
(pyapp.env, Info.plist.template, entrypoint.py, build_pilot.sh)
существуют и базово валидны.

Цель: тихая регрессия (кто-то переименовал файл, удалил env-var,
сломал Info.plist XML) ловится на CI до того как пилот качает
сломанный .dmg.

НЕ запускает реальную сборку — для этого нужен arm64 + Rust.
Только статическая валидация контракта.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PACKAGING = _REPO / "packaging"


# ----------------------------------------------------------------------
# Файлы существуют
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "packaging/README.md",
        "packaging/pyapp.env",
        "packaging/Info.plist.template",
        "packaging/entrypoint.py",
        "scripts/build_pilot.sh",
        ".github/workflows/build-pilot.yml",
    ],
)
def test_packaging_file_exists(relpath: str):
    p = _REPO / relpath
    assert p.exists(), f"Phase 1 artifact missing: {relpath}"
    assert p.stat().st_size > 0, f"Phase 1 artifact is empty: {relpath}"


def test_build_script_is_executable():
    p = _REPO / "scripts" / "build_pilot.sh"
    # Под Windows и Linux биты исполняемости вели бы себя по-разному;
    # тест важен на macOS / GitHub Actions раннере.  Минимум — owner-execute.
    mode = p.stat().st_mode
    assert mode & 0o100, (
        f"scripts/build_pilot.sh must be executable; current mode: {oct(mode)}"
    )


# ----------------------------------------------------------------------
# Info.plist.template — валидный XML после подстановки версии
# ----------------------------------------------------------------------


def test_info_plist_template_parses_after_substitution():
    raw = (_PACKAGING / "Info.plist.template").read_text(encoding="utf-8")
    rendered = raw.replace("__VERSION__", "1.0.0-pilot")
    # plistlib бросит ValueError на битый XML
    data = plistlib.loads(rendered.encode("utf-8"))
    assert data["CFBundleName"] == "PaClean"
    assert data["CFBundleIdentifier"] == "com.paclean.assistant"
    assert data["CFBundleVersion"] == "1.0.0-pilot"


def test_info_plist_requires_macos_13_plus_arm64():
    raw = (_PACKAGING / "Info.plist.template").read_text(encoding="utf-8")
    rendered = raw.replace("__VERSION__", "x")
    data = plistlib.loads(rendered.encode("utf-8"))
    assert data["LSMinimumSystemVersion"] == "13.0"
    assert data["LSArchitecturePriority"] == ["arm64"]


def test_info_plist_explains_apple_events_usage():
    """TCC требует понятного описания для пользователя — иначе диалог
    разрешения выглядит подозрительно и его отклоняют."""
    raw = (_PACKAGING / "Info.plist.template").read_text(encoding="utf-8")
    data = plistlib.loads(raw.replace("__VERSION__", "x").encode("utf-8"))
    usage = data.get("NSAppleEventsUsageDescription", "")
    assert "PaClean" in usage
    assert "почт" in usage.lower() or "mail" in usage.lower()
    assert "календар" in usage.lower() or "calendar" in usage.lower()
    # Privacy assurance — important for non-engineer testers
    assert "компьютер" in usage.lower() or "локальн" in usage.lower()


# ----------------------------------------------------------------------
# pyapp.env — обязательные env-vars присутствуют
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "PYAPP_PROJECT_NAME",
        "PYAPP_PROJECT_VERSION",
        "PYAPP_EXEC_MODULE",
        "PYAPP_EXEC_SPEC",
        "PYAPP_PYTHON_VERSION",
        "PYAPP_PROJECT_DEPENDENCY_FILE",
        "PYAPP_FULL_ISOLATION",
        "PYAPP_SELF_COMMAND",
    ],
)
def test_pyapp_env_declares_key(key: str):
    src = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    assert re.search(rf"^{re.escape(key)}=", src, re.MULTILINE), (
        f"pyapp.env missing required key: {key}"
    )


def test_pyapp_entry_module_points_to_real_callable():
    src = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    m = re.search(r"^PYAPP_EXEC_SPEC=(\S+)", src, re.MULTILINE)
    assert m, "PYAPP_EXEC_SPEC missing"
    spec = m.group(1)
    # Должно быть в формате "module:function"
    mod, fn = spec.split(":", 1)
    # entrypoint.py существует на диске
    entry = _REPO / (mod.replace(".", "/") + ".py")
    assert entry.exists(), f"entry module not on disk: {entry}"
    # Содержит указанную функцию
    src_entry = entry.read_text(encoding="utf-8")
    assert re.search(rf"^def {re.escape(fn)}\(", src_entry, re.MULTILINE), (
        f"entrypoint {mod}:{fn} — function not defined"
    )


# ----------------------------------------------------------------------
# entrypoint.py — базовый smoke на импорт без MLX
# ----------------------------------------------------------------------


def test_entrypoint_main_is_callable():
    """Импортируем entrypoint и проверяем что main существует.
    Полный запуск не делаем — он стартует uvicorn."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_test_entrypoint",
        _PACKAGING / "entrypoint.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(getattr(mod, "main", None))


# ----------------------------------------------------------------------
# build_pilot.sh — содержит критичные шаги
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        "uv build --wheel",            # шаг 1 — wheel
        "uv export --no-dev",          # шаг 2 — lock
        "uv pip download",             # шаг 3 — wheelhouse
        "cargo install pyapp",         # шаг 4 — launcher
        "codesign --force --deep --sign -",  # ad-hoc подпись
        # bundle структура — путь собирается через $APP_DIR/Contents/MacOS,
        # ищем индивидуальные маркеры (без подразумеваемого $APP_DIR/)
        "Contents/MacOS",
        "Contents/Info.plist",
        "Info.plist.template",         # подстановка версии
    ],
)
def test_build_script_contains_step(marker: str):
    src = (_REPO / "scripts" / "build_pilot.sh").read_text(encoding="utf-8")
    assert marker in src, f"build_pilot.sh missing critical step: {marker!r}"


def test_build_script_refuses_non_arm64():
    src = (_REPO / "scripts" / "build_pilot.sh").read_text(encoding="utf-8")
    # Без Apple Silicon бандл бесполезен (MLX не работает) — скрипт должен
    # явно отказаться, а не молча собирать сломанный .app.
    assert "arm64" in src
    assert "fail" in src


# ----------------------------------------------------------------------
# GitHub Actions workflow — собирается на macos-14 (arm64)
# ----------------------------------------------------------------------


def test_workflow_uses_arm64_runner():
    src = (_REPO / ".github" / "workflows" / "build-pilot.yml").read_text(encoding="utf-8")
    assert "macos-14" in src, "build-pilot workflow must run on macos-14 (arm64)"
    # macos-13 = Intel — не подходит, MLX не соберётся
    assert "macos-13\n" not in src and "macos-12" not in src


def test_workflow_runs_smoke_launch():
    """Без smoke-test после сборки регрессия (бандл собрался, но не
    стартует) проходит на CI как зелёная — это плохо.  Workflow должен
    хотя бы попытаться запустить executable."""
    src = (_REPO / ".github" / "workflows" / "build-pilot.yml").read_text(encoding="utf-8")
    assert "MacOS/PaClean" in src
    assert "Smoke" in src or "smoke" in src
