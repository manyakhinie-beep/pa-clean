"""
Shared pytest fixtures for pa-merge test suite.

All tests run without:
  - MLX / Apple Silicon
  - Apple Calendar / Mail access
  - A real Outlook installation
  - A real vault on disk (fixtures create temp dirs)
"""

from __future__ import annotations

import os
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from personal_assistant.models import CalendarEvent, Contact, MailMessage

# ---------------------------------------------------------------------------
# Env-var snapshot + clear (runs at conftest IMPORT time, before any test
# module imports ``personal_assistant.config``). This is the only reliable
# way to keep hermetic unit/e2e tests from loading multi-GB MLX/embedding
# models when the developer's shell has ``PA_MLX_MODEL_PATH`` exported (e.g.
# from a prior live-MLX scenario run). Without this, ``Settings.__init__``
# re-populates ``settings.mlx_model_path`` from the env var, and code paths
# that read the env directly bypass the runtime fixture blanking entirely.
#
# Scenario fixtures that need the original value read these snapshots
# (NOT os.environ) — so live tests still work when invoked inline:
#     PA_MLX_MODEL_PATH=/path/ uv run pytest -m "scenario and live and mlx"
# ---------------------------------------------------------------------------
ORIG_PA_MLX_MODEL_PATH = os.environ.pop("PA_MLX_MODEL_PATH", "")
ORIG_PA_EMBEDDING_MODEL = os.environ.pop("PA_EMBEDDING_MODEL", "")
ORIG_PA_EMBEDDING_MODEL_PATH = os.environ.pop("PA_EMBEDDING_MODEL_PATH", "")
ORIG_PA_VAULT_PATH = os.environ.pop("PA_VAULT_PATH", "")
os.environ["PA_PRELOAD_MODEL"] = "0"


# ---------------------------------------------------------------------------
# Test isolation: ignore any developer's local data/config.json overlay
# ---------------------------------------------------------------------------
# The Rules tab writes runtime settings to data/config.json. That file is
# developer-local (gitignored) and must NOT leak into the test suite — e.g.
# a custom calendar_default_duration would break intent-parser tests. Re-init
# the settings singleton from env/built-in defaults, pointing the overlay at a
# path that does not exist.
@pytest.fixture(autouse=True, scope="session")
def _isolate_runtime_config_overlay():
    from personal_assistant import config as _cfg

    _cfg.settings.__init__(config_path=Path("/nonexistent/pa-merge-test-config.json"))
    # Safety: the suite must never touch real Apple Mail / Calendar via
    # AppleScript (it would hang on a TCC prompt or mutate real data). Force
    # side-effect-free mode for the whole session; per-test monkeypatch may
    # still flip it to exercise the non-test-mode branch.
    _cfg.settings.e2e_test_mode = True
    # Never let the server lifespan preload multi-GB MLX weights during tests
    # (a TestClient(app) startup would otherwise blow up RAM). Scenario tests
    # load the model explicitly via their own fixture, not via preload.
    os.environ["PA_PRELOAD_MODEL"] = "0"
    # Blank the model/embedding paths so unit/e2e never lazily load multi-GB
    # weights when an endpoint touches the MLX engine — the engine then returns
    # its "unavailable" response instead. Scenario tests re-set these from
    # PA_MLX_MODEL_PATH / PA_EMBEDDING_MODEL(_PATH) in tests/scenarios/conftest.py.
    _cfg.settings.mlx_model_path = ""
    _cfg.settings.embedding_model = ""
    _cfg.settings.embedding_model_path = ""
    # Point the vault at an empty temp dir so the BM25 index (built at app
    # startup from settings.vault_path) never loads the developer's real,
    # potentially huge, ~/PersonalAssistantVault into memory. Tests that need
    # vault data create their own temp vault and patch settings.vault_path.
    _cfg.settings.vault_path = Path(tempfile.mkdtemp(prefix="pa-test-vault-"))
    yield


# ---------------------------------------------------------------------------
# Automatic marker assignment
# ---------------------------------------------------------------------------
#
# Tests are organised by directory, so we derive markers from each test's path
# instead of decorating 35 files by hand:
#
#   tests/unit/...       -> @pytest.mark.unit
#   tests/e2e/...        -> @pytest.mark.e2e
#   tests/scenarios/...  -> @pytest.mark.scenario
#
# Integration sub-markers are added when the path mentions a subsystem, so that
# expressions like ``-m "scenario and mlx"`` select the right subset:
#
#   path contains "mlx"                       -> @pytest.mark.mlx
#   path contains "mail"/"draft"/"outlook"    -> @pytest.mark.mail
#   path contains "calendar"                  -> @pytest.mark.calendar
#
# Explicit @pytest.mark.<name> decorators still work and simply add to these.

