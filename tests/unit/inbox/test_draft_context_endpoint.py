"""
Unit tests for the Inbox → Chat integration — draft-context endpoint
and related pipeline (TC-1.x from INBOX_CHAT_TESTING_PLAN.md).

All tests run without a vault, without MLX, and without macOS:
  - draft-context returns 200 with required keys
  - context_prompt is non-empty string
  - graceful fallback when item not in vault
  - POST /api/chat/send accepts reply_message_id (BUG-2 / BUG-3 fix)
  - POST /api/chat/send with context_paths works (TC-1.4)
  - POST /api/chat/send without context_paths degrades gracefully (TC-1.5 / GAP-4)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures — reuse the module-scope client from existing e2e suite
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from personal_assistant.mlx_server.server import app
    return TestClient(app, raise_server_exceptions=False)


def _ok(r, expected_status: int = 200):
    assert r.status_code == expected_status, (
        f"Expected {expected_status}, got {r.status_code}: {r.text[:300]}"
    )
    return r.json()


# ---------------------------------------------------------------------------
# TC-1.1  draft-context happy path (any item_id → graceful 200)
# ---------------------------------------------------------------------------


class TestDraftContextEndpoint:
    """TC-1.1 through TC-1.3: /api/v1/inbox/{item_id}/draft-context"""

    def test_tc1_1_returns_200_for_any_item_id(self, client):
        """TC-1.1: 200 even when vault is empty (graceful degradation)."""
        r = client.post("/api/v1/inbox/test_item_abc/draft-context")
        assert r.status_code == 200

    def test_tc1_1_response_has_required_keys(self, client):
        """TC-1.1: All required fields present in the response."""
        j = _ok(client.post("/api/v1/inbox/test_item_abc/draft-context"))
        for key in ("item_id", "subject", "context_prompt", "thread_id"):
            assert key in j, f"Missing key: {key!r}"

    def test_tc1_1_context_prompt_is_non_empty_string(self, client):
        """TC-1.1: context_prompt must be a non-empty string."""
        j = _ok(client.post("/api/v1/inbox/test_item_abc/draft-context"))
        assert isinstance(j["context_prompt"], str)
        assert len(j["context_prompt"]) > 0

    def test_tc1_1_item_id_echoed_in_response(self, client):
        """TC-1.1: item_id in response matches the request path."""
        unique_id = "unique_inbox_item_xyz_987"
        j = _ok(client.post(f"/api/v1/inbox/{unique_id}/draft-context"))
        assert j["item_id"] == unique_id

    def test_tc1_2_missing_item_fallback_no_crash(self, client):
        """TC-1.2: Non-existent item_id returns 200 with fallback context (not 404/500)."""
        r = client.post("/api/v1/inbox/__nonexistent_item_zzz__/draft-context")
        # Endpoint always returns 200 with minimal fallback context
        assert r.status_code == 200
        j = r.json()
        assert "context_prompt" in j

    def test_tc1_3_item_without_vault_path_returns_context(self, client):
        """TC-1.3: When vault not synced, endpoint still returns context_prompt."""
        j = _ok(client.post("/api/v1/inbox/no_vault_path_item/draft-context"))
        assert "context_prompt" in j
        # context_prompt must not contain raw Python None or null
        assert "None" not in j["context_prompt"]
        assert "null" not in j["context_prompt"]

    def test_tc1_3_context_prompt_no_none_for_empty_subject(self, client):
        """TC-1.3: Empty/missing subject doesn't produce 'None' in context."""
        j = _ok(client.post("/api/v1/inbox/item_empty_subj/draft-context"))
        prompt = j.get("context_prompt", "")
        assert "None" not in prompt, f"'None' found in prompt: {prompt!r}"


# ---------------------------------------------------------------------------
# TC-1.4  /api/chat/send with context_paths
# ---------------------------------------------------------------------------


