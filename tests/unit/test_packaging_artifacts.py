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
        # Entry-point переехал в src/personal_assistant/app_launcher.py,
        # см. test_exec_spec_not_under_packaging_namespace.
        "src/personal_assistant/app_launcher.py",
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


def test_info_plist_requires_macos_14_plus_arm64():
    """mlx 0.30+ публикуется как macosx_14_0_arm64 wheels — на macOS 13
    бандл не запустится.  LSMinimumSystemVersion должно совпадать с
    `--platform` в build_pilot.sh, иначе либо пилоты увидят
    непонятную ошибку запуска, либо pip download отдаст пустой wheelhouse."""
    raw = (_PACKAGING / "Info.plist.template").read_text(encoding="utf-8")
    rendered = raw.replace("__VERSION__", "x")
    data = plistlib.loads(rendered.encode("utf-8"))
    assert data["LSMinimumSystemVersion"] == "14.0"
    assert data["LSArchitecturePriority"] == ["arm64"]


def test_build_script_platform_matches_info_plist():
    """Регрессия: если build_pilot.sh --platform macosx_X_0_arm64
    отстаёт от Info.plist LSMinimumSystemVersion=X, pip download либо
    падает (наш текущий случай) либо тянет несовместимые wheels."""
    build_sh = (_REPO / "scripts" / "build_pilot.sh").read_text(encoding="utf-8")
    plist = (_PACKAGING / "Info.plist.template").read_text(encoding="utf-8")

    m_sh = re.search(r"--platform\s+macosx_(\d+)_0_arm64", build_sh)
    assert m_sh, "build_pilot.sh missing --platform macosx_X_0_arm64"
    sh_major = int(m_sh.group(1))

    data = plistlib.loads(plist.replace("__VERSION__", "x").encode("utf-8"))
    plist_major = int(data["LSMinimumSystemVersion"].split(".")[0])

    assert sh_major == plist_major, (
        f"macOS version mismatch: build_pilot.sh --platform=macosx_{sh_major}_0_arm64, "
        f"Info.plist LSMinimumSystemVersion={plist_major}.0. These MUST agree."
    )


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
        # PYAPP_EXEC_SPEC — единственный способ задать вызов функции в 0.29;
        # PYAPP_EXEC_MODULE без него запустил бы `python -m` без аргументов.
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


def test_pyapp_env_does_not_set_obsolete_distribution_variant():
    """В PyApp 0.29 убрали ``PYAPP_DISTRIBUTION_VARIANT=install_only_stripped``
    как селектор python-build-standalone-варианта; теперь этот env
    интерпретируется как URL источника, build.rs падает с
    «Unable to determine format for distribution source».  Регрессионная
    страховка от случайного возврата строки в pyapp.env."""
    src = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    assert not re.search(
        r"^PYAPP_DISTRIBUTION_VARIANT\s*=\s*install_only",
        src,
        re.MULTILINE,
    ), "PYAPP_DISTRIBUTION_VARIANT=install_only_* is invalid in PyApp 0.29+"


def test_pyapp_entry_module_points_to_real_callable():
    src = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    m = re.search(r"^PYAPP_EXEC_SPEC=(\S+)", src, re.MULTILINE)
    assert m, "PYAPP_EXEC_SPEC missing"
    spec = m.group(1)
    # Должно быть в формате "module:function"
    mod, fn = spec.split(":", 1)
    # Модуль должен лежать внутри установленного wheel-пакета — пробуем
    # обе типичных локации (src/* layout и top-level layout).
    candidates = [
        _REPO / "src" / (mod.replace(".", "/") + ".py"),
        _REPO / (mod.replace(".", "/") + ".py"),
    ]
    entry = next((c for c in candidates if c.exists()), None)
    assert entry is not None, (
        f"entry module {mod!r} not found in any of: {[str(c) for c in candidates]}"
    )
    # Содержит указанную функцию
    src_entry = entry.read_text(encoding="utf-8")
    assert re.search(rf"^def {re.escape(fn)}\(", src_entry, re.MULTILINE), (
        f"entrypoint {mod}:{fn} — function not defined"
    )


def test_exec_spec_not_under_packaging_namespace():
    """``packaging`` — реальный PyPI-пакет (PyPA), который pip ставит как
    транзитив через setuptools/pip-internal.  ``PYAPP_EXEC_SPEC`` под
    этим namespace конфликтует: PyApp импортирует чужой ``packaging``,
    submodule entrypoint не находит, бандл падает с ImportError
    на первом же запуске.

    Регрессия: убеждаемся что spec — в нашем package, не в ``packaging.*``."""
    src = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    m = re.search(r"^PYAPP_EXEC_SPEC=(\S+)", src, re.MULTILINE)
    assert m, "PYAPP_EXEC_SPEC missing"
    spec = m.group(1)
    mod = spec.split(":", 1)[0]
    top_level = mod.split(".", 1)[0]
    assert top_level != "packaging", (
        f"PYAPP_EXEC_SPEC={spec!r} collides with PyPI 'packaging' package. "
        "Move entrypoint into personal_assistant.* or another namespace owned "
        "by the wheel."
    )


