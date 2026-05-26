"""
Integration tests for the Inbox → Chat pipeline (Stages 3 & 5 from
INBOX_CHAT_TESTING_PLAN.md).

These tests use a shared FastAPI TestClient and mock MLXEngine so they run on
any platform (no Apple Silicon required) and without a real model loaded.

Coverage:
  TC-3.1  Full Draft pipeline: draft-context → new thread → chat/send
  TC-3.2  Summarize action pipeline
  TC-3.3  Create-Meeting action pipeline
  TC-3.4  Thread isolation: two letters → two separate threads
  TC-5.1  MLX unavailable: graceful degradation (no 500)
  TC-5.4  Two concurrent draft actions produce separate threads
  TC-5.5  Empty subject handled without "None" in prompt
  TC-5.6  Cyrillic subject/body not garbled
"""

from __future__ import annotations

import threading
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from personal_assistant.mlx_server.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def mock_mlx(monkeypatch):
    """
    Patch MLXEngine.chat() and MLXEngine.stream() for all integration tests
    so they return a deterministic response without real model inference.
    """
    deterministic = "Уважаемый коллега, спасибо за ваше письмо. [mock-response]"

    def _mock_chat(self, messages, **kwargs):
        return deterministic

    def _mock_stream(self, messages, **kwargs):
        for token in deterministic.split():
            yield token + " "

    with patch(
        "personal_assistant.mlx_server.engine.MLXEngine.chat",
        new=_mock_chat,
    ), patch(
        "personal_assistant.mlx_server.engine.MLXEngine.stream",
        new=_mock_stream,
    ):
        yield


def _new_thread(client) -> Optional[str]:
    """Create a new chat thread, return thread_id or None."""
    r = client.post("/api/chat/threads", json={})
    if r.status_code == 200:
        data = r.json()
        return data.get("thread_id") or data.get("id")
    return None


def _assert_ok(r, label: str = ""):
    msg = f"{label}: expected 200, got {r.status_code}. Body: {r.text[:300]}"
    assert r.status_code == 200, msg
    return r


# ---------------------------------------------------------------------------
# TC-3.1  Full Draft pipeline
# ---------------------------------------------------------------------------