_CATEGORY_BY_DIR = {
    "/tests/unit/": "unit",
    "/tests/e2e/": "e2e",
    "/tests/scenarios/": "scenario",
}

_MAIL_KEYWORDS = ("mail", "draft", "outlook", "inbox")


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Auto-apply category and subsystem markers based on each test's path."""
    for item in items:
        path = str(item.fspath).replace(os.sep, "/")
        lowered = path.lower()

        for needle, marker in _CATEGORY_BY_DIR.items():
            if needle in path:
                item.add_marker(getattr(pytest.mark, marker))
                break

        if "mlx" in lowered:
            item.add_marker(pytest.mark.mlx)
        if any(k in lowered for k in _MAIL_KEYWORDS):
            item.add_marker(pytest.mark.mail)
        if "calendar" in lowered:
            item.add_marker(pytest.mark.calendar)


# ---------------------------------------------------------------------------
# Vault fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Minimal vault with sample .md files for all sections."""
    vault = tmp_path / "vault"

    cal_dir = vault / "calendar" / "2026" / "05"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-15_standup-abc123.md").write_text(
        textwrap.dedent("""\
        ---
        id: "cal_standup_001"
        title: "Standup 15 мая"
        type: calendar-event
        source: "calendar"
        start: "2026-05-15T10:00:00+00:00"
        end: "2026-05-15T10:30:00+00:00"
        tags: [meeting, daily]
        sha256: "aabbcc"
        ---
        Обсудили план на неделю.
        """),
        encoding="utf-8",
    )

    mail_dir = vault / "mail" / "2026" / "05"
    mail_dir.mkdir(parents=True)
    (mail_dir / "2026-05-15_invoice-gh789.md").write_text(
        textwrap.dedent("""\
        ---
        id: "outlook_msg_invoice001"
        title: "Invoice #1042"
        type: mail-message
        source: "outlook"
        date: "2026-05-15T09:00:00+00:00"
        sender: "billing@vendor.com"
        tags: [finance]
        sha256: "ddeeff"
        ---
        Счёт на оплату. Срок: 25.05.2026.
        """),
        encoding="utf-8",
    )

    contacts_dir = vault / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "alice@example.com.md").write_text(
        textwrap.dedent("""\
        ---
        email: alice@example.com
        name: "Alice Smith"
        tags: [vip]
        ---
        """),
        encoding="utf-8",
    )

    return vault


@pytest.fixture
def vault_index(tmp_vault: Path):
    from personal_assistant.mlx_server.vault_index import VaultIndex
    return VaultIndex(vault_path=tmp_vault).load(use_cache=False)


# ---------------------------------------------------------------------------
# Sample Pydantic models
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_event() -> CalendarEvent:
    return CalendarEvent(
        uid="test-uid-001",
        title="Team Standup",
        start=datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 15, 10, 30, tzinfo=timezone.utc),
        location="Zoom",
        notes="Daily sync",
        attendees=["alice@example.com", "bob@example.com"],
        calendar_name="Work",
    )


@pytest.fixture
def sample_mail() -> MailMessage:
    return MailMessage(
        message_id="<msg-001@example.com>",
        subject="Invoice #1042",
        sender_name="Billing Dept",
        sender_email="billing@vendor.com",
        recipients=["me@company.com"],
        date=datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc),
        mailbox="Inbox",
        body="Счёт на оплату за май. ASAP. Сумма: 45 000 ₽.",
        has_attachments=True,
        attachments=["invoice_may_2026.pdf"],
        source="outlook",
    )


@pytest.fixture
def sample_mail_reply(sample_mail) -> MailMessage:
    """A reply to sample_mail — same normalized subject."""
    return MailMessage(
        message_id="<msg-002@example.com>",
        subject="Re: Invoice #1042",
        sender_name="Me",
        sender_email="me@company.com",
        recipients=["billing@vendor.com"],
        date=datetime(2026, 5, 16, 10, 0, tzinfo=timezone.utc),
        mailbox="Sent",
        body="Оплата произведена.",
        has_attachments=False,
        source="outlook",
    )


@pytest.fixture
def sample_contact() -> Contact:
    return Contact(
        email="alice@example.com",
        name="Alice Smith",
        organization="Acme Corp",
        phone="+7 999 123-45-67",
    )


@pytest.fixture
def classify_config() -> dict:
    return {
        "classifiers": {
            "urgency": {
                "urgent": {"keywords": ["asap", "срочно", "deadline"]},
                "important": {"keywords": ["important", "важно"]},
            },
            "category": {
                "finance": {"keywords": ["invoice", "payment", "счёт", "оплата"]},
                "meeting": {"keywords": ["meeting", "встреча", "agenda"]},
                "legal": {"keywords": ["contract", "nda", "договор"]},
            },
        },
    }
