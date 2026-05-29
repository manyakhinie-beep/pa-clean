# -*- mode: python ; coding: utf-8 -*-
# PaClean.spec — PyInstaller bundling для macOS arm64.
#
# Запуск:
#   cd packaging/pyinstaller
#   pyinstaller --clean --noconfirm PaClean.spec
#
# Артефакт:
#   dist/PaClean.app   (директория-бандл)
#   dist/PaClean       (.app's COLLECT output)
#
# Минимум 5-10 минут на M3 Max + ~600-800 MB размер бандла.
# Можно ускорить через UPX, но добавляет +30s к запуску → не используем.

import os
import sys
from pathlib import Path

# Путь к корню репозитория (этот spec лежит в packaging/pyinstaller/)
HERE = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821  (SPEC inj. by PyInstaller)
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

block_cipher = None


# ---------------------------------------------------------------------------
# Datas — что вшить рядом с executable как файлы (не код)
# ---------------------------------------------------------------------------
# Формат: (src_path, dest_dir_relative_to_bundle)
datas = [
    # WebUI ассеты — без них index.html не отдастся
    (
        os.path.join(REPO_ROOT, "webui", "dist"),
        "personal_assistant/webui_dist",
    ),
    # Шаблоны Jinja (vault/writer.py использует их)
    (
        os.path.join(REPO_ROOT, "src", "personal_assistant", "templates"),
        "personal_assistant/templates",
    ),
]

# Опционально: souls.md и базовые конфиги, если хотим preset
for opt in ["souls.md", "data/persona.json", "data/gtd_rules.json"]:
    src = os.path.join(REPO_ROOT, opt)
    if os.path.exists(src):
        datas.append((src, os.path.dirname(opt) or "."))


# ---------------------------------------------------------------------------
# Hidden imports — что PyInstaller не находит автоматически
# ---------------------------------------------------------------------------
# Динамические импорты, factory-функции, плагины — попадают сюда.
hiddenimports = [
    # uvicorn protocols (динамически выбираются по конфигу)
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",

    # FastAPI / Starlette plugins
    "fastapi",
    "starlette.middleware.cors",

    # MLX — основной use case
    "mlx",
    "mlx_lm",
    "mlx_lm.sample_utils",
    "mlx_lm.tokenizer_utils",

    # personal_assistant — все подмодули
    "personal_assistant",
    "personal_assistant.mlx_server.server",
    "personal_assistant.mlx_server.tasks.draft_reply",
    "personal_assistant.mlx_server.tasks.summarize",
    "personal_assistant.services.delegate_service",
    "personal_assistant.services.deadline_extractor",
    "personal_assistant.services.date_anchors",
    "personal_assistant.services.tool_prompts",
    "personal_assistant.services.lora_dataset",
    "personal_assistant.services.lora_trainer",
    "personal_assistant.app_launcher",

    # PyWebView Cocoa backend (объект-c bridge через pyobjc)
    "webview",
    "webview.platforms.cocoa",
    "objc",
    "AppKit",
    "WebKit",
    "Foundation",
]


# ---------------------------------------------------------------------------
# Excludes — что НЕ тащить
# ---------------------------------------------------------------------------
# Dev-зависимости (pytest и ко.), test-фреймворки, embedding-стек
# который тяжёлый и не нужен в первом релизе (~250 MB sentence-transformers
# + torch).  Если потом окажется что они нужны — вернём.
excludes = [
    "pytest",
    "pytest_mock",
    "pytest_asyncio",
    "ruff",
    "mypy",
    "coverage",
    "IPython",
    "jupyter",
    "notebook",
    "matplotlib",
    "tkinter",
    # sentence-transformers + torch — вес ~700 MB, не критичны для draft/summarize
    # "torch",
    # "sentence_transformers",
]


# ---------------------------------------------------------------------------
# Analysis — главный этап «что собрать»
# ---------------------------------------------------------------------------
a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[REPO_ROOT, os.path.join(REPO_ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Иконка приложения — генерируется отдельно через scripts/make_icns.sh.
# Если icon.icns ещё не собран, PyInstaller просто пропустит иконку
# (значение None), бандл соберётся с дефолтной серой папкой.
_ICON_PATH = os.path.join(REPO_ROOT, "packaging", "icon.icns")
_ICON = _ICON_PATH if os.path.exists(_ICON_PATH) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaClean",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # см. комментарий вверху: UPX замедляет старт
    console=False,        # GUI app — нет окна терминала
    disable_windowed_traceback=False,
    target_arch="arm64",  # Apple Silicon ONLY (MLX-требование)
    codesign_identity=None,  # ad-hoc подпись применим build-скриптом отдельно
    entitlements_file=None,
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PaClean",
)

app = BUNDLE(
    coll,
    name="PaClean.app",
    icon=_ICON,  # см. _ICON_PATH выше — None если иконка не собрана
    bundle_identifier="com.paclean.assistant",
    info_plist={
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "LSMinimumSystemVersion": "14.0",
        "LSArchitecturePriority": ["arm64"],
        "NSHighResolutionCapable": True,
        # macOS TCC strings — пользователь увидит их в системных
        # диалогах разрешений на автоматизацию Mail/Calendar.
        "NSAppleEventsUsageDescription": (
            "PaClean читает ваши письма и события календаря, "
            "чтобы синхронизировать их в локальный vault и помогать "
            "с почтой и встречами. Все данные остаются на вашем "
            "компьютере."
        ),
        "NSCalendarsUsageDescription": (
            "PaClean читает ваши события календаря для синхронизации "
            "и помощи в планировании."
        ),
    },
)
