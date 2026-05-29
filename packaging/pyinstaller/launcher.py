"""
launcher.py — entry-point для PyInstaller-сборки PaClean.

В отличие от PyApp-варианта (``src/personal_assistant/app_launcher.py``),
который открывает системный браузер, эта обёртка показывает **нативное
macOS-окно** через PyWebView (WKWebView под капотом — тот же движок что
у Safari).

Преимущества:
  * Иконка в Dock, Cmd+Q работает правильно, нет лишней вкладки в
    Chrome пользователя.
  * Закрытие окна = выход из приложения.
  * Никакой Rust-обвязки — чистый Python через PyInstaller.

Сценарий:
  1. Стартуем FastAPI в фоновом потоке.
  2. Ждём 2 секунды, чтобы сервер успел подняться.
  3. PyWebView открывает окно на http://127.0.0.1:8765.
  4. При закрытии окна — graceful shutdown сервера и выход.

Запускается из PyInstaller-бандла как ``PaClean.app/Contents/MacOS/PaClean``.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional


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
            logging.StreamHandler(sys.stderr),
        ],
    )
    logging.getLogger().info(f"PaClean PyInstaller launcher starting; log → {log_file}")


# ---------------------------------------------------------------------------
# Default config — стандартные macOS-локации, без .env-файла
# ---------------------------------------------------------------------------


def _app_data_dir() -> Path:
    p = Path.home() / "Library" / "Application Support" / "PaClean"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_default_env() -> None:
    """Подготавливаем env-vars ДО импорта personal_assistant.config,
    иначе settings подхватит дефолты, указывающие на dev-локации."""
    base = _app_data_dir()
    vault = Path.home() / "PaCleanVault"
    vault.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("PA_VAULT_PATH", str(vault))
    os.environ.setdefault("PA_LOG_PATH", str(_log_path()))
    os.environ.setdefault(
        "PA_MLX_MODEL_PATH",
        str(Path.home() / ".cache" / "huggingface" / "hub"),
    )
    os.environ.setdefault("PA_CONFIG_DIR", str(base))


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None
_server_should_stop = threading.Event()


def _run_server(host: str, port: int) -> None:
    """Запускаем uvicorn в фоновом потоке.

    Используем low-level Config + Server чтобы можно было корректно
    остановить из главного потока когда пользователь закроет окно.
    """
    try:
        import uvicorn  # noqa: PLC0415
        from personal_assistant.mlx_server.server import app  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logging.getLogger().exception(f"Failed to import server: {exc}")
        return

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Watcher-нить: следит за _server_should_stop и сообщает uvicorn-у
    # что пора выходить.
    def _watch() -> None:
        _server_should_stop.wait()
        server.should_exit = True

    threading.Thread(target=_watch, daemon=True).start()

    try:
        server.run()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger().exception(f"Server crashed: {exc}")


# ---------------------------------------------------------------------------
# Main — PyWebView window
# ---------------------------------------------------------------------------


def main() -> int:
    _configure_logging()
    _ensure_default_env()

    host = os.environ.get("PA_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("PA_SERVER_PORT", "8765"))

    # 1. Стартуем сервер
    global _server_thread
    _server_thread = threading.Thread(
        target=_run_server, args=(host, port), daemon=True,
    )
    _server_thread.start()

    # 2. Ждём пока он действительно ответит (с таймаутом)
    import urllib.request
    import urllib.error
    deadline = time.time() + 15.0
    server_url = f"http://{host}:{port}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{server_url}/", timeout=1) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.3)
    else:
        logging.getLogger().error(
            f"Server did not respond at {server_url} within 15s; aborting"
        )
        return 1

    # 3. Открываем нативное окно WebKit
    try:
        import webview  # noqa: PLC0415
    except ImportError as exc:
        logging.getLogger().exception(
            "pywebview not installed in bundle — bundle is broken: "
            f"{exc}"
        )
        return 1

    logging.getLogger().info(f"Opening native window at {server_url}")
    webview.create_window(
        title="PaClean",
        url=server_url,
        width=1280,
        height=820,
        min_size=(960, 640),
        # resizable=True (default)
    )

    try:
        # gui="cocoa" гарантирует WebKit-bridge на macOS (явно лучше чем
        # автодетект — на CI-средах автодетект иногда падает).
        webview.start(gui="cocoa")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger().exception(f"PyWebView crashed: {exc}")
        return 1
    finally:
        # 4. Окно закрыто → останавливаем сервер
        logging.getLogger().info("Window closed, stopping server")
        _server_should_stop.set()
        if _server_thread is not None:
            _server_thread.join(timeout=3.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
