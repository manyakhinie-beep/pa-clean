"""
Unit tests for chat_routes._resolve_reply_message_id.

This function is the bridge between vault file IDs (which the WebUI knows
about) and Mail.app's internal numeric message_id (which AppleScript needs
to call ``reply origMsg``).  If it returns None on a real mail file, the
chat draft silently falls back to a NEW outgoing message instead of
threading into the existing conversation — which is exactly the symptom
the user reported ("чат создаёт новое письмо вместо ответа в треде").

Coverage:
  * Well-formed YAML — happy path
  * Lenient parser — legacy run-on YAML (``location:"X"tags: [...]``)
  * Match by ``id`` frontmatter
  * Match by file stem when ``id`` is absent
  * Missing message_id → returns None (so caller falls back gracefully)
  * Non-existent vault item → returns None
  * Vault dir missing → returns None
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _patch_vault(tmp_path: Path):
    return patch(
        "personal_assistant.config.settings.vault_path",
        tmp_path,
    )


def test_resolves_by_id_frontmatter(tmp_path):
    _write(
        tmp_path / "mail" / "msg1.md",
        '---\n'
        'id: msg_q2_001\n'
        'message_id: "42001"\n'
        'subject: "Q2 plan"\n'
        '---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_q2_001") == "42001"


def test_resolves_by_file_stem_when_id_missing(tmp_path):
    _write(
        tmp_path / "mail" / "msg_stem_only.md",
        '---\n'
        'message_id: "42002"\n'
        'subject: "no id field"\n'
        '---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_stem_only") == "42002"


def test_resolves_legacy_runon_yaml(tmp_path):
    # Legacy Jinja trim_blocks bug glued YAML fields onto one line.  Without
    # the lenient parser, yaml.safe_load raises and the resolver returns
    # None — the symptom the user hit ("новое письмо вместо ответа").
    _write(
        tmp_path / "mail" / "msg_legacy.md",
        '---\n'
        'id: msg_legacy\n'
        'message_id: "42003"\n'
        'subject: "ok"location: "Б.38.13"tags: [почта]\n'
        '---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_legacy") == "42003"


def test_returns_none_when_message_id_missing(tmp_path):
    _write(
        tmp_path / "mail" / "msg_no_mid.md",
        '---\nid: msg_no_mid\nsubject: "no message id"\n---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_no_mid") is None


def test_returns_none_for_unknown_id(tmp_path):
    _write(
        tmp_path / "mail" / "msg1.md",
        '---\nid: msg1\nmessage_id: "1"\n---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("nonexistent_id") is None


def test_returns_none_when_vault_missing(tmp_path):
    # No mail/ dir created.
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("anything") is None


def test_strips_whitespace_around_message_id(tmp_path):
    _write(
        tmp_path / "mail" / "msg_ws.md",
        '---\nid: msg_ws\nmessage_id: "  42099  "\n---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_ws") == "42099"


def test_subdir_scan(tmp_path):
    # mail entries are nested under year/month folders in real vaults.
    _write(
        tmp_path / "mail" / "2026" / "05" / "msg_nested.md",
        '---\nid: msg_nested\nmessage_id: "42100"\n---\n\nBody.\n',
    )
    with _patch_vault(tmp_path):
        assert _resolve_reply_message_id("msg_nested") == "42100"
