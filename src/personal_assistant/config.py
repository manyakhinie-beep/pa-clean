"""
Configuration — environment defaults plus a runtime-editable overlay.

Resolution order (later wins):
  1. Built-in defaults.
  2. Environment variables / ``.env`` (all use the ``PA_`` prefix).
  3. ``data/config.json`` overlay — the runtime store written by the
     "Правила" (Rules) tab. Only the editable AI-tool settings declared in
     :data:`EDITABLE_FIELDS` may live here.

The overlay is what makes settings editable from the UI without a server
restart: :meth:`Settings.update` validates the incoming values, applies them
to the in-memory singleton *immediately*, and persists them atomically to
``config.json``.

New in pa-merge: sync_sources, sync_on_schedule, mail_fetch_attachments,
and the editable AI-tool settings (mlx_top_p, mail_auto_draft,
calendar_check_conflicts, calendar_default_duration, e2e_test_mode).

Note: the summarization system prompt is NOT a config setting — it lives in
``tool_prompts`` (``summarize_system`` in ``vault/.tool_prompts.json``), which
is the single canonical store and is already validated against prompt
injection. The Rules tab edits it via the ``/tool-prompts`` endpoint.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

# Matches a trailing inline comment preceded by one or more spaces, e.g.:
#   PA_FOO=bar   # some note  →  "bar"
#   PA_FOO=      # comment    →  ""
_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def _env(key: str, default: str = "") -> str:
    """Return env var PA_<key>, stripping any trailing inline comment."""
    raw = os.environ.get(f"PA_{key}", default)
    return _INLINE_COMMENT_RE.sub("", raw).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = _env(key, str(default)).lower()
    return val in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Editable AI-tool settings — single source of truth for the "Правила" tab.
#
# This schema drives three things at once:
#   * validation in :meth:`Settings.update`,
#   * the form fields + tooltips rendered by the Rules tab,
#   * the keys allowed in ``data/config.json``.
#
# ``type`` ∈ {"str", "text", "int", "float", "bool"}.
# Numeric fields may declare ``min`` / ``max`` (inclusive).
# ---------------------------------------------------------------------------

EDITABLE_FIELDS: dict[str, dict[str, Any]] = {
    "mlx_model_path": {
        "type": "str",
        "label": "Путь к модели MLX",
        "help": "Локальный путь или HF-репозиторий модели MLX для инференса.",
        "group": "mlx",
    },
    "mlx_temperature": {
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "label": "Температура",
        "help": "Температура сэмплирования (0.0–2.0). Выше — разнообразнее.",
        "group": "mlx",
    },
    "mlx_max_tokens": {
        "type": "int",
        "min": 1,
        "max": 32768,
        "label": "Максимум токенов",
        "help": "Верхняя граница длины ответа модели.",
        "group": "mlx",
    },
    "mlx_top_p": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "label": "Top-p (nucleus)",
        "help": "Nucleus sampling (0.0–1.0). 1.0 — отключено.",
        "group": "mlx",
    },
    "mail_auto_draft": {
        "type": "bool",
        "label": "Автосоздание черновиков",
        "help": "Автоматически создавать черновики ответов на письма.",
        "group": "mail",
    },
    "calendar_check_conflicts": {
        "type": "bool",
        "label": "Проверка конфликтов",
        "help": "Проверять пересечения при создании событий.",
        "group": "calendar",
    },
    "calendar_default_duration": {
        "type": "int",
        "min": 1,
        "max": 1440,
        "label": "Длительность встречи (мин)",
        "help": "Длительность по умолчанию для новых встреч, в минутах.",
        "group": "calendar",
    },
    "e2e_test_mode": {
        "type": "bool",
        "label": "Режим тестирования (без side-effects)",
        "help": "Подавляет реальные побочные эффекты (отправку писем, запись в календарь).",
        "group": "tests",
    },
}


def _coerce_and_validate(name: str, value: Any) -> Any:
    """Coerce *value* to the type declared for *name* and range-check it.

    :raises KeyError: if *name* is not an editable setting.
    :raises ValueError: if the value cannot be coerced or is out of range.
    """
    if name not in EDITABLE_FIELDS:
        raise KeyError(f"Unknown setting: {name!r}")
    spec = EDITABLE_FIELDS[name]
    kind = spec["type"]

    if kind in ("str", "text"):
        return "" if value is None else str(value)

    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    if kind == "int":
        try:
            coerced: Any = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}: expected an integer, got {value!r}")
    elif kind == "float":
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name}: expected a number, got {value!r}")
    else:  # pragma: no cover - guarded by schema
        raise ValueError(f"{name}: unsupported type {kind!r}")

    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and coerced < lo:
        raise ValueError(f"{name}: {coerced} < min {lo}")
    if hi is not None and coerced > hi:
        raise ValueError(f"{name}: {coerced} > max {hi}")
    return coerced


def _resolve_config_path() -> Path:
    """Where the runtime overlay (config.json) lives.

    Overridable via ``PA_CONFIG_PATH``; otherwise ``<project_root>/data/config.json``.
    Project root is derived from this file's location so it is CWD-independent:
    ``src/personal_assistant/config.py`` → parents[2] == project root.
    """
    override = _env("CONFIG_PATH", "")
    if override:
        return Path(override).expanduser()
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "data" / "config.json"


class Settings:
    """Effective configuration: env defaults overlaid with ``config.json``.

    A module-level singleton :data:`settings` is created at import time. Tests
    may construct their own instance with a custom *config_path* to avoid
    touching the repo's ``data/config.json``.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        # ----------------------------------------------------------- Vault
        self.vault_path: Path = Path(
            _env("VAULT_PATH", str(Path.home() / "PersonalAssistantVault"))
        ).expanduser()

        # ----------------------------------------------------- Sync Sources
        # Comma-separated: calendar, mail
        self.sync_sources: str = _env("SYNC_SOURCES", "calendar,mail")
        self.sync_on_schedule: bool = _env_bool("SYNC_ON_SCHEDULE", False)

        # ------------------------------------------------------ Calendar sync
        self.calendar_days_back: int = _env_int("CALENDAR_DAYS_BACK", 30)
        self.calendar_days_forward: int = _env_int("CALENDAR_DAYS_FORWARD", 90)
        self.calendar_names: str = _env("CALENDAR_NAMES", "")
        self.calendar_max_events: int = _env_int("CALENDAR_MAX_EVENTS", 300)
        self.calendar_per_cal_timeout: int = _env_int("CALENDAR_PER_CAL_TIMEOUT", 45)
        self.calendar_fetch_attendees: bool = _env_bool("CALENDAR_FETCH_ATTENDEES", False)

        # --------------------------------------------------------- Mail sync
        self.mail_days_back: int = _env_int("MAIL_DAYS_BACK", 30)
        self.mail_max_messages: int = _env_int("MAIL_MAX_MESSAGES", 100)
        self.mail_per_mbox_timeout: int = _env_int("MAIL_PER_MBOX_TIMEOUT", 45)
        self.mail_fetch_body: bool = _env_bool("MAIL_FETCH_BODY", True) or _env_bool(
            "MAIL_FETCH_SNIPPET", False
        )
        # Fetching recipients (To/CC) costs ~5 extra AppleScript IPC calls
        # per message but is required for reply-all (chat draft button) to
        # restore the full participant list on existing threads. Defaults
        # to True so the WebUI reply flow works out of the box; users with
        # huge inboxes can disable via PA_MAIL_FETCH_RECIPIENTS=false.
        self.mail_fetch_recipients: bool = _env_bool("MAIL_FETCH_RECIPIENTS", True)
        self.mail_fetch_attachments: bool = _env_bool("MAIL_FETCH_ATTACHMENTS", False)
        self.mail_attachments_path: str = _env(
            "MAIL_ATTACHMENTS_PATH",
            str(Path.home() / "PersonalAssistantVault" / "attachments"),
        )

        # ---------------------------------------------------------- Behavior
        self.overwrite: bool = _env_bool("OVERWRITE", False)
        self.log_level: str = _env("LOG_LEVEL", "INFO")
        # User's own email — used to identify "my" outgoing mail (follow-up
        # detection, meeting prep). Read from PA_USER_EMAIL.
        self.user_email: str = _env("USER_EMAIL", "")

        # -------------------------------------------------------------- MLX
        self.mlx_model_path: str = _env("MLX_MODEL_PATH", "")
        self.mlx_max_tokens: int = _env_int("MLX_MAX_TOKENS", 1024)
        self.mlx_temperature: float = _env_float("MLX_TEMPERATURE", 0.3)
        self.mlx_top_p: float = _env_float("MLX_TOP_P", 1.0)
        self.mlx_context_chars: int = _env_int("MLX_CONTEXT_CHARS", 12_000)

        # ----------------------------------------------- Mail AI features
        self.mail_auto_draft: bool = _env_bool("MAIL_AUTO_DRAFT", False)
        # NB: the summarization prompt is owned by tool_prompts (summarize_system),
        # not config — see the module docstring.

        # ------------------------------------------------- Calendar AI features
        self.calendar_check_conflicts: bool = _env_bool("CALENDAR_CHECK_CONFLICTS", True)
        self.calendar_default_duration: int = _env_int("CALENDAR_DEFAULT_DURATION", 60)

        # ------------------------------------------------------------- Tests
        self.e2e_test_mode: bool = _env_bool("E2E_TEST_MODE", False)

        # ------------------------------------------------------------ Server
        self.server_host: str = _env("SERVER_HOST", "127.0.0.1")
        self.server_port: int = _env_int("SERVER_PORT", 8000)

        # --------------------------------------------------------- Scheduler
        self.schedule_enabled: bool = _env_bool("SCHEDULE_ENABLED", False)
        self.schedule_cron: str = _env("SCHEDULE_CRON", "0 9 * * *")

        # ----------------------------------------------------- Classify config
        self.classify_config_path: str = _env("CLASSIFY_CONFIG_PATH", "")

        # ------------------------------------------ Embedding model (Stage M2)
        self.embedding_model: str = _env("EMBEDDING_MODEL", "")
        self.embedding_model_path: str = _env("EMBEDDING_MODEL_PATH", "")
        self.embedding_ssl_verify: bool = _env_bool("EMBEDDING_SSL_VERIFY", True)
        self.hf_token: str = _env("HF_TOKEN", "")

        # ------------------------------------------------- Runtime overlay
        self._config_path: Path = config_path or _resolve_config_path()
        self._apply_overlay()

    # ------------------------------------------------------------------ Overlay

    @property
    def config_path(self) -> Path:
        """Path to the runtime overlay file (``config.json``)."""
        return self._config_path

    def _read_overlay(self) -> dict[str, Any]:
        try:
            with open(self._config_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _apply_overlay(self) -> None:
        """Overlay editable settings from ``config.json`` onto this instance.

        Invalid or unknown keys are ignored so a malformed file never crashes
        the app — defaults simply remain in effect.
        """
        for name, value in self._read_overlay().items():
            if name not in EDITABLE_FIELDS:
                continue
            try:
                setattr(self, name, _coerce_and_validate(name, value))
            except (ValueError, KeyError):
                continue

    def editable_dict(self) -> dict[str, Any]:
        """Return the current values of the editable AI-tool settings."""
        return {name: getattr(self, name) for name in EDITABLE_FIELDS}

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate, apply in-memory, and persist editable settings.

        :param values: subset of :data:`EDITABLE_FIELDS` to change.
        :returns: the full editable settings dict after the update.
        :raises KeyError: on an unknown setting name.
        :raises ValueError: on a value that fails type/range validation.
        """
        # Validate everything before mutating anything (all-or-nothing).
        validated = {name: _coerce_and_validate(name, val) for name, val in values.items()}

        # Apply to the live instance immediately (no restart needed).
        for name, val in validated.items():
            setattr(self, name, val)

        # Persist: merge into the existing overlay, write atomically.
        merged = self._read_overlay()
        merged.update(validated)
        self._write_overlay(merged)
        return self.editable_dict()

    def _write_overlay(self, data: dict[str, Any]) -> None:
        """Atomically write the overlay file (temp file + os.replace)."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._config_path.parent), prefix=".config.", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, self._config_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def reload(self) -> None:
        """Re-read the overlay file and re-apply it onto this instance."""
        self._apply_overlay()

    # --------------------------------------------------------------- Properties

    @property
    def sync_sources_list(self) -> list[str]:
        return [s.strip() for s in self.sync_sources.split(",") if s.strip()]

    @property
    def calendar_names_list(self) -> list[str]:
        return [n.strip() for n in self.calendar_names.split(",") if n.strip()]

    @property
    def classify_config_file(self) -> Path:
        if self.classify_config_path:
            return Path(self.classify_config_path)
        # Derive project root reliably from this file's location:
        #   src/personal_assistant/config.py  →  parents[2] = project root
        # This works regardless of the current working directory (CWD),
        # unlike the previous `.parent × 4` approach which broke in sandbox / CI.
        project_root = Path(__file__).resolve().parents[2]
        default_data = project_root / "data" / "classify.yaml"
        if default_data.exists():
            return default_data
        return self.vault_path / "classify.yaml"

    @property
    def mail_attachments_dir(self) -> Path:
        """Return resolved attachments directory path."""
        return Path(self.mail_attachments_path).expanduser()


settings = Settings()