class TestChatSendWithContext:
    """TC-1.4 through TC-1.6: POST /api/chat/send"""

    def _new_thread_id(self, client) -> str:
        """Create a thread and return its ID."""
        r = client.post("/api/chat/threads", json={})
        if r.status_code == 200:
            data = r.json()
            return data.get("thread_id") or data.get("id", "")
        # Fallback: send without thread_id — server creates one
        return ""

    def test_tc1_4_send_with_empty_context_paths_returns_200(self, client):
        """TC-1.4 baseline: /api/chat/send works with empty context_paths."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши ответ на это письмо",
            "mode": "draft",
            "context_paths": [],
        })
        assert r.status_code == 200

    def test_tc1_4_send_response_is_non_empty_string(self, client):
        """TC-1.4: Response text is non-empty (even when MLX not available)."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши ответ",
            "mode": "draft",
            "context_paths": [],
        })
        assert r.status_code == 200
        # Streaming endpoint returns plain text
        assert len(r.text) > 0

    def test_tc1_4_send_with_vault_thread_id_returns_200(self, client):
        """TC-1.4: Sending with vault_thread_id accepted (even if thread not in vault)."""
        r = client.post("/api/chat/send", json={
            "message": "Суммаризируй переписку",
            "mode": "summarize",
            "context_paths": [],
            "vault_thread_id": "test_vault_thread_001",
        })
        assert r.status_code == 200

    def test_tc1_5_send_without_context_paths_graceful_degradation(self, client):
        """TC-1.5 / GAP-4: No context_paths → response still arrives (graceful)."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши ответ на это письмо",
            "mode": "draft",
            # context_paths intentionally omitted
        })
        # Must NOT be 500 or 422
        assert r.status_code == 200

    def test_tc1_6_send_accepts_reply_message_id(self, client):
        """TC-1.6 / BUG-2 + BUG-3 fix: backend accepts reply_message_id without 422."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши ответ",
            "mode": "draft",
            "context_paths": [],
            "reply_message_id": "outlook_msg_abc123",
        })
        # Must NOT return 422 (validation error) after BUG-3 fix
        assert r.status_code != 422, (
            "Backend rejected reply_message_id — BUG-3 not fixed in ChatSendRequest"
        )
        assert r.status_code == 200

    def test_tc1_6_reply_message_id_combined_with_vault_thread_id(self, client):
        """TC-1.6: reply_message_id and vault_thread_id can coexist in one request."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши черновик",
            "mode": "draft",
            "context_paths": [],
            "vault_thread_id": "thread_xyz789",
            "reply_message_id": "msg_001",
        })
        assert r.status_code == 200

    def test_tc1_6_empty_reply_message_id_normalised_to_none(self, client):
        """TC-1.6: Empty string reply_message_id is normalised to null (not error)."""
        r = client.post("/api/chat/send", json={
            "message": "Тест",
            "mode": "chat",
            "reply_message_id": "",  # empty string → should normalise to None
        })
        assert r.status_code == 200

    def test_tc1_7_thread_persisted_after_send(self, client):
        """TC-1.7: After /send, thread appears in /api/chat/threads list."""
        # Send a message and capture the returned thread_id from X-Thread-ID header
        r = client.post("/api/chat/send", json={
            "message": "Привет от тест TC-1.7",
            "mode": "chat",
        })
        assert r.status_code == 200
        tid = r.headers.get("x-thread-id") or r.headers.get("X-Thread-ID")
        if tid:
            threads = client.get("/api/chat/threads").json().get("threads", [])
            ids = [t["id"] for t in threads]
            assert tid in ids, f"Thread {tid!r} not found in list after /send"


# ---------------------------------------------------------------------------
# TC-1.7  build_draft_context service — unit level
# ---------------------------------------------------------------------------


class TestBuildDraftContextService:
    """TC-1.7: build_draft_context() runs standalone without app state."""

    def test_service_runs_without_vault(self):
        """Service returns valid dict when vault_path=None."""
        from personal_assistant.services.draft_context_service import build_draft_context

        result = build_draft_context("standalone_test_item", vault_path=None, my_email="")
        assert isinstance(result, dict)
        assert "context_prompt" in result
        assert "item_id" in result
        assert result["item_id"] == "standalone_test_item"

    def test_service_context_prompt_non_empty(self):
        """build_draft_context always produces a non-empty context_prompt."""
        from personal_assistant.services.draft_context_service import build_draft_context

        result = build_draft_context("item_999", vault_path=None, my_email="test@example.com")
        assert isinstance(result["context_prompt"], str)
        assert len(result["context_prompt"]) > 0

    def test_service_contains_required_keys(self):
        """build_draft_context result has all keys expected by the endpoint."""
        from personal_assistant.services.draft_context_service import build_draft_context

        result = build_draft_context("key_test", vault_path=None, my_email="")
        expected_keys = {
            "item_id", "subject", "sender", "sender_email", "thread_id",
            "thread_messages", "context_prompt", "message_count",
        }
        for k in expected_keys:
            assert k in result, f"Missing key in build_draft_context result: {k!r}"

    def test_service_message_count_is_int(self):
        """message_count must be an integer (not None or string)."""
        from personal_assistant.services.draft_context_service import build_draft_context

        result = build_draft_context("mc_test", vault_path=None, my_email="")
        assert isinstance(result["message_count"], int)

    def test_service_thread_messages_is_list(self):
        """thread_messages must be a list (possibly empty)."""
        from personal_assistant.services.draft_context_service import build_draft_context

        result = build_draft_context("tm_test", vault_path=None, my_email="")
        assert isinstance(result["thread_messages"], list)