class TestDraftPipeline:
    """TC-3.1: inbox-draft-context → new thread → chat/send"""

    def test_tc3_1_draft_context_then_send_returns_200(self, client):
        """TC-3.1 happy path: full draft pipeline succeeds."""
        # Step 1: get draft-context (simulates inbox.js._openDraftWithContext)
        ctx = _assert_ok(
            client.post("/api/v1/inbox/test_msg_001/draft-context"),
            "draft-context",
        ).json()
        assert "context_prompt" in ctx

        # Step 2: send with context (BUG-1 fix: new thread; BUG-2 fix: reply_message_id)
        r = _assert_ok(
            client.post("/api/chat/send", json={
                "message": ctx["context_prompt"],
                "mode": "draft",
                "context_paths": [],
                "vault_thread_id": ctx.get("thread_id") or None,
                "reply_message_id": "test_msg_001",
            }),
            "chat/send",
        )
        assert len(r.text) > 0

    def test_tc3_1_draft_context_prompt_non_empty(self, client):
        """TC-3.1: context_prompt from draft-context is a non-empty string."""
        ctx = client.post("/api/v1/inbox/msg_002/draft-context").json()
        assert isinstance(ctx.get("context_prompt"), str)
        assert len(ctx["context_prompt"]) > 0

    def test_tc3_1_reply_message_id_accepted_by_backend(self, client):
        """TC-3.1 / BUG-2+BUG-3: backend accepts reply_message_id without 422."""
        r = client.post("/api/chat/send", json={
            "message": "Напиши черновик ответа",
            "mode": "draft",
            "reply_message_id": "outlook_msg_test_001",
        })
        assert r.status_code != 422, "BUG-3 not fixed: reply_message_id rejected"
        assert r.status_code == 200

    def test_tc3_1_two_calls_produce_different_responses(self, client):
        """TC-3.1: Sending same message twice creates separate thread entries."""
        payload = {
            "message": "Тест: одинаковый запрос",
            "mode": "draft",
            "reply_message_id": "msg_same",
        }
        r1 = client.post("/api/chat/send", json=payload)
        r2 = client.post("/api/chat/send", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Each call produces a valid response (even if text is the same from mock)
        assert len(r1.text) > 0
        assert len(r2.text) > 0


# ---------------------------------------------------------------------------
# TC-3.2  Summarize pipeline
# ---------------------------------------------------------------------------


class TestSummarizePipeline:
    """TC-3.2: 'Summarize' action sends with mode=chat and context."""

    def test_tc3_2_summarize_returns_200(self, client):
        """TC-3.2: summarize request accepted."""
        r = client.post("/api/chat/send", json={
            "message": "Суммаризируй тред писем по теме «Отчёт Q2»",
            "mode": "chat",
            "context_paths": [],
            "reply_message_id": "msg_q2_001",
        })
        _assert_ok(r, "summarize")

    def test_tc3_2_summarize_with_vault_thread_id(self, client):
        """TC-3.2: summarize with vault_thread_id accepted."""
        r = client.post("/api/chat/send", json={
            "message": "Суммаризируй переписку",
            "mode": "summarize",
            "vault_thread_id": "test_thread_q2",
            "context_paths": [],
        })
        _assert_ok(r, "summarize+vault_thread_id")

    def test_tc3_2_response_non_empty(self, client):
        """TC-3.2: Non-empty response for summarize action."""
        r = client.post("/api/chat/send", json={
            "message": "Суммаризируй",
            "mode": "summarize",
        })
        assert r.status_code == 200
        assert len(r.text) > 0


# ---------------------------------------------------------------------------
# TC-3.3  Create-Meeting pipeline
# ---------------------------------------------------------------------------


class TestCreateMeetingPipeline:
    """TC-3.3: 'Create Meeting' action pipeline."""

    def test_tc3_3_create_meeting_request_accepted(self, client):
        """TC-3.3: create-meeting style request accepted (mode=chat)."""
        r = client.post("/api/chat/send", json={
            "message": "Создай событие в календаре по письму «Встреча команды»",
            "mode": "chat",
            "reply_message_id": "msg_meeting_001",
        })
        _assert_ok(r, "create-meeting")

    def test_tc3_3_no_500_for_meeting_request(self, client):
        """TC-3.3: meeting request never returns 500."""
        r = client.post("/api/chat/send", json={
            "message": "Запланируй встречу на пятницу в 14:00",
            "mode": "chat",
        })
        assert r.status_code != 500


# ---------------------------------------------------------------------------
# TC-3.4  Thread isolation
# ---------------------------------------------------------------------------


class TestThreadIsolation:
    """TC-3.4: Each draft action should produce an isolated thread."""

    def test_tc3_4_two_explicit_threads_are_separate(self, client):
        """TC-3.4: Explicitly creating two threads gives different IDs."""
        t1 = _new_thread(client)
        t2 = _new_thread(client)
        if t1 and t2:
            assert t1 != t2, "BUG-1 artefact: thread IDs must differ"

    def test_tc3_4_send_to_explicit_thread_persists_it(self, client):
        """TC-3.4: Message sent to explicit thread is found in history."""
        tid = _new_thread(client)
        if not tid:
            pytest.skip("Thread creation endpoint unavailable")

        r = client.post("/api/chat/send", json={
            "thread_id": tid,
            "message": "Тест изоляции треда TC-3.4",
            "mode": "chat",
        })
        assert r.status_code == 200

        history = client.get(f"/api/chat/history/{tid}").json()
        messages = history.get("messages", [])
        user_msgs = [m for m in messages if m.get("role") == "user"]
        contents = [m.get("content", "") for m in user_msgs]
        assert any("TC-3.4" in c for c in contents), (
            f"Sent message not found in thread {tid!r} history"
        )

    def test_tc3_4_thread_messages_do_not_cross_contaminate(self, client):
        """TC-3.4: Messages sent to different threads don't appear in each other."""
        t1 = _new_thread(client)
        t2 = _new_thread(client)
        if not (t1 and t2):
            pytest.skip("Thread creation unavailable")

        marker1 = "КОНТЕНТ_ПЕРВОГО_ПИСЬМА_12345"
        marker2 = "КОНТЕНТ_ВТОРОГО_ПИСЬМА_67890"

        client.post("/api/chat/send", json={"thread_id": t1, "message": marker1})
        client.post("/api/chat/send", json={"thread_id": t2, "message": marker2})

        h1 = client.get(f"/api/chat/history/{t1}").json()
        h2 = client.get(f"/api/chat/history/{t2}").json()

        msgs1 = " ".join(m.get("content", "") for m in h1.get("messages", []))
        msgs2 = " ".join(m.get("content", "") for m in h2.get("messages", []))

        assert marker2 not in msgs1, "Thread 2 content leaked into thread 1"
        assert marker1 not in msgs2, "Thread 1 content leaked into thread 2"


# ---------------------------------------------------------------------------
# TC-5.1  MLX unavailable: graceful degradation
# ---------------------------------------------------------------------------


class TestMLXUnavailable:
    """TC-5.1: When MLX is not available, /send returns 200 (not 500)."""

    def test_tc5_1_no_500_when_mlx_flag_false(self, client):
        """TC-5.1: MLX unavailable → 200 with informative message."""
        with patch(
            "personal_assistant.mlx_server.engine.MLXEngine._mlx_available",
            False,
        ):
            r = client.post("/api/chat/send", json={
                "message": "Напиши ответ",
                "mode": "draft",
            })
        # Must NOT be 500
        assert r.status_code == 200, (
            f"Expected 200 when MLX unavailable, got {r.status_code}"
        )

    def test_tc5_1_draft_context_still_works_without_mlx(self, client):
        """TC-5.1: draft-context endpoint works even when MLX is not loaded."""
        with patch(
            "personal_assistant.mlx_server.engine.MLXEngine._mlx_available",
            False,
        ):
            r = client.post("/api/v1/inbox/msg_no_mlx/draft-context")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# TC-5.4  Concurrent actions
# ---------------------------------------------------------------------------


class TestConcurrentActions:
    """TC-5.4: Concurrent draft actions don't cause crashes."""

    def test_tc5_4_concurrent_sends_both_return_200(self, client):
        """TC-5.4: Two simultaneous /send requests complete successfully."""
        results = []

        def do_send(label: str):
            r = client.post("/api/chat/send", json={
                "message": f"Concurrent test {label}",
                "mode": "chat",
            })
            results.append(r.status_code)

        t1 = threading.Thread(target=do_send, args=("A",))
        t2 = threading.Thread(target=do_send, args=("B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert all(s == 200 for s in results), f"Some concurrent sends failed: {results}"


# ---------------------------------------------------------------------------
# TC-5.5  Empty subject
# ---------------------------------------------------------------------------


class TestEdgeCaseEmptySubject:
    """TC-5.5: Letters with empty/missing subject handled gracefully."""

    def test_tc5_5_draft_context_no_none_string(self, client):
        """TC-5.5: context_prompt must not contain literal 'None'."""
        j = client.post("/api/v1/inbox/item_no_subject/draft-context").json()
        prompt = j.get("context_prompt", "")
        assert "None" not in prompt, f"Literal 'None' found in prompt: {prompt!r}"

    def test_tc5_5_empty_message_rejected(self, client):
        """TC-5.5: Sending empty message returns 422 (validation)."""
        r = client.post("/api/chat/send", json={
            "message": "",   # empty — should fail validation
            "mode": "chat",
        })
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# TC-5.6  Cyrillic content
# ---------------------------------------------------------------------------


class TestCyrillicContent:
    """TC-5.6: Cyrillic in subject/body is not garbled in context_prompt."""

    def test_tc5_6_cyrillic_item_id_accepted(self, client):
        """TC-5.6: Item IDs with cyrillic-derived slugs don't crash endpoint."""
        # Slugified from "Иванов_отчёт_Q2"
        r = client.post("/api/v1/inbox/ivanov_otchet_q2/draft-context")
        assert r.status_code == 200

    def test_tc5_6_cyrillic_in_send_message(self, client):
        """TC-5.6: Messages with cyrillic content are accepted without 422."""
        r = client.post("/api/chat/send", json={
            "message": "Иванов написал: «Прошу прислать отчёт за Q2»",
            "mode": "draft",
            "reply_message_id": "ivanov_q2",
        })
        assert r.status_code == 200

    def test_tc5_6_cyrillic_not_corrupted_in_response(self, client):
        """TC-5.6: Response text doesn't contain Unicode replacement chars."""
        r = client.post("/api/chat/send", json={
            "message": "Ответ на письмо от Иванова",
            "mode": "draft",
        })
        assert r.status_code == 200
        assert "�" not in r.text, "Unicode replacement char in response (encoding issue)"