# ----------------------------------------------------------------------
# entrypoint.py — базовый smoke на импорт без MLX
# ----------------------------------------------------------------------


def test_entrypoint_main_is_callable():
    """Импортируем app_launcher и проверяем что main существует.
    Полный запуск не делаем — он стартует uvicorn."""
    import importlib.util

    launcher_path = _REPO / "src" / "personal_assistant" / "app_launcher.py"
    spec = importlib.util.spec_from_file_location(
        "_test_app_launcher",
        launcher_path,
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


def test_workflow_smoke_step_avoids_gnu_timeout():
    """``timeout(1)`` отсутствует на macOS-раннерах без brew coreutils.
    Использовать его в shell-степе — гарантия 127 exit code и провала
    smoke-test независимо от того, работает бандл или нет."""
    src = (_REPO / ".github" / "workflows" / "build-pilot.yml").read_text(encoding="utf-8")
    # Не должно быть вызова `timeout 5s` или `timeout Ns ./dist/...`
    assert "timeout 5s" not in src
    assert "timeout 10s" not in src
    # Должен быть портабельный паттерн: запуск в фоне + sleep + kill
    assert "kill -0" in src, (
        "smoke step must use portable bg+sleep+kill -0 pattern instead of GNU timeout(1)"
    )


def test_workflow_opts_into_node24():
    """GitHub deprecates Node 20 on actions runners 16 Sep 2026.
    Workflow должен явно включить Node 24, иначе после deadline
    actions сломаются молча."""
    src = (_REPO / ".github" / "workflows" / "build-pilot.yml").read_text(encoding="utf-8")
    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24" in src, (
        "workflow must set FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true"
    )


def test_workflow_uses_modern_setup_uv():
    """astral-sh/setup-uv v3 крутится на Node 20 (deprecated).
    v5 — Node-24-ready (декабрь 2025)."""
    src = (_REPO / ".github" / "workflows" / "build-pilot.yml").read_text(encoding="utf-8")
    assert "astral-sh/setup-uv@v3" not in src, (
        "setup-uv@v3 is Node-20-only; bump to @v5"
    )
    assert "astral-sh/setup-uv@v5" in src


# ----------------------------------------------------------------------
# Python version consistency — mlx 0.30+ ships cp313-only wheels.
# Если pyapp.env скажет 3.12, а build_pilot.sh — 3.13 (или наоборот),
# wheelhouse не соберётся.  Закрепляем единое значение во всём
# пайплайне.
# ----------------------------------------------------------------------


def test_pyapp_python_version_matches_build_script():
    pyapp_env = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    build_sh = (_REPO / "scripts" / "build_pilot.sh").read_text(encoding="utf-8")

    m_env = re.search(r"^PYAPP_PYTHON_VERSION=(\S+)", pyapp_env, re.MULTILINE)
    assert m_env, "PYAPP_PYTHON_VERSION not declared"
    env_ver = m_env.group(1).strip()

    m_sh = re.search(r"--python-version\s+(\d+\.\d+)", build_sh)
    assert m_sh, "build_pilot.sh missing --python-version"
    sh_ver = m_sh.group(1)

    assert env_ver == sh_ver, (
        f"Python version mismatch: pyapp.env={env_ver}, build_pilot.sh={sh_ver}. "
        "These must agree or wheelhouse won't match the bootstrap interpreter "
        "(see mlx 0.30+ cp313-only wheels regression in CI)."
    )


def test_python_version_is_recent_enough_for_mlx():
    """mlx 0.30+ публикует wheels только для cp313 — версия в pyapp.env
    должна быть как минимум 3.13."""
    pyapp_env = (_PACKAGING / "pyapp.env").read_text(encoding="utf-8")
    m = re.search(r"^PYAPP_PYTHON_VERSION=(\d+)\.(\d+)", pyapp_env, re.MULTILINE)
    assert m, "PYAPP_PYTHON_VERSION missing or malformed"
    major, minor = int(m.group(1)), int(m.group(2))
    assert (major, minor) >= (3, 13), (
        f"PYAPP_PYTHON_VERSION={major}.{minor} — mlx-lm requires Python 3.13+ "
        "(cp313-only wheels published since mlx 0.30)"
    )
