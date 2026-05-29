"""
Pin-тесты на обвязку иконки приложения.

Сама иконка (icon.icns) — бинарь, генерируется из icon-source.png через
scripts/make_icns.sh.  Здесь проверяем только что build-pipeline
**знает** про иконку и подтянет её если файл присутствует.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------
# Скрипт make_icns.sh существует и executable
# ----------------------------------------------------------------------


def test_make_icns_script_exists_and_executable():
    p = _REPO / "scripts" / "make_icns.sh"
    assert p.exists(), "scripts/make_icns.sh missing"
    mode = p.stat().st_mode
    assert mode & 0o100, "make_icns.sh must be executable (chmod +x)"


@pytest.mark.parametrize(
    "marker",
    [
        "sips",                   # macOS resize tool
        "iconutil -c icns",       # стандартный конвертер
        # Apple обязательные 10 размеров
        "icon_16x16.png",
        "icon_16x16@2x.png",
        "icon_32x32@2x.png",
        "icon_128x128@2x.png",
        "icon_256x256@2x.png",
        "icon_512x512.png",
        "icon_512x512@2x.png",
        # отказ при мелком source
        '"$SRC_WIDTH" -lt 512',
    ],
)
def test_make_icns_contains_step(marker: str):
    src = (_REPO / "scripts" / "make_icns.sh").read_text(encoding="utf-8")
    assert marker in src, f"make_icns.sh missing critical step: {marker!r}"


# ----------------------------------------------------------------------
# PyInstaller spec ссылается на icon.icns
# ----------------------------------------------------------------------


def test_pyinstaller_spec_uses_icon():
    src = (
        _REPO / "packaging" / "pyinstaller" / "PaClean.spec"
    ).read_text(encoding="utf-8")
    # Path к icon
    assert "_ICON_PATH" in src or "icon.icns" in src
    # EXE и BUNDLE оба должны передать icon=_ICON
    assert src.count("icon=_ICON") >= 2, (
        "PaClean.spec must pass icon=_ICON to both EXE and BUNDLE"
    )


# ----------------------------------------------------------------------
# PyApp Info.plist объявляет CFBundleIconFile
# ----------------------------------------------------------------------


def test_pyapp_info_plist_declares_icon_file():
    src = (
        _REPO / "packaging" / "Info.plist.template"
    ).read_text(encoding="utf-8")
    assert "<key>CFBundleIconFile</key>" in src
    assert "<string>icon</string>" in src, (
        "CFBundleIconFile должен ссылаться на icon (без .icns суффикса) — "
        "macOS ищет icon.icns в Contents/Resources/"
    )


# ----------------------------------------------------------------------
# Build-скрипты копируют icon.icns в нужное место
# ----------------------------------------------------------------------


def test_pyapp_build_script_copies_icon():
    src = (_REPO / "scripts" / "build_pilot.sh").read_text(encoding="utf-8")
    assert "icon.icns" in src
    # Должен копировать в Contents/Resources
    assert "Contents/Resources/icon.icns" in src
    # И вызывать make_icns.sh если icns ещё не собран
    assert "make_icns.sh" in src


def test_pyinstaller_build_script_generates_icon_before_pyinstaller():
    src = (_REPO / "scripts" / "build_pyinstaller.sh").read_text(encoding="utf-8")
    assert "icon.icns" in src
    assert "make_icns.sh" in src
    # Генерация иконки должна быть ДО запуска pyinstaller
    icon_pos = src.find("make_icns.sh")
    pyi_pos = src.find("pyinstaller --clean")
    assert 0 < icon_pos < pyi_pos, (
        "make_icns.sh должен вызываться раньше pyinstaller — иначе spec не "
        "найдёт icon.icns и бандл выйдет без иконки"
    )
