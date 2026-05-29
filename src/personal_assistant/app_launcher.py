"""
app_launcher.py — entry-point модуль для PyApp-бандла PaClean.app.

Раньше жил в ``packaging/entrypoint.py``, но столкнулся с name-collision:
``packaging`` — это реальный PyPI-пакет (PyPA, дёрнут pip-ом),
PyApp ставит его в venv, и ``import packaging.entrypoint`` находил
тот модуль вместо нашего → ``ModuleNotFoundError: No module named
'packaging'`` (на самом деле «no submodule entrypoint» в чужом
пакете).  Переезд в ``personal_assistant`` устраняет collision
и заодно гарантирует что launcher попадает в установленный wheel.

Что делает:
  1. Настраивает env-vars для vault / configs / logs в стандартных
     macOS-локациях (``~/PaCleanVault``, ``~/Library/Application
     Support/PaClean``, ``~/Library/Logs/PaClean``).
  2. Стартует FastAPI-сервер через uvicorn.
  3. Открывает WebUI в браузере по умолчанию.
  4. Держит процесс живым пока сервер не остановится; ловит
     SIGTERM/SIGINT для graceful shutdown по Cmd+Q.

Вызывается PyApp через ``PYAPP_EXEC_SPEC=personal_assistant.app_launcher:main``.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging — пишем в ~/Library/Logs/PaClean/server.log
# ---------------------------------------------------------------------------


def _log_path() -> Path:
    log_dir = Path.home() / "Library" / "Logs" / "PaClean"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "server.log"


def _configure_logging() -> None:
    log_file = _log_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger().info(f"PaClean app_launcher starting; log → {log_file}")


# ---------------------------------------------------------------------------
# Default config: создаём ~/Library/Application Support/PaClean/ и
# инициализируем vault + env при первом запуске
# ---------------------------------------------------------------------------


def _app_data_dir() -> Path:
    """Стандартный location для пользовательских данных macOS-приложения."""
    p = Path.home() / "Library" / "Application Support" / "PaClean"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_default_env() -> None:
    """Если PA_VAULT_PATH не задан — указываем на ~/PaCleanVault/.

    Это критично для пилота: пользователь-не-инженер не должен лезть в .env.
    Все настройки делаются через WebUI → Правила.
    """
    base = _app_data_dir()
    vault = Path.home() / "PaCleanVault"
    vault.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("PA_VAULT_PATH", str(vault))
    os.environ.setdefault("PA_LOG_PATH", str(_log_path()))
    # Веса модели качаются мастером в WebUI; место по умолчанию —
    # стандартный HuggingFace cache в домашней папке.
    os.environ.setdefault(
        "PA_MLX_MODEL_PATH",
        str(Path.home() / ".cache" / "huggingface" / "hub"),
    )
    # Базы конфигов pa-clean (config.json, sync_state.json, rules.json) —
    # внутри app-data-dir чтобы не путаться с git-репозиторием разработчика.
    os.environ.setdefault("PA_CONFIG_DIR", str(base))


# ---------------------------------------------------------------------------
# Open WebUI in browser — с небольшой задержкой пока сервер не поднялся
# ---------------------------------------------------------------------------


def _open_browser_after_delay(host: str, port: int, delay: float = 2.0) -> None:
    def _open() -> None:
        time.sleep(delay)
        url = f"http://{host}:{port}"
        try:
            webbrowser.open(url, new=2)
            logging.getLogger().info(f"Opened browser at {url}")
        except Exception as exc:  # noqa: BLE001
            logging.getLogger().warning(f"Could not open browser: {exc}")

    threading.Thread(target=_open, daemon=True).start()


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main() -> int:
    _configure_logging()
    _ensure_default_env()

    host = os.environ.get("PA_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("PA_SERVER_PORT", "8765"))

    _open_browser_after_delay(host, port, delay=3.0)

    # Импортируем сервер ПОСЛЕ настройки env — settings подхватятся.
    try:
        import uvicorn  # noqa: PLC0415

        from personal_assistant.mlx_server.server import app  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logging.getLogger().exception(f"Failed to import server: {exc}")
        return 1

    # Graceful shutdown по SIGTERM/SIGINT — macOS присылает их когда
    # пользователь закрывает приложение через Cmd+Q.
    def _on_signal(signum: int, _frame) -> None:  # noqa: ANN001
        logging.getLogger().info(f"Received signal {signum}, shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except SystemExit:
        return 0
    except Exception as exc:  # noqa: BLE001
        logging.getLogger().exception(f"Server crashed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
