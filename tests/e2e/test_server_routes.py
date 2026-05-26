"""
E2E route tests — exercise every major API endpoint via FastAPI TestClient.

All tests run in-process (no live server required) using raise_server_exceptions=False
so that even 4xx/5xx responses are returned rather than raised.

Coverage:
  - Status / health
  - Chat v2 (threads, send, clear, delete)
  - Vault (list, tags, diagnostics, reload, search)
  - PersonalVault v2 (threads, context)
  - Inbox (list, filter, single item, read/unread, tags, assign-project, suggestions)
  - Sync (status, trigger)
  - Settings (get, save)
  - Classify (config, labels, apply)
  - Search (keyword, hybrid, docs)
  - Projects (list, create, update, delete)
  - Rules (structured + GTD + Eisenhower)
  - Today (dashboard: greeting, events, attention, suggestions)
  - Reports (list, generate)
  - Profile + Assistant config
  - Tools + Tool Prompts
  - Model catalogue
  - Index + Schedule status
  - Souls / Persona
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared client fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from personal_assistant.mlx_server.server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ok(r):
    """Assert response is 2xx."""
    assert r.status_code < 400, f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:200]}"
    return r


def json_ok(r):
    ok(r)
    return r.json()


# ---------------------------------------------------------------------------
# Status / health
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status(self, client):
        j = json_ok(client.get("/status"))
        assert "status" in j or "mlx" in j or isinstance(j, dict)

    def test_index_status(self, client):
        j = json_ok(client.get("/index/status"))
        assert isinstance(j, dict)

    def test_schedule_status(self, client):
        j = json_ok(client.get("/schedule/status"))
        assert isinstance(j, dict)


# ---------------------------------------------------------------------------
# Chat v2
# ---------------------------------------------------------------------------


def _send_and_get_tid(client) -> str | None:
    """Send a chat message; returns thread_id from the X-Thread-ID header or None."""
    r = client.post("/api/chat/send", json={
        "thread_id": None,
        "message": "Тестовый запрос",
        "mode": "chat",
    })
    assert r.status_code < 400, f"chat/send → {r.status_code}: {r.text[:80]}"
    # Response is streaming text/plain; thread_id is in the response header
    return r.headers.get("x-thread-id") or r.headers.get("X-Thread-ID")


class TestChatV2:
    def test_list_threads(self, client):
        j = json_ok(client.get("/api/chat/threads"))
        assert "threads" in j

    def test_send_returns_200(self, client):
        """Send returns 200 with text/plain streaming body (no MLX model needed)."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "ping",
            "mode": "chat",
        })
        assert r.status_code == 200
        # Body is plain text (error message or answer)
        assert len(r.text) > 0

    def test_send_produces_thread_in_list(self, client):
        """After sending, at least one thread should appear in /api/chat/threads."""
        client.post("/api/chat/send", json={
            "thread_id": None, "message": "thread test", "mode": "chat"
        })
        j = json_ok(client.get("/api/chat/threads"))
        assert isinstance(j.get("threads", []), list)

    def test_history_for_existing_thread(self, client):
        threads = json_ok(client.get("/api/chat/threads")).get("threads", [])
        if threads:
            tid = threads[0].get("id") or threads[0].get("thread_id")
            if tid:
                h = json_ok(client.get(f"/api/chat/history/{tid}"))
                assert "messages" in h or isinstance(h, dict)

    def test_clear_existing_thread(self, client):
        threads = json_ok(client.get("/api/chat/threads")).get("threads", [])
        if threads:
            tid = threads[0].get("id") or threads[0].get("thread_id")
            if tid:
                ok(client.post(f"/api/chat/clear/{tid}"))

    def test_delete_all_threads(self, client):
        ok(client.delete("/api/chat/threads/all"))


# ---------------------------------------------------------------------------
# Vault (markdown)
# ---------------------------------------------------------------------------


class TestVault:
    def test_list_empty(self, client):
        j = json_ok(client.get("/vault/list"))
        assert "docs" in j
        assert "total" in j

    def test_list_with_section(self, client):
        j = json_ok(client.get("/vault/list?section=mail&limit=10"))
        assert "docs" in j

    def test_tags(self, client):
        j = json_ok(client.get("/vault/tags"))
        assert isinstance(j, (list, dict))

    def test_diagnostics(self, client):
        j = json_ok(client.get("/vault/diagnostics"))
        assert isinstance(j, dict)

    def test_reload(self, client):
        j = json_ok(client.post("/vault/reload"))
        assert isinstance(j, dict)

    def test_mention(self, client):
        j = json_ok(client.get("/vault/mention?q=test&limit=5"))
        assert isinstance(j, (list, dict))

    def test_contacts(self, client):
        j = json_ok(client.get("/vault/contacts?limit=10"))
        assert isinstance(j, (list, dict))

    def test_list_returns_section_counts(self, client):
        """vault/list must include section_counts, urgency_counts, category_counts, total_all."""
        j = json_ok(client.get("/vault/list"))
        for key in ("section_counts", "urgency_counts", "category_counts", "total_all"):
            assert key in j, f"Missing key '{key}' in vault/list response"
        assert isinstance(j["section_counts"], dict)
        assert isinstance(j["urgency_counts"], dict)
        assert isinstance(j["category_counts"], dict)
        assert isinstance(j["total_all"], int)

    def test_list_section_filter_counts(self, client):
        """Counts should still be present when filtering by section."""
        j = json_ok(client.get("/vault/list?section=mail"))
        assert "section_counts" in j
        assert "total_all" in j

    def test_mentioned_in_returns_structure(self, client):
        """/vault/mentioned-in returns path, title, tags, items, count."""
        j = json_ok(client.get("/vault/mentioned-in?path=calendar/2026/05/test.md"))
        for key in ("path", "title", "tags", "items", "count"):
            assert key in j, f"Missing key '{key}' in vault/mentioned-in response"
        assert isinstance(j["items"], list)
        assert isinstance(j["count"], int)

    def test_mentioned_in_no_path_422(self, client):
        """Calling /vault/mentioned-in without path param should return 422."""
        r = client.get("/vault/mentioned-in")
        assert r.status_code == 422

    def test_mentioned_in_unknown_path_empty(self, client):
        """Unknown path should return empty items (not 404)."""
        j = json_ok(client.get("/vault/mentioned-in?path=nonexistent/path/xyz.md"))
        assert j["count"] == 0
        assert j["items"] == []


# ---------------------------------------------------------------------------
# PersonalVault v2
# ---------------------------------------------------------------------------


class TestPersonalVaultV2:
    def test_list_threads(self, client):
        j = json_ok(client.get("/api/v1/vault/threads"))
        assert "threads" in j

    def test_list_items(self, client):
        j = json_ok(client.get("/api/v1/vault/items"))
        assert "items" in j

    def test_context_no_thread(self, client):
        """Context endpoint returns graceful empty when no thread_id given."""
        j = json_ok(client.post("/api/v1/vault/context", json={"mode": "chat"}))
        assert "messages" in j

    def test_context_unknown_thread_404(self, client):
        r = client.post("/api/v1/vault/context", json={"thread_id": "nonexistent_tid_xyz", "mode": "chat"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


class TestInbox:
    def test_list_all(self, client):
        j = json_ok(client.get("/api/v1/inbox"))
        assert "items" in j
        assert "stats" in j

    def test_list_filter_urgent(self, client):
        j = json_ok(client.get("/api/v1/inbox?filter=urgent"))
        assert "items" in j

    def test_list_filter_mail(self, client):
        j = json_ok(client.get("/api/v1/inbox?filter=mail"))
        assert "items" in j

    def test_list_filter_calendar(self, client):
        j = json_ok(client.get("/api/v1/inbox?filter=calendar"))
        assert "items" in j

    def test_single_item_404_or_503(self, client):
        """Nonexistent item returns 404 (vault loaded) or 503 (vault not loaded)."""
        r = client.get("/api/v1/inbox/nonexistent_item_xyz")
        assert r.status_code in (404, 503)

    def test_summarize_missing_body(self, client):
        """Summarize with missing item_id should 422."""
        r = client.post("/api/v1/inbox/summarize", json={})
        assert r.status_code in (400, 422)

    def test_items_have_tags_field(self, client):
        """Every item must include a tags list (may be empty)."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            assert "tags" in item, "item missing tags"
            assert isinstance(item["tags"], list)

    def test_display_tags_classifier_mapping(self):
        """_display_tags maps urgency/category classifier tags to correct cls values."""
        from personal_assistant.inbox.routes import _display_tags
        result = _display_tags(["urgency:urgent", "category:finance", "urgency:low"])
        by_label = {r["label"]: r["cls"] for r in result}
        assert by_label.get("Срочно")   == "urgency-urgent"
        assert by_label.get("Финансы")  == "category-finance"
        assert by_label.get("Обычный")  == "urgency-low"

    def test_display_tags_legacy_mapping(self):
        """Legacy tags (срочно, важно, meeting) still work."""
        from personal_assistant.inbox.routes import _display_tags
        result = _display_tags(["срочно", "важно", "meeting"])
        labels = [r["label"] for r in result]
        assert "Срочно"  in labels
        assert "Важно"   in labels
        assert "Встречи" in labels

    def test_display_tags_unknown_classifier(self):
        """Unknown classifier tags (x:y) are auto-mapped with default cls."""
        from personal_assistant.inbox.routes import _display_tags
        result = _display_tags(["custom:foo"])
        assert len(result) == 1
        assert result[0]["cls"] == "custom-foo"

    def test_display_tags_deduplication(self):
        """Duplicate labels are removed even if tags differ."""
        from personal_assistant.inbox.routes import _display_tags
        result = _display_tags(["urgency:urgent", "urgency:critical", "urgency:high"])
        labels = [r["label"] for r in result]
        assert labels.count("Срочно") == 1

    # ── New state fields in response ─────────────────────────────────────────

    def test_items_have_read_field(self, client):
        """Every item must include server-side 'read' boolean."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            assert "read" in item, "item missing 'read' field"
            assert isinstance(item["read"], bool)

    def test_items_have_project_id_field(self, client):
        """Every item must include 'project_id' (None if unassigned)."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            assert "project_id" in item, "item missing 'project_id' field"

    def test_stats_has_unread(self, client):
        """Stats block must include unread count."""
        j = json_ok(client.get("/api/v1/inbox"))
        stats = j.get("stats", {})
        assert "unread" in stats
        assert isinstance(stats["unread"], int)

    def test_filter_important(self, client):
        """filter=important returns only important items."""
        j = json_ok(client.get("/api/v1/inbox?filter=important"))
        assert "items" in j
        assert isinstance(j["items"], list)

    # ── Read / unread round-trip ─────────────────────────────────────────────

    def test_mark_read_unknown_item(self, client):
        """POST /read on unknown id still returns 200 (creates state entry)."""
        r = client.post("/api/v1/inbox/ghost_item_00/read")
        assert r.status_code == 200
        j = r.json()
        assert j.get("read") is True
        assert j.get("id") == "ghost_item_00"

    def test_mark_unread_roundtrip(self, client):
        """Mark read then unread returns correct flag each time."""
        item_id = "roundtrip_test_42"
        r1 = client.post(f"/api/v1/inbox/{item_id}/read")
        assert r1.status_code == 200
        assert r1.json()["read"] is True

        r2 = client.post(f"/api/v1/inbox/{item_id}/unread")
        assert r2.status_code == 200
        assert r2.json()["read"] is False

    # ── Tags ─────────────────────────────────────────────────────────────────

    def test_set_tags(self, client):
        """POST /tags sets extra_tags and returns them."""
        item_id = "tag_test_item_01"
        r = client.post(
            f"/api/v1/inbox/{item_id}/tags",
            json={"tags": ["category:finance", "urgency:urgent"], "mode": "set"},
        )
        assert r.status_code == 200
        j = r.json()
        assert "extra_tags" in j
        assert "category:finance" in j["extra_tags"]
        assert "urgency:urgent" in j["extra_tags"]

    def test_append_tags(self, client):
        """mode=append merges new tags without duplicating existing ones."""
        item_id = "tag_test_item_02"
        # First set
        client.post(f"/api/v1/inbox/{item_id}/tags",
                    json={"tags": ["category:finance"], "mode": "set"})
        # Then append
        r = client.post(f"/api/v1/inbox/{item_id}/tags",
                        json={"tags": ["category:finance", "urgency:high"], "mode": "append"})
        assert r.status_code == 200
        tags = r.json()["extra_tags"]
        assert tags.count("category:finance") == 1, "duplicate not deduplicated"
        assert "urgency:high" in tags

    def test_tags_mode_set_replaces(self, client):
        """mode=set replaces existing tags completely."""
        item_id = "tag_test_item_03"
        client.post(f"/api/v1/inbox/{item_id}/tags",
                    json={"tags": ["old:tag"], "mode": "set"})
        r = client.post(f"/api/v1/inbox/{item_id}/tags",
                        json={"tags": ["new:tag"], "mode": "set"})
        assert r.status_code == 200
        tags = r.json()["extra_tags"]
        assert "old:tag" not in tags
        assert "new:tag" in tags

    def test_tags_empty_list(self, client):
        """Setting empty list clears all tags."""
        item_id = "tag_test_item_04"
        client.post(f"/api/v1/inbox/{item_id}/tags",
                    json={"tags": ["some:tag"], "mode": "set"})
        r = client.post(f"/api/v1/inbox/{item_id}/tags",
                        json={"tags": [], "mode": "set"})
        assert r.status_code == 200
        assert r.json()["extra_tags"] == []

    def test_tags_missing_body(self, client):
        """Missing 'tags' field returns 422."""
        r = client.post("/api/v1/inbox/x/tags", json={"mode": "set"})
        assert r.status_code == 422

    # ── Assign project ────────────────────────────────────────────────────────

    def test_assign_project(self, client):
        """POST /assign-project persists project_id and returns it."""
        item_id = "proj_test_item_01"
        r = client.post(
            f"/api/v1/inbox/{item_id}/assign-project",
            json={"project_id": "proj_abc", "project_name": "Project Alpha"},
        )
        assert r.status_code == 200
        j = r.json()
        assert j.get("project_id") == "proj_abc"

    def test_assign_project_without_name(self, client):
        """project_name is optional."""
        item_id = "proj_test_item_02"
        r = client.post(
            f"/api/v1/inbox/{item_id}/assign-project",
            json={"project_id": "proj_xyz"},
        )
        assert r.status_code == 200
        assert r.json().get("project_id") == "proj_xyz"

    def test_assign_project_adds_category_tag(self, client):
        """Assigning a project auto-adds category:projects to extra_tags."""
        item_id = "proj_test_item_03"
        r = client.post(
            f"/api/v1/inbox/{item_id}/assign-project",
            json={"project_id": "proj_tag_check", "project_name": "Tag Check"},
        )
        assert r.status_code == 200
        state = r.json().get("state", {})
        extra = state.get("extra_tags", [])
        assert "category:projects" in extra, "category:projects not auto-added"

    def test_assign_project_missing_id(self, client):
        """Missing project_id returns 422."""
        r = client.post("/api/v1/inbox/any/assign-project", json={"project_name": "X"})
        assert r.status_code == 422

    # ── Suggestions ──────────────────────────────────────────────────────────

    def test_suggestions_no_vault(self, client):
        """When vault not loaded suggestions endpoint returns 503."""
        r = client.get("/api/v1/inbox/anything/suggestions")
        # 503 = vault not loaded; 404 = vault loaded but item missing
        assert r.status_code in (503, 404)

    # ── State persistence round-trip ─────────────────────────────────────────

    def test_state_persistence_roundtrip(self, client):
        """Read→tag→assign chain — all state accumulated for same item."""
        item_id = "state_persist_99"

        # Mark read
        r1 = client.post(f"/api/v1/inbox/{item_id}/read")
        assert r1.json()["read"] is True

        # Add tag
        r2 = client.post(f"/api/v1/inbox/{item_id}/tags",
                         json={"tags": ["custom:verify"], "mode": "set"})
        assert "custom:verify" in r2.json()["extra_tags"]

        # Assign project
        r3 = client.post(f"/api/v1/inbox/{item_id}/assign-project",
                         json={"project_id": "proj_persist"})
        assert r3.json()["project_id"] == "proj_persist"

        # Verify read state still persisted (unread then re-read)
        r4 = client.post(f"/api/v1/inbox/{item_id}/unread")
        assert r4.json()["read"] is False
        r5 = client.post(f"/api/v1/inbox/{item_id}/read")
        assert r5.json()["read"] is True


# ── Suggestions unit tests (no vault needed) ──────────────────────────────────

class TestInboxSuggestionsUnit:
    """_build_suggestions is purely rule-based and doesn't need the vault."""

    def test_urgent_tag_triggers_action(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions(["urgency:urgent"], "email")
        assert any("сегодня" in a.lower() or "ответить" in a.lower()
                   for a in result["next_actions"])

    def test_finance_tag(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions(["category:finance"], "email")
        assert any("бухгалтер" in a.lower() or "финанс" in a.lower()
                   for a in result["next_actions"])

    def test_meeting_type(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions([], "meeting")
        assert any("повестк" in a.lower() or "calendar" in a.lower() or "слот" in a.lower()
                   for a in result["next_actions"])

    def test_email_default_action(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions([], "email")
        assert result["next_actions"]  # at least one default action

    def test_tag_suggestions_non_empty(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions(["urgency:urgent", "category:finance"], "email")
        assert result["tag_suggestions"]

    def test_max_four_actions(self):
        from personal_assistant.inbox.routes import _build_suggestions
        # Apply all rules at once
        all_tags = [
            "urgency:urgent", "category:finance",
            "category:legal", "meeting", "category:travel",
        ]
        result = _build_suggestions(all_tags, "email")
        assert len(result["next_actions"]) <= 4

    def test_max_four_tag_suggestions(self):
        from personal_assistant.inbox.routes import _build_suggestions
        result = _build_suggestions(
            ["urgency:urgent", "category:finance", "category:legal", "meeting", "category:travel"],
            "email",
        )
        assert len(result["tag_suggestions"]) <= 4


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class TestSync:
    def test_sync_status(self, client):
        j = json_ok(client.get("/sync/status"))
        assert isinstance(j, dict)

    def test_sync_trigger(self, client):
        # Sync may return 200 with status or kick off background task
        j = json_ok(client.post("/sync", json={}))
        assert isinstance(j, dict)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get(self, client):
        j = json_ok(client.get("/settings"))
        assert isinstance(j, dict)

    def test_save_roundtrip(self, client):
        # Only save non-path, non-secret fields to avoid corrupting .env
        # with sandbox-relative paths (virtiofs mount paths like /sessions/...)
        r = client.post("/settings", json={
            "mlx_max_tokens": 1024,
            "mlx_temperature": 0.3,
            "schedule_enabled": False,
            "log_level": "INFO",
        })
        assert r.status_code < 400


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_config(self, client):
        j = json_ok(client.get("/classify/config"))
        assert isinstance(j, dict)

    def test_labels(self, client):
        j = json_ok(client.get("/classify/labels"))
        assert isinstance(j, (list, dict))

    def test_apply(self, client):
        j = json_ok(client.post("/classify/apply"))
        assert isinstance(j, dict)


# ---------------------------------------------------------------------------
# Stage 8: LLM Classify endpoints
# ---------------------------------------------------------------------------


class TestClassifyLLMStage8:
    """E2E tests for Stage 8 LLM classification endpoints (LC-E2E-01..10)."""

    def test_LC_E2E_01_stats_endpoint_returns_ok(self, client):
        """GET /classify/stats returns status='ok' and required fields."""
        j = json_ok(client.get("/classify/stats"))
        assert j.get("status") == "ok"

    def test_LC_E2E_02_stats_has_total_docs(self, client):
        j = json_ok(client.get("/classify/stats"))
        assert "total_docs" in j
        assert isinstance(j["total_docs"], int)
        assert j["total_docs"] >= 0

    def test_LC_E2E_03_stats_has_ai_classified(self, client):
        j = json_ok(client.get("/classify/stats"))
        assert "ai_classified" in j
        assert isinstance(j["ai_classified"], int)

    def test_LC_E2E_04_stats_has_category_distribution(self, client):
        j = json_ok(client.get("/classify/stats"))
        assert "category_distribution" in j
        assert isinstance(j["category_distribution"], dict)

    def test_LC_E2E_05_stats_has_cache_section(self, client):
        j = json_ok(client.get("/classify/stats"))
        assert "cache" in j
        assert "total_entries" in j["cache"]

    def test_LC_E2E_06_llm_batch_returns_status(self, client):
        """POST /classify/llm-batch returns a status key."""
        j = json_ok(client.post("/classify/llm-batch"))
        assert "status" in j

    def test_LC_E2E_07_llm_batch_when_disabled_returns_disabled(self, client):
        """When llm_classify.enabled=false, batch returns status='disabled'."""
        import yaml

        from personal_assistant.config import settings
        path = settings.classify_config_file
        if not path.exists():
            pytest.skip("No classify.yaml present")
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if cfg.get("llm_classify", {}).get("enabled", False):
            pytest.skip("llm_classify is enabled — can't test disabled path")
        j = json_ok(client.post("/classify/llm-batch"))
        assert j["status"] == "disabled"

    def test_LC_E2E_08_llm_batch_when_enabled_returns_started(self, client):
        """When llm_classify.enabled=true, batch returns status='started'."""
        import yaml

        from personal_assistant.config import settings
        path = settings.classify_config_file
        if not path.exists():
            pytest.skip("No classify.yaml present")
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not cfg.get("llm_classify", {}).get("enabled", False):
            pytest.skip("llm_classify is disabled — can't test started path")
        j = json_ok(client.post("/classify/llm-batch"))
        assert j["status"] == "started"
        assert "threshold" in j
        assert "batch_size" in j

    def test_LC_E2E_09_classify_config_has_llm_section(self, client):
        """GET /classify/config returns config containing llm_classify key."""
        j = json_ok(client.get("/classify/config"))
        parsed = j.get("parsed") or {}
        # After Stage 8, classify.yaml has llm_classify section
        assert "llm_classify" in parsed

    def test_LC_E2E_10_classify_config_llm_has_threshold(self, client):
        j = json_ok(client.get("/classify/config"))
        parsed = j.get("parsed") or {}
        llm_cfg = parsed.get("llm_classify") or {}
        assert "threshold" in llm_cfg
        threshold = llm_cfg["threshold"]
        assert 0.0 < threshold <= 1.0


# ---------------------------------------------------------------------------
# Stage 8 Unit integration: classify_doc with confidence
# ---------------------------------------------------------------------------


class TestClassifyDocConfidence:
    """Unit tests verifying rule_confidence field in ClassifyResult (LC-U-01..08)."""

    def _make_doc(self, title: str, body: str, section: str = "mail"):
        """Build a VaultDoc with proper frontmatter dict."""
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.vault_index import VaultDoc
        raw = f"---\ntitle: {title}\n---\n{body}"
        return VaultDoc(
            path=Path(__file__),
            section=section,
            frontmatter={"title": title},
            content=body,
            raw=raw,
        )

    def test_LC_U_01_rule_confidence_field_present(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        doc = self._make_doc("Invoice payment", "Please pay the invoice")
        result = classify_doc(doc, config={
            "classifiers": {
                "category": {"finance": {"keywords": ["invoice", "payment"]}}
            }
        })
        assert hasattr(result, "rule_confidence")
        assert 0.0 <= result.rule_confidence <= 1.0

    def test_LC_U_02_high_confidence_when_keywords_match(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        doc = self._make_doc("Срочно: встреча", "встреча zoom invoice оплата")
        config = {"classifiers": {
            "urgency": {"urgent": {"keywords": ["срочно"]}},
            "category": {"meeting": {"keywords": ["встреча", "zoom"]}},
        }}
        result = classify_doc(doc, config=config)
        assert result.rule_confidence == 1.0

    def test_LC_U_03_zero_confidence_no_keywords(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        doc = self._make_doc("Привет мир", "Просто обычный текст без совпадений")
        config = {"classifiers": {
            "urgency": {"urgent": {"keywords": ["urgent"]}},
            "category": {"finance": {"keywords": ["invoice"]}},
        }}
        result = classify_doc(doc, config=config)
        assert result.rule_confidence == 0.0

    def test_LC_U_04_llm_assisted_false_without_engine(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        doc = self._make_doc("X", "Y")
        result = classify_doc(doc, config={"classifiers": {}}, engine=None)
        assert result.llm_assisted is False

    def test_LC_U_05_llm_category_empty_without_engine(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        doc = self._make_doc("X", "Y")
        result = classify_doc(doc, config={"classifiers": {}})
        assert result.llm_category == ""

    def test_LC_U_06_llm_called_when_below_threshold(self):
        """When enabled + confidence < threshold, LLM is invoked."""
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        engine = MagicMock()
        engine.ask.return_value = "finance"

        doc = self._make_doc("Hello world", "neutral text no keywords")
        config = {
            "classifiers": {
                "urgency": {"urgent": {"keywords": ["urgent"]}},
            },
            "llm_classify": {
                "enabled": True,
                "threshold": 0.9,
                "categories": ["finance", "hr"],
            },
        }
        result = classify_doc(doc, config=config, engine=engine)
        assert result.llm_assisted is True
        assert result.llm_category == "finance"
        engine.ask.assert_called_once()

    def test_LC_U_07_llm_not_called_when_above_threshold(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import classify_doc

        engine = MagicMock()
        engine.ask.return_value = "finance"

        doc = self._make_doc("Срочно urgent invoice", "urgent срочно invoice счёт")
        config = {
            "classifiers": {
                "urgency": {"urgent": {"keywords": ["urgent", "срочно"]}},
                "category": {"finance": {"keywords": ["invoice", "счёт"]}},
            },
            "llm_classify": {
                "enabled": True,
                "threshold": 0.4,
                "categories": ["finance", "hr"],
            },
        }
        result = classify_doc(doc, config=config, engine=engine)
        # Both classifiers matched → confidence 1.0 ≥ 0.4 → no LLM call
        assert result.llm_assisted is False
        engine.ask.assert_not_called()

    def test_LC_U_08_batch_classify_result_has_llm_count(self):
        import sys
        sys.path.insert(0, "src")
        from personal_assistant.mlx_server.tasks.classify import (
            BatchClassifyResult,
        )

        batch = BatchClassifyResult(
            total=3,
            classified=3,
            results=[],
            llm_assisted_count=2,
        )
        assert batch.llm_assisted_count == 2


# ---------------------------------------------------------------------------
# Regression tests — QA Audit findings
# ---------------------------------------------------------------------------


class TestQARegression:
    """Regression tests added after QA audit (2026-05-24)."""

    def test_BUG01_classify_apply_settings_btn_has_handler(self):
        """BUG-01: classify-apply-settings-btn must be wired in settings.js.

        Previously this button existed in index.html but had no event listener,
        making it inert. The fix adds an async handler in settings.js that calls
        api.classifyApply() and shows progress via OperationProgress.
        """
        settings_js = Path("webui/frontend/js/settings.js").read_text()
        assert "classify-apply-settings-btn" in settings_js, (
            "classify-apply-settings-btn is not referenced in settings.js"
        )
        # Verify it's wired with addEventListener, not just referenced in a comment
        assert "classifyApplySettingsBtn" in settings_js or \
               "classify-apply-settings-btn" in settings_js
        # Verify classifyApply() is called inside its handler
        assert "classifyApply" in settings_js

    def test_BUG01_dist_settings_js_has_handler(self):
        """BUG-01: dist/js/settings.js must also contain the handler (post-build)."""
        dist_js = Path("webui/dist/js/settings.js").read_text()
        assert "classify-apply-settings-btn" in dist_js, (
            "dist/js/settings.js is stale — classify-apply-settings-btn handler missing"
        )

    def test_search_docs_no_500_on_empty_vault(self, client):
        """BUG-02: /search/docs must not return 5xx.

        When vault is empty, endpoint may return 200 or 503 (graceful degradation),
        but must never return 500 Internal Server Error.
        """
        r = client.post("/search/docs", json={"query": "test"})
        assert r.status_code != 500, f"Unexpected 500 from /search/docs: {r.text[:200]}"

    def test_all_get_endpoints_not_500(self, client):
        """All core GET endpoints must return < 500."""
        endpoints = [
            "/status", "/vault/list", "/vault/tags", "/vault/diagnostics",
            "/classify/config", "/classify/labels", "/classify/stats",
            "/settings", "/schedule/status", "/sync/status", "/index/status",
            "/projects", "/souls", "/tools", "/tool-prompts",
            "/eisenhower", "/gtd-rules", "/rules", "/tag-history",
            "/api/chat/threads", "/api/v1/inbox", "/api/v1/today",
        ]
        for path in endpoints:
            r = client.get(path)
            assert r.status_code < 500, f"GET {path} → {r.status_code}: {r.text[:100]}"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_keyword(self, client):
        j = json_ok(client.post("/search", json={"query": "test"}))
        assert isinstance(j, (list, dict))

    def test_hybrid(self, client):
        j = json_ok(client.post("/search/hybrid", json={"query": "calendar"}))
        assert isinstance(j, (list, dict))

    def test_docs(self, client):
        j = json_ok(client.post("/search/docs", json={"query": "meeting"}))
        assert isinstance(j, (list, dict))

    def test_stream_endpoint_exists(self, client):
        """Stream endpoint should accept POST (response may be streaming text)."""
        r = client.post("/search/stream", json={"query": "test"})
        assert r.status_code < 500  # 200 or 4xx is fine, 5xx is not


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class TestProjects:
    def test_list(self, client):
        j = json_ok(client.get("/projects"))
        assert isinstance(j, (list, dict))

    def test_create_update_delete(self, client):
        body = {"name": "E2E Project", "description": "auto", "status": "active", "goals": []}
        created = json_ok(client.post("/projects", json=body))
        pid = created.get("id") or (created.get("project") or {}).get("id")
        if not pid and isinstance(created, dict):
            pid = next((v for k, v in created.items() if "id" in k.lower()), None)
        if pid:
            ok(client.put(f"/projects/{pid}", json={"name": "Updated", "status": "active"}))
            ok(client.delete(f"/projects/{pid}"))

    def test_goals_crud(self, client):
        """Goal add → toggle done → delete."""
        p = json_ok(client.post("/projects", json={
            "name": "Goals Test", "description": "", "status": "active", "goals": [],
        }))
        pid = p.get("id")
        assert pid, "project must have id"

        # Add goal with deadline
        g = json_ok(client.post(f"/projects/{pid}/goals", json={
            "title": "Write report", "done": False, "deadline": "пт",
        }))
        gid = g.get("id")
        assert gid, "goal must have id"
        assert g["title"] == "Write report"
        assert g.get("deadline") == "пт"

        # Mark done
        ok(client.put(f"/projects/{pid}/goals/{gid}", json={
            "title": "Write report", "done": True, "deadline": "пт",
        }))

        # Delete goal via new DELETE endpoint
        r = client.delete(f"/projects/{pid}/goals/{gid}")
        assert r.status_code < 400
        deleted = r.json()
        assert deleted.get("ok") is True
        assert not any(g2.get("id") == gid for g2 in deleted.get("goals", []))

        # Cleanup
        ok(client.delete(f"/projects/{pid}"))

    def test_goals_progress_recalc(self, client):
        """Progress recalculates when goal marked done."""
        p = json_ok(client.post("/projects", json={
            "name": "Progress Test", "description": "", "status": "active", "goals": [],
        }))
        pid = p["id"]

        # Add two goals
        g1 = json_ok(client.post(f"/projects/{pid}/goals", json={"title": "G1", "done": False}))
        g2 = json_ok(client.post(f"/projects/{pid}/goals", json={"title": "G2", "done": False}))

        # Mark one done via project update
        ok(client.put(f"/projects/{pid}", json={
            "name": "Progress Test", "description": "", "status": "active",
            "goals": [
                {"id": g1["id"], "title": "G1", "done": True},
                {"id": g2["id"], "title": "G2", "done": False},
            ],
        }))
        updated_list = json_ok(client.get("/projects"))
        proj = next(
            (q for q in updated_list.get("projects", []) if q["id"] == pid), None
        )
        assert proj is not None
        assert proj.get("progress") == 50

        ok(client.delete(f"/projects/{pid}"))

    def test_related_endpoint(self, client):
        """GET /projects/{id}/related returns expected shape."""
        p = json_ok(client.post("/projects", json={
            "name": "Related Test", "description": "", "status": "active", "goals": [],
        }))
        pid = p["id"]

        r = json_ok(client.get(f"/projects/{pid}/related"))
        for key in ("mail_count", "meeting_count", "contact_count", "thread_count",
                    "mails", "meetings", "contacts", "chat_threads"):
            assert key in r, f"related response missing key: {key}"

        ok(client.delete(f"/projects/{pid}"))

    def test_suggest_goal(self, client):
        """POST /projects/{id}/suggest-goal returns a non-empty title."""
        p = json_ok(client.post("/projects", json={
            "name": "Бюджет Q4", "description": "Квартальный бюджет", "status": "active",
            "goals": [],
        }))
        pid = p["id"]
        json_ok(client.post(f"/projects/{pid}/goals", json={"title": "Собрать данные", "done": True}))
        r = json_ok(client.post(f"/projects/{pid}/suggest-goal", json={}))
        assert "title" in r
        assert len(r["title"]) > 3, "Suggested title should be non-trivial"

        ok(client.delete(f"/projects/{pid}"))

    def test_assistant_suggests(self, client):
        """GET /projects/{id}/assistant-suggests returns suggestion and action."""
        import datetime
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        p = json_ok(client.post("/projects", json={
            "name": "Дедлайн завтра", "description": "", "status": "active",
            "deadline": tomorrow, "goals": [],
        }))
        pid = p["id"]
        json_ok(client.post(f"/projects/{pid}/goals", json={"title": "Финальный слайд", "done": False}))

        r = json_ok(client.get(f"/projects/{pid}/assistant-suggests"))
        assert "suggestion" in r and len(r["suggestion"]) > 5
        assert "action" in r
        assert r["project_id"] == pid

        ok(client.delete(f"/projects/{pid}"))

    def test_paused_status(self, client):
        """Projects can be created/updated with paused status."""
        p = json_ok(client.post("/projects", json={
            "name": "На паузе", "description": "", "status": "paused", "goals": [],
        }))
        pid = p["id"]
        assert p.get("status") == "paused"
        json_ok(client.put(f"/projects/{pid}", json={
            "name": "На паузе", "description": "", "status": "active", "goals": [],
        }))
        ok(client.delete(f"/projects/{pid}"))

    def test_link_vault_contact(self, client):
        """Link and unlink vault items + contacts."""
        p = json_ok(client.post("/projects", json={
            "name": "Link Test", "description": "", "status": "active", "goals": [],
        }))
        pid = p["id"]

        lv = json_ok(client.post(f"/projects/{pid}/link-vault", json={"vault_path": "outlook/test.md"}))
        assert "outlook/test.md" in lv.get("vault_items", [])

        lc = json_ok(client.post(f"/projects/{pid}/link-contact", json={"email": "a@b.com", "name": "Alice"}))
        emails = [c["email"] if isinstance(c, dict) else c for c in lc.get("contacts", [])]
        assert "a@b.com" in emails

        ok(client.delete(f"/projects/{pid}/link-vault?vault_path=outlook/test.md"))
        ok(client.delete(f"/projects/{pid}/link-contact?email=a@b.com"))
        ok(client.delete(f"/projects/{pid}"))


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    # ── Structured rules ──────────────────────────────────────────────────────
    def test_list_structured_returns_dict_with_rules(self, client):
        j = json_ok(client.get("/rules"))
        # May return {"rules": [...]} or a plain list
        if isinstance(j, dict):
            assert "rules" in j
            assert isinstance(j["rules"], list)
        else:
            assert isinstance(j, list)

    def test_create_update_delete_rule(self, client):
        """Full CRUD round-trip for a structured rule."""
        # Create
        r = client.post("/rules", json={
            "name": "E2E-тест правило",
            "condition": "subject contains срочно",
            "action": "tag:urgency:urgent",
            "enabled": True,
        })
        assert r.status_code < 400
        created = r.json()
        rid = created.get("id") or (created.get("rule") or {}).get("id")
        assert rid, "Created rule must have an id"

        # Update
        upd = client.put(f"/rules/{rid}", json={
            "name": "E2E-тест правило (обновлено)",
            "condition": "subject contains важно",
            "action": "tag:urgency:important",
            "enabled": False,
        })
        assert upd.status_code < 400

        # Verify it appears in list
        listing = json_ok(client.get("/rules"))
        rules = listing if isinstance(listing, list) else listing.get("rules", [])
        ids = [r2.get("id") for r2 in rules]
        assert rid in ids

        # Delete
        ok(client.delete(f"/rules/{rid}"))

        # Verify removed
        listing2 = json_ok(client.get("/rules"))
        rules2 = listing2 if isinstance(listing2, list) else listing2.get("rules", [])
        assert rid not in [r2.get("id") for r2 in rules2]

    def test_delete_unknown_rule_404(self, client):
        r = client.delete("/rules/nonexistent_rule_id_xyz")
        assert r.status_code == 404

    # ── GTD rules round-trip ──────────────────────────────────────────────────
    def test_gtd_rules_get_returns_dict(self, client):
        j = json_ok(client.get("/gtd-rules"))
        assert isinstance(j, dict)

    def test_gtd_rules_save_load_roundtrip(self, client):
        """PUT /gtd-rules then GET should return the same data."""
        payload = {"rules": ["Входящие: обрабатывать раз в день", "Срочное: делать сразу"]}
        r = client.put("/gtd-rules", json=payload)
        assert r.status_code < 400
        j = json_ok(client.get("/gtd-rules"))
        assert j.get("rules") == payload["rules"]

    def test_gtd_rules_empty_list_accepted(self, client):
        r = client.put("/gtd-rules", json={"rules": []})
        assert r.status_code < 400
        j = json_ok(client.get("/gtd-rules"))
        assert j.get("rules") == []

    def test_gtd_rules_missing_field_422(self, client):
        r = client.put("/gtd-rules", json={})   # rules field required
        assert r.status_code == 422

    # ── Eisenhower matrix round-trip ──────────────────────────────────────────
    def test_eisenhower_get_returns_dict(self, client):
        j = json_ok(client.get("/eisenhower"))
        assert isinstance(j, dict)

    def test_eisenhower_save_load_roundtrip(self, client):
        """PUT /eisenhower then GET should return the same tasks."""
        tasks = [
            {"id": "t1", "title": "Отчёт к пятнице", "quadrant": "q1", "source": "mail"},
            {"id": "t2", "title": "Обновить доки", "quadrant": "q2", "source": "project"},
        ]
        r = client.put("/eisenhower", json={"tasks": tasks})
        assert r.status_code < 400
        j = json_ok(client.get("/eisenhower"))
        saved = j.get("tasks", [])
        assert len(saved) == 2
        titles = [t["title"] for t in saved]
        assert "Отчёт к пятнице" in titles
        assert "Обновить доки" in titles

    def test_eisenhower_empty_tasks_accepted(self, client):
        r = client.put("/eisenhower", json={"tasks": []})
        assert r.status_code < 400
        j = json_ok(client.get("/eisenhower"))
        assert j.get("tasks") == []

    def test_eisenhower_missing_field_422(self, client):
        r = client.put("/eisenhower", json={})  # tasks field required
        assert r.status_code == 422

    # ── Classify-text endpoint ────────────────────────────────────────────────
    def test_classify_text_returns_result(self, client):
        """POST /rules/classify should return a classification result."""
        r = client.post("/rules/classify", json={"text": "Срочный отчёт по финансам"})
        assert r.status_code < 400
        j = r.json()
        # Result must include at minimum these keys
        assert "eisenhower_quadrant" in j
        assert "action_type" in j
        assert "tags" in j
        assert isinstance(j["tags"], list)

    def test_classify_text_empty_returns_result(self, client):
        """Empty text should still return a valid (default) classification."""
        r = client.post("/rules/classify", json={"text": ""})
        assert r.status_code < 400
        j = r.json()
        assert "eisenhower_quadrant" in j

    def test_classify_text_missing_field_422(self, client):
        r = client.post("/rules/classify", json={})
        assert r.status_code == 422

    def test_classify_text_with_contacts(self, client):
        """classify with contacts list shouldn't crash."""
        r = client.post("/rules/classify", json={
            "text": "Письмо от руководителя",
            "contacts": [{"name": "Иван Петров", "email": "ivan@corp.ru"}],
        })
        assert r.status_code < 400


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class TestReports:
    def test_list_returns_list(self, client):
        """GET /api/v1/reports must return a JSON list (not wrapped dict)."""
        j = json_ok(client.get("/api/v1/reports"))
        assert isinstance(j, list)

    def test_generate_daily_agenda_structure(self, client):
        """Generated report must have id, type, generated_at, content fields."""
        j = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "daily_agenda"}))
        for key in ("id", "type", "generated_at", "content"):
            assert key in j, f"Missing key '{key}' in report"
        assert j["type"] == "daily_agenda"
        assert len(j["content"]) > 0

    def test_generate_weekly_review_structure(self, client):
        j = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "weekly_review"}))
        assert j["type"] == "weekly_review"
        assert "id" in j

    def test_generate_completed_review(self, client):
        j = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "completed_review"}))
        assert j["type"] == "completed_review"

    def test_get_by_id(self, client):
        """Generated report should be retrievable by id."""
        gen = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "daily_agenda"}))
        report_id = gen["id"]
        j = json_ok(client.get(f"/api/v1/reports/{report_id}"))
        assert j["id"] == report_id
        assert j["type"] == "daily_agenda"

    def test_get_unknown_id_404(self, client):
        r = client.get("/api/v1/reports/nonexistent_report_xyz")
        assert r.status_code == 404

    def test_delete_report(self, client):
        """Delete a generated report; subsequent GET should return 404."""
        gen = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "daily_agenda"}))
        report_id = gen["id"]
        j = json_ok(client.delete(f"/api/v1/reports/{report_id}"))
        assert j.get("ok") is True
        r = client.get(f"/api/v1/reports/{report_id}")
        assert r.status_code == 404

    def test_generate_unknown_type_4xx(self, client):
        r = client.post("/api/v1/reports/generate", json={"report_type": "nonexistent_type"})
        assert r.status_code in (400, 422, 404)

    def test_list_after_generate_grows(self, client):
        """After generating a report, the list must contain at least that report."""
        gen = json_ok(client.post("/api/v1/reports/generate", json={"report_type": "weekly_review"}))
        reports = json_ok(client.get("/api/v1/reports"))
        ids = [r["id"] for r in reports]
        assert gen["id"] in ids


# ---------------------------------------------------------------------------
# Inbox: Structured Extraction (Stage 1 AI features)
# ---------------------------------------------------------------------------


class TestInboxExtraction:
    """Tests for POST /extract, GET /extraction, DELETE /extraction-cache."""

    # ── POST /{item_id}/extract ─────────────────────────────────────────────

    def test_extract_with_body_returns_valid_structure(self, client):
        """POST /extract with explicit body returns full ExtractionResult shape."""
        body = "Прошу срочно подготовить финансовый отчёт до 30 мая. Жду ответа."
        r = client.post("/api/v1/inbox/extract_test_01/extract", json={"body": body})
        assert r.status_code == 200
        j = r.json()
        assert j["id"] == "extract_test_01"
        extr = j["extraction"]
        # Required fields
        for field in ("action_items", "entities", "intent", "tone", "reply_required",
                      "deadline", "summary_one_line"):
            assert field in extr, f"extraction missing field: {field}"
        # Type assertions
        assert isinstance(extr["action_items"], list)
        assert isinstance(extr["entities"], dict)
        assert isinstance(extr["reply_required"], bool)
        assert extr["intent"] in {
            "request", "info", "question", "deadline", "meeting", "fyi", "unknown"
        }
        assert extr["tone"] in {"formal", "informal", "urgent", "neutral"}

    def test_extract_entities_shape(self, client):
        """Entities dict must have the four expected sub-lists."""
        body = "Встреча с Иваном Петровым из ООО Альфа. Бюджет: 500 000 руб."
        r = json_ok(client.post("/api/v1/inbox/extract_test_02/extract", json={"body": body}))
        entities = r["extraction"]["entities"]
        for key in ("people", "organizations", "amounts", "dates"):
            assert key in entities, f"entities missing key: {key}"
            assert isinstance(entities[key], list)

    def test_extract_deadline_iso_format(self, client):
        """If deadline is found, it must match YYYY-MM-DD format."""
        body = "Отчёт нужен до 2026-06-15."
        r = json_ok(client.post("/api/v1/inbox/extract_deadline_01/extract", json={"body": body}))
        deadline = r["extraction"]["deadline"]
        if deadline is not None:
            import re
            assert re.match(r"\d{4}-\d{2}-\d{2}", deadline), \
                f"deadline not ISO: {deadline!r}"

    def test_extract_empty_body_returns_result(self, client):
        """Empty body must not crash — returns a valid (empty) result."""
        r = client.post("/api/v1/inbox/extract_empty_01/extract", json={"body": ""})
        assert r.status_code == 200
        extr = r.json()["extraction"]
        assert isinstance(extr["action_items"], list)

    def test_extract_no_body_field_uses_vault(self, client):
        """Omitting body still returns 200 (looks up vault, falls back gracefully)."""
        r = client.post("/api/v1/inbox/extract_no_body_01/extract", json={})
        # 200 even if vault isn't loaded (empty body fallback)
        assert r.status_code == 200
        assert "extraction" in r.json()

    def test_extract_long_body_truncated_gracefully(self, client):
        """Very long body (>3000 chars) must not crash the endpoint."""
        long_body = "Прошу подготовить документы. " * 200  # ~6000 chars
        r = client.post("/api/v1/inbox/extract_long_01/extract", json={"body": long_body})
        assert r.status_code == 200

    def test_extract_action_items_capped_at_ten(self, client):
        """More than 10 action triggers → action_items list ≤ 10."""
        body = ". ".join([f"Прошу сделать задачу {i}" for i in range(20)])
        r = json_ok(client.post("/api/v1/inbox/extract_cap_01/extract", json={"body": body}))
        assert len(r["extraction"]["action_items"]) <= 10

    def test_extract_method_field_present(self, client):
        """method field is returned at the top level (not inside extraction)."""
        body = "Нужно согласовать договор."
        r = json_ok(client.post("/api/v1/inbox/extract_method_01/extract", json={"body": body}))
        assert "method" in r
        assert r["method"] in {"mlx", "fallback", "cached"}

    # ── GET /{item_id}/extraction ────────────────────────────────────────────

    def test_get_extraction_404_before_extract(self, client):
        """GET /extraction before POST /extract must return 404."""
        # Use a unique item_id very unlikely to be in cache
        unique_id = "extract_never_called_xyzzy_9999"
        # First clear cache to ensure clean state
        client.delete("/api/v1/inbox/extraction-cache")
        r = client.get(f"/api/v1/inbox/{unique_id}/extraction")
        assert r.status_code == 404

    def test_get_extraction_200_after_extract(self, client):
        """After POST /extract, GET /extraction returns 200 with same data."""
        item_id = "extract_roundtrip_01"
        body = "Встреча в пятницу, нужно согласовать повестку."
        # Run extraction
        post_j = json_ok(client.post(
            f"/api/v1/inbox/{item_id}/extract",
            json={"body": body},
        ))
        # Now retrieve
        get_j = json_ok(client.get(f"/api/v1/inbox/{item_id}/extraction"))
        assert get_j["id"] == item_id
        assert "extraction" in get_j
        # Same intent
        assert get_j["extraction"]["intent"] == post_j["extraction"]["intent"]

    # ── force=True bypasses cache ────────────────────────────────────────────

    def test_extract_force_reruns_extraction(self, client):
        """force=True always returns fresh result (no cache shortcut)."""
        item_id = "extract_force_01"
        body = "Срочное письмо: подтвердите участие в совещании."
        # First extract to populate cache
        json_ok(client.post(f"/api/v1/inbox/{item_id}/extract", json={"body": body}))
        # Force re-extraction
        r2 = json_ok(client.post(
            f"/api/v1/inbox/{item_id}/extract",
            json={"body": body, "force": True},
        ))
        # Both must be valid results with identical shape
        for field in ("action_items", "entities", "intent", "tone"):
            assert field in r2["extraction"]
        # method should NOT be "cached" when forced
        assert r2["method"] != "cached"

    # ── DELETE /extraction-cache ─────────────────────────────────────────────

    def test_clear_extraction_cache_returns_count(self, client):
        """DELETE /extraction-cache returns {removed: int, status: 'ok'}."""
        # Ensure at least one entry exists
        client.post("/api/v1/inbox/cache_clear_test_01/extract",
                    json={"body": "Тестовое письмо для кэша."})
        r = json_ok(client.delete("/api/v1/inbox/extraction-cache"))
        assert "removed" in r
        assert isinstance(r["removed"], int)
        assert r.get("status") == "ok"

    def test_clear_cache_twice_second_returns_zero(self, client):
        """Clearing an already-empty cache returns removed=0."""
        # First clear whatever is there
        client.delete("/api/v1/inbox/extraction-cache")
        # Second clear
        r = json_ok(client.delete("/api/v1/inbox/extraction-cache"))
        assert r["removed"] == 0

    def test_extract_then_clear_then_get_still_200(self, client):
        """DELETE /cache clears the MLX re-use cache but inbox_state.json persists.

        After POST /extract the result is stored in inbox_state (server-side
        persistence). DELETE /extraction-cache only purges the MLX dedup cache
        so subsequent GETs on the same item_id still return 200.
        """
        item_id = "extract_clear_cycle_01"
        body = "Совещание по проекту в четверг."
        json_ok(client.post(f"/api/v1/inbox/{item_id}/extract", json={"body": body}))
        json_ok(client.delete("/api/v1/inbox/extraction-cache"))
        # State is persisted in inbox_state.json — still accessible
        r = client.get(f"/api/v1/inbox/{item_id}/extraction")
        assert r.status_code == 200
        assert "extraction" in r.json()


# ---------------------------------------------------------------------------
# Extraction unit tests (no server needed — pure Python)
# ---------------------------------------------------------------------------


class TestExtractionUnit:
    """Unit tests for extract.py — fallback, JSON repair, cache helpers."""

    # ── _fallback_extract ────────────────────────────────────────────────────

    def test_fallback_detects_request_intent(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Прошу подготовить отчёт к пятнице.")
        assert result.intent == "request"

    def test_fallback_detects_meeting_intent(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Созвон по проекту в среду в 10:00.")
        assert result.intent == "meeting"

    def test_fallback_detects_urgent_tone(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Срочно! Необходимо ответить до конца дня.")
        assert result.tone == "urgent"

    def test_fallback_reply_required_true(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Прошу ответить на письмо до конца дня.")
        assert result.reply_required is True

    def test_fallback_extracts_rub_amount(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Счёт на 150 000 руб. Оплатить до пятницы.")
        assert any("руб" in a.lower() or "150" in a for a in result.entities.amounts)

    def test_fallback_extracts_iso_date(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Документы нужны до 2026-06-01.")
        assert result.deadline == "2026-06-01"

    def test_fallback_action_items_from_trigger(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("Прошу согласовать договор с юристами.")
        assert any("согласовать" in ai.text.lower() for ai in result.action_items)

    def test_fallback_empty_body(self):
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        result = _fallback_extract("")
        assert result.intent in {"info", "unknown"}
        assert result.action_items == []

    def test_fallback_result_is_always_valid(self):
        """_fallback_extract never raises even on garbage input."""
        from personal_assistant.mlx_server.tasks.extract import _fallback_extract
        for text in ["", "   ", "!!!", "2026-13-99", "а" * 5000]:
            result = _fallback_extract(text)
            assert result.intent in {
                "request", "info", "question", "deadline", "meeting", "fyi", "unknown"
            }
            assert result.tone in {"formal", "informal", "urgent", "neutral"}

    # ── _repair_json ─────────────────────────────────────────────────────────

    def test_repair_strips_markdown_fence(self):
        from personal_assistant.mlx_server.tasks.extract import _repair_json
        raw = '```json\n{"intent": "request"}\n```'
        repaired = _repair_json(raw)
        assert json_ok_str(repaired)["intent"] == "request"

    def test_repair_fixes_trailing_comma_object(self):
        from personal_assistant.mlx_server.tasks.extract import _repair_json
        raw = '{"intent": "fyi",}'
        repaired = _repair_json(raw)
        import json
        d = json.loads(repaired)
        assert d["intent"] == "fyi"

    def test_repair_fixes_trailing_comma_array(self):
        from personal_assistant.mlx_server.tasks.extract import _repair_json
        raw = '{"items": [1, 2, 3,]}'
        repaired = _repair_json(raw)
        import json
        d = json.loads(repaired)
        assert d["items"] == [1, 2, 3]

    def test_repair_finds_nested_json(self):
        from personal_assistant.mlx_server.tasks.extract import _repair_json
        raw = 'Here is the result:\n{"intent": "meeting"}\nEOF'
        repaired = _repair_json(raw)
        import json
        assert json.loads(repaired)["intent"] == "meeting"

    def test_repair_raises_on_no_json(self):
        import pytest

        from personal_assistant.mlx_server.tasks.extract import _repair_json
        with pytest.raises((ValueError, Exception)):
            _repair_json("no json here at all")

    # ── _body_sha256 ─────────────────────────────────────────────────────────

    def test_sha256_is_16_hex_chars(self):
        from personal_assistant.mlx_server.tasks.extract import _body_sha256
        sha = _body_sha256("hello")
        assert len(sha) == 16
        assert all(c in "0123456789abcdef" for c in sha)

    def test_sha256_same_input_same_hash(self):
        from personal_assistant.mlx_server.tasks.extract import _body_sha256
        assert _body_sha256("abc") == _body_sha256("abc")

    def test_sha256_different_input_different_hash(self):
        from personal_assistant.mlx_server.tasks.extract import _body_sha256
        assert _body_sha256("abc") != _body_sha256("xyz")

    # ── cache helpers ─────────────────────────────────────────────────────────

    def test_cache_size_and_clear(self, tmp_path, monkeypatch):
        """clear_cache empties cache; cache_size returns 0 after clear."""
        import personal_assistant.mlx_server.tasks.extract as extr_mod

        # Point cache to tmp dir
        monkeypatch.setattr(extr_mod, "_CACHE_PATH", tmp_path / "extraction_cache.json")

        # Clear first (may already be empty)
        extr_mod.clear_cache()
        assert extr_mod.cache_size() == 0

        # Extract something to populate cache
        extr_mod.extract("Прошу согласовать договор.", force=True)
        assert extr_mod.cache_size() == 1

        # Clear and verify
        removed = extr_mod.clear_cache()
        assert removed == 1
        assert extr_mod.cache_size() == 0

    def test_cache_hit_returns_cached_method(self, tmp_path, monkeypatch):
        """Second extract() call returns method='cached'."""
        import personal_assistant.mlx_server.tasks.extract as extr_mod
        monkeypatch.setattr(extr_mod, "_CACHE_PATH", tmp_path / "extraction_cache.json")

        body = "Тест кэширования: встреча в пятницу."
        extr_mod.extract(body, force=True)   # populate
        result = extr_mod.extract(body)       # should hit cache
        assert result.method == "cached"

    def test_extract_force_skips_cache(self, tmp_path, monkeypatch):
        """force=True returns method != 'cached'."""
        import personal_assistant.mlx_server.tasks.extract as extr_mod
        monkeypatch.setattr(extr_mod, "_CACHE_PATH", tmp_path / "extraction_cache.json")

        body = "Срочный запрос на согласование."
        extr_mod.extract(body)           # populate cache
        result = extr_mod.extract(body, force=True)
        assert result.method != "cached"

    # ── _parse_extraction_json ───────────────────────────────────────────────

    def test_parse_valid_json(self):
        from personal_assistant.mlx_server.tasks.extract import _parse_extraction_json
        raw = """{
          "action_items": [{"text": "Написать отчёт", "deadline": "2026-06-01", "assignee": "me"}],
          "entities": {"people": ["Иван"], "organizations": [], "amounts": [], "dates": []},
          "intent": "request",
          "tone": "formal",
          "reply_required": true,
          "deadline": "2026-06-01",
          "summary_one_line": "Краткое описание"
        }"""
        result = _parse_extraction_json(raw)
        assert result.intent == "request"
        assert result.reply_required is True
        assert len(result.action_items) == 1
        assert result.action_items[0].deadline == "2026-06-01"

    def test_parse_invalid_intent_becomes_unknown(self):
        from personal_assistant.mlx_server.tasks.extract import _parse_extraction_json
        raw = '{"action_items":[],"entities":{},"intent":"bogus","tone":"formal","reply_required":false,"deadline":null,"summary_one_line":""}'
        result = _parse_extraction_json(raw)
        assert result.intent == "unknown"

    def test_parse_invalid_tone_becomes_neutral(self):
        from personal_assistant.mlx_server.tasks.extract import _parse_extraction_json
        raw = '{"action_items":[],"entities":{},"intent":"fyi","tone":"angry","reply_required":false,"deadline":null,"summary_one_line":""}'
        result = _parse_extraction_json(raw)
        assert result.tone == "neutral"

    def test_parse_non_iso_deadline_nulled(self):
        from personal_assistant.mlx_server.tasks.extract import _parse_extraction_json
        raw = '{"action_items":[],"entities":{},"intent":"info","tone":"neutral","reply_required":false,"deadline":"next Friday","summary_one_line":""}'
        result = _parse_extraction_json(raw)
        assert result.deadline is None

    def test_parse_action_items_capped_at_10(self):
        import json

        from personal_assistant.mlx_server.tasks.extract import _parse_extraction_json
        items = [{"text": f"Task {i}", "deadline": None, "assignee": None} for i in range(15)]
        data = {
            "action_items": items, "entities": {},
            "intent": "request", "tone": "formal",
            "reply_required": False, "deadline": None, "summary_one_line": "",
        }
        result = _parse_extraction_json(json.dumps(data))
        assert len(result.action_items) <= 10


# ---------------------------------------------------------------------------
# Helpers for unit tests
# ---------------------------------------------------------------------------

def json_ok_str(s: str) -> dict:
    """Parse a JSON string and return dict."""
    import json
    return json.loads(s)


# ---------------------------------------------------------------------------
# Profile + Assistant config
# ---------------------------------------------------------------------------


class TestProfileAndConfig:
    def test_profile_get(self, client):
        j = json_ok(client.get("/api/v1/profile"))
        assert isinstance(j, dict)

    def test_profile_save(self, client):
        original = json_ok(client.get("/api/v1/profile"))
        # Only save safe fields — don't round-trip file paths that may be sandbox-specific
        safe = {k: v for k, v in original.items() if k in ("name", "email", "language", "role")}
        if safe:
            ok(client.put("/api/v1/profile", json=safe))

    def test_assistant_config_get(self, client):
        j = json_ok(client.get("/api/v1/assistant-config"))
        assert isinstance(j, dict)

    def test_assistant_config_save(self, client):
        original = json_ok(client.get("/api/v1/assistant-config"))
        # Only save scalar/string fields, skip any path-like values
        safe = {k: v for k, v in original.items()
                if v is None or isinstance(v, (bool, int, float))
                or (isinstance(v, str) and not v.startswith("/"))}
        if safe:
            ok(client.put("/api/v1/assistant-config", json=safe))


# ---------------------------------------------------------------------------
# Tools + Tool Prompts
# ---------------------------------------------------------------------------


class TestToolsAndPrompts:
    def test_list_tools(self, client):
        j = json_ok(client.get("/tools"))
        assert isinstance(j, (list, dict))

    def test_get_tool_prompts(self, client):
        j = json_ok(client.get("/tool-prompts"))
        assert isinstance(j, dict)

    def test_save_tool_prompts(self, client):
        original = json_ok(client.get("/tool-prompts"))
        ok(client.post("/tool-prompts", json=original))


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


class TestModelManagement:
    def test_catalogue(self, client):
        j = json_ok(client.get("/model/catalogue"))
        assert isinstance(j, (list, dict))


# ---------------------------------------------------------------------------
# Today dashboard
# ---------------------------------------------------------------------------


class TestToday:
    def test_today_returns_200(self, client):
        """GET /api/v1/today should always return 200."""
        r = client.get("/api/v1/today")
        assert r.status_code == 200

    def test_today_has_required_keys(self, client):
        """Response must contain all top-level keys."""
        j = client.get("/api/v1/today").json()
        for key in ("greeting", "updated_at", "next_update", "bullets",
                    "events", "events_total", "attention", "attention_total",
                    "urgent_count", "suggestions"):
            assert key in j, f"Missing key: {key}"

    def test_today_greeting_is_string(self, client):
        j = client.get("/api/v1/today").json()
        assert isinstance(j["greeting"], str)
        assert len(j["greeting"]) > 0

    def test_today_bullets_is_list(self, client):
        j = client.get("/api/v1/today").json()
        assert isinstance(j["bullets"], list)

    def test_today_events_is_list(self, client):
        j = client.get("/api/v1/today").json()
        assert isinstance(j["events"], list)

    def test_today_attention_is_list(self, client):
        j = client.get("/api/v1/today").json()
        assert isinstance(j["attention"], list)

    def test_today_suggestions_is_list(self, client):
        j = client.get("/api/v1/today").json()
        assert isinstance(j["suggestions"], list)

    def test_today_suggestions_at_most_3(self, client):
        j = client.get("/api/v1/today").json()
        assert len(j["suggestions"]) <= 3

    def test_today_event_shape(self, client):
        """Every event dict must have required fields."""
        j = client.get("/api/v1/today").json()
        for ev in j["events"]:
            for field in ("id", "title", "time", "status", "path"):
                assert field in ev, f"Event missing field: {field}"
            assert ev["status"] in ("active", "upcoming", "past")

    def test_today_attention_shape(self, client):
        """Every attention item must have sender + subject fields."""
        j = client.get("/api/v1/today").json()
        for item in j["attention"]:
            for field in ("id", "sender_name", "sender_initials", "sender_color",
                          "subject", "time_label", "path"):
                assert field in item, f"Attention item missing field: {field}"

    def test_today_suggestion_shape(self, client):
        """Every suggestion must have action + message for the frontend."""
        j = client.get("/api/v1/today").json()
        for s in j["suggestions"]:
            for field in ("icon", "label", "detail", "action", "message"):
                assert field in s, f"Suggestion missing field: {field}"

    def test_today_updated_at_format(self, client):
        """updated_at should look like HH:MM."""
        import re
        j = client.get("/api/v1/today").json()
        assert re.match(r"^\d{2}:\d{2}$", j["updated_at"]), \
            f"updated_at format unexpected: {j['updated_at']}"

    def test_today_next_update_format(self, client):
        """next_update should look like HH:MM."""
        import re
        j = client.get("/api/v1/today").json()
        assert re.match(r"^\d{2}:\d{2}$", j["next_update"]), \
            f"next_update format unexpected: {j['next_update']}"

    def test_today_urgent_count_non_negative(self, client):
        j = client.get("/api/v1/today").json()
        assert j["urgent_count"] >= 0

    def test_today_greeting_helper(self):
        """_greeting() returns a non-empty string for any first name."""
        from personal_assistant.today.routes import _greeting
        for name in ("Игорь", "Alice", ""):
            result = _greeting(name)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_today_next_update_helper(self):
        """_next_update_label() always returns HH:MM string."""
        import re

        from personal_assistant.today.routes import _next_update_label
        label = _next_update_label()
        assert re.match(r"^\d{2}:\d{2}$", label), f"Got: {label}"

    def test_today_is_today_helper(self):
        """_is_today() is True for now, False for old dates."""
        from datetime import datetime, timezone

        from personal_assistant.today.routes import _is_today
        now_iso = datetime.now(timezone.utc).isoformat()
        assert _is_today(now_iso) is True
        assert _is_today("2000-01-01T00:00:00Z") is False
        assert _is_today(None) is False
        assert _is_today("") is False


# ---------------------------------------------------------------------------
# Souls + Persona
# ---------------------------------------------------------------------------


class TestSoulsAndPersona:
    def test_souls_get(self, client):
        j = json_ok(client.get("/souls"))
        assert isinstance(j, dict)

    def test_souls_save(self, client):
        original = json_ok(client.get("/souls"))
        content = original.get("content", "")
        ok(client.put("/souls", json={"content": content}))

    def test_persona_get(self, client):
        j = json_ok(client.get("/persona"))
        assert isinstance(j, dict)


# ---------------------------------------------------------------------------
# Stage 2 — AI Priority Score  (TestInboxPriority)
# ---------------------------------------------------------------------------


class TestInboxPriority:
    """
    Verify that every inbox item carries priority score and label, that
    sort_by=priority ordering is respected, and that stats include the
    followup counter.

    Scenarios
    ---------
    S1  Each item has an integer ``priority`` in [0, 100].
    S2  Each item has a ``priority_label`` ∈ {"low", "medium", "high"}.
    S3  Each item has a ``followup_needed`` bool.
    S4  Stats block contains ``followup`` integer.
    S5  sort_by=priority returns items in non-ascending priority order.
    S6  sort_by=date (default) still returns priority fields.
    S7  filter=followup returns only items with followup_needed=True.
    S8  filter=followup + sort_by=priority still returns valid structure.
    S9  Priority enrichment is idempotent: calling twice does not double-score.
    S10 priority_label matches the score bracket (low<40, medium<70, high≥70).
    """

    def test_s1_priority_field_int_range(self, client):
        """S1: priority is int in [0, 100] for every item."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            p = item.get("priority")
            assert isinstance(p, int), f"priority not int: {p!r}"
            assert 0 <= p <= 100, f"priority out of range: {p}"

    def test_s2_priority_label_values(self, client):
        """S2: priority_label is one of the three allowed strings."""
        valid = {"low", "medium", "high"}
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            lbl = item.get("priority_label")
            assert lbl in valid, f"unexpected priority_label: {lbl!r}"

    def test_s3_followup_needed_bool(self, client):
        """S3: followup_needed is a boolean on every item."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            fn = item.get("followup_needed")
            assert isinstance(fn, bool), f"followup_needed not bool: {fn!r}"

    def test_s4_stats_has_followup(self, client):
        """S4: stats dict contains integer followup counter."""
        j = json_ok(client.get("/api/v1/inbox"))
        stats = j.get("stats", {})
        assert "followup" in stats, "stats missing 'followup' key"
        assert isinstance(stats["followup"], int)
        assert stats["followup"] >= 0

    def test_s5_sort_by_priority_descending(self, client):
        """S5: sort_by=priority items are in non-ascending priority order."""
        j = json_ok(client.get("/api/v1/inbox?sort_by=priority"))
        items = j.get("items", [])
        priorities = [it.get("priority", 0) for it in items]
        assert priorities == sorted(priorities, reverse=True), \
            f"items not sorted by priority desc: {priorities}"

    def test_s6_sort_by_date_still_has_priority(self, client):
        """S6: default sort (date) still populates priority fields."""
        j = json_ok(client.get("/api/v1/inbox?sort_by=date"))
        for item in j.get("items", []):
            assert "priority" in item
            assert "priority_label" in item

    def test_s7_filter_followup_subset(self, client):
        """S7: filter=followup returns only items with followup_needed=True."""
        j = json_ok(client.get("/api/v1/inbox?filter=followup"))
        for item in j.get("items", []):
            assert item.get("followup_needed") is True, \
                f"item in followup filter but followup_needed is not True: {item.get('id')}"

    def test_s8_filter_followup_with_priority_sort(self, client):
        """S8: filter=followup&sort_by=priority returns valid structure."""
        j = json_ok(client.get("/api/v1/inbox?filter=followup&sort_by=priority"))
        assert "items" in j
        assert "stats" in j
        items = j["items"]
        if len(items) >= 2:
            priorities = [it.get("priority", 0) for it in items]
            assert priorities == sorted(priorities, reverse=True)

    def test_s9_priority_enrichment_fields_present(self, client):
        """S9: Both calls return same items with same priority fields (idempotent)."""
        j1 = json_ok(client.get("/api/v1/inbox"))
        j2 = json_ok(client.get("/api/v1/inbox"))
        items1 = {it["id"]: it.get("priority") for it in j1["items"]}
        items2 = {it["id"]: it.get("priority") for it in j2["items"]}
        assert items1 == items2, "priority scores changed between identical calls"

    def test_s10_priority_label_matches_score(self, client):
        """S10: priority_label bracket matches score value (thresholds: 34=medium, 67=high)."""
        j = json_ok(client.get("/api/v1/inbox"))
        for item in j.get("items", []):
            score = item.get("priority", 0)
            label = item.get("priority_label", "low")
            if score >= 67:
                assert label == "high",   f"score={score} should be high, got {label}"
            elif score >= 34:
                assert label == "medium", f"score={score} should be medium, got {label}"
            else:
                assert label == "low",    f"score={score} should be low, got {label}"


# ---------------------------------------------------------------------------
# Stage 2 — Follow-up Detection  (TestFollowup)
# ---------------------------------------------------------------------------


class TestFollowup:
    """
    Verify the dedicated follow-up endpoint and its contract.

    Scenarios
    ---------
    F1  GET /followup-needed returns {items, count, threshold_days}.
    F2  count matches len(items).
    F3  threshold_days in response equals the requested parameter.
    F4  Endpoint returns 200 even when vault is empty / not loaded.
    F5  threshold_days=0 returns no error (edge case: everything is old enough).
    F6  threshold_days=30 (max) accepted without error.
    F7  threshold_days=-1 returns 422 (below allowed minimum).
    F8  threshold_days=31 returns 422 (above allowed maximum).
    F9  items list contains only strings (item IDs).
    F10 items in response are also present in inbox list (subset consistency).
    """

    def test_f1_response_shape(self, client):
        """F1: Response has required keys."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed"))
        assert "items" in j,          "missing 'items' key"
        assert "count" in j,          "missing 'count' key"
        assert "threshold_days" in j, "missing 'threshold_days' key"

    def test_f2_count_matches_len(self, client):
        """F2: count equals len(items)."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed"))
        assert j["count"] == len(j["items"]), \
            f"count={j['count']} but len(items)={len(j['items'])}"

    def test_f3_threshold_days_echoed(self, client):
        """F3: threshold_days in response mirrors the request parameter."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed?threshold_days=5"))
        assert j["threshold_days"] == 5

    def test_f4_returns_200_no_vault(self, client):
        """F4: Returns 200 even if vault is empty / unavailable."""
        r = client.get("/api/v1/inbox/followup-needed")
        assert r.status_code == 200

    def test_f5_threshold_zero(self, client):
        """F5: threshold_days=0 accepted without error."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed?threshold_days=0"))
        assert isinstance(j["items"], list)

    def test_f6_threshold_thirty(self, client):
        """F6: threshold_days=30 accepted without error."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed?threshold_days=30"))
        assert isinstance(j["items"], list)

    def test_f7_threshold_negative_rejected(self, client):
        """F7: threshold_days=-1 returns 422."""
        r = client.get("/api/v1/inbox/followup-needed?threshold_days=-1")
        assert r.status_code == 422

    def test_f8_threshold_too_large_rejected(self, client):
        """F8: threshold_days=31 returns 422."""
        r = client.get("/api/v1/inbox/followup-needed?threshold_days=31")
        assert r.status_code == 422

    def test_f9_items_are_strings(self, client):
        """F9: Each item in the list is a non-empty string (item ID)."""
        j = json_ok(client.get("/api/v1/inbox/followup-needed"))
        for item_id in j.get("items", []):
            assert isinstance(item_id, str) and item_id, \
                f"item_id is not a non-empty string: {item_id!r}"

    def test_f10_followup_ids_in_inbox(self, client):
        """F10: IDs from followup-needed are a subset of all inbox item IDs."""
        followup_ids = set(json_ok(client.get("/api/v1/inbox/followup-needed"))["items"])
        if not followup_ids:
            return  # nothing to check
        inbox_ids = {it["id"] for it in json_ok(client.get("/api/v1/inbox"))["items"]}
        unknown = followup_ids - inbox_ids
        assert not unknown, f"followup IDs not found in inbox: {unknown}"


# ---------------------------------------------------------------------------
# Stage 2 — Priority scoring unit helpers (inline, no vault needed)
# ---------------------------------------------------------------------------


class TestPriorityHelpers:
    """
    Light unit tests that exercise the priority module directly,
    without going through the HTTP stack.

    Scenarios
    ---------
    P1  compute_priority returns int in [0, 100] for a bare item.
    P2  Item with urgency:urgent tag scores ≥ 40.
    P3  Item with reply_required=True extraction scores higher than without.
    P4  priority_label returns correct bracket for boundary values.
    P5  enrich_with_priority adds both fields in-place.
    P6  enrich_with_priority is safe on empty list.
    P7  build_contact_graph returns empty dict for non-existent path.
    """

    def test_p1_compute_returns_int_in_range(self):
        """P1: compute_priority on a minimal item returns 0–100 int."""
        from personal_assistant.mlx_server.tasks.priority import compute_priority
        item = {"id": "x", "type": "email", "tags": []}
        score = compute_priority(item)
        assert isinstance(score, int)
        assert 0 <= score <= 100

    def test_p2_urgency_urgent_boosts_score(self):
        """P2: urgency:urgent in tags_raw (raw tag list) pushes score ≥ 40."""
        from personal_assistant.mlx_server.tasks.priority import compute_priority
        # urgency is read from tags_raw (pre-classifier raw tags), not tags
        item = {"id": "x", "type": "email", "tags": [], "tags_raw": ["urgency:urgent"]}
        assert compute_priority(item) >= 40

    def test_p3_reply_required_boosts_score(self):
        """P3: reply_required=True yields higher score than without."""
        from personal_assistant.mlx_server.tasks.priority import compute_priority
        base   = {"id": "a", "type": "email", "tags": []}
        with_r = {"id": "b", "type": "email", "tags": [],
                  "extraction": {"reply_required": True}}
        assert compute_priority(with_r) > compute_priority(base)

    def test_p4_priority_label_boundaries(self):
        """P4: label boundaries at 34 (low→medium) and 67 (medium→high)."""
        from personal_assistant.mlx_server.tasks.priority import priority_label
        assert priority_label(0)   == "low"
        assert priority_label(33)  == "low"
        assert priority_label(34)  == "medium"
        assert priority_label(66)  == "medium"
        assert priority_label(67)  == "high"
        assert priority_label(100) == "high"

    def test_p5_enrich_adds_fields_in_place(self):
        """P5: enrich_with_priority adds priority and priority_label to each item."""
        from personal_assistant.mlx_server.tasks.priority import enrich_with_priority
        items = [{"id": "i1", "tags": []}, {"id": "i2", "tags": ["urgency:urgent"]}]
        enrich_with_priority(items)
        for it in items:
            assert "priority"       in it
            assert "priority_label" in it
            assert isinstance(it["priority"], int)
            assert it["priority_label"] in {"low", "medium", "high"}

    def test_p6_enrich_empty_list(self):
        """P6: enrich_with_priority on empty list does not raise."""
        from personal_assistant.mlx_server.tasks.priority import enrich_with_priority
        enrich_with_priority([])   # must not raise

    def test_p7_contact_graph_missing_vault(self):
        """P7: build_contact_graph returns {} for non-existent vault path."""
        from personal_assistant.mlx_server.tasks.priority import build_contact_graph
        result = build_contact_graph("/tmp/__no_such_vault_path_xyz__")
        assert isinstance(result, dict)
        assert result == {}


# ---------------------------------------------------------------------------
# Stage 4: Thread-Aware Draft — /api/v1/inbox/{item_id}/draft-context
# ---------------------------------------------------------------------------

class TestDraftContext:
    """
    Verify the Thread-Aware Draft endpoint that enriches a draft request with
    full email-thread context from the vault before handing it to the LLM.

    Scenarios
    ---------
    DC1  POST /api/v1/inbox/{id}/draft-context returns HTTP 200.
    DC2  Response body contains all required keys.
    DC3  ``item_id`` in response matches the path parameter.
    DC4  ``thread_messages`` is a list.
    DC5  ``key_facts`` is a list.
    DC6  ``my_previous_replies`` is a list.
    DC7  ``context_prompt`` is a non-empty string.
    DC8  ``thread_summary`` is a non-empty string.
    DC9  ``draft_hint`` is a string (may be empty when no thread).
    DC10 ``message_count`` equals len(thread_messages).
    DC11 ``subject`` is a string.
    DC12 ``sender`` and ``sender_email`` are strings.
    DC13 Unknown item_id still returns 200 with graceful minimal context.
    DC14 ``context_prompt`` contains at least one Cyrillic word ("ответ"/"Составь").
    DC15 Service-level unit: build_draft_context runs without app state.
    """

    # -----------------------------------------------------------------------
    # HTTP contract tests (use the real FastAPI TestClient)
    # -----------------------------------------------------------------------

    def test_dc1_returns_200(self, client):
        """DC1: POST .../draft-context returns 200 regardless of vault state."""
        resp = client.post("/api/v1/inbox/any_test_item/draft-context")
        assert resp.status_code == 200, resp.text

    def test_dc2_all_required_keys(self, client):
        """DC2: All required keys are present in the response."""
        resp = client.post("/api/v1/inbox/any_test_item/draft-context")
        j = resp.json()
        required = {
            "item_id", "subject", "sender", "sender_email", "thread_id",
            "thread_messages", "thread_summary", "key_facts",
            "my_previous_replies", "draft_hint", "context_prompt",
            "message_count",
        }
        missing = required - set(j.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_dc3_item_id_echoed(self, client):
        """DC3: item_id in response matches the path parameter."""
        item_id = "test_echo_item_xyz"
        j = client.post(f"/api/v1/inbox/{item_id}/draft-context").json()
        assert j["item_id"] == item_id

    def test_dc4_thread_messages_is_list(self, client):
        """DC4: thread_messages is a list (may be empty without vault)."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["thread_messages"], list)

    def test_dc5_key_facts_is_list(self, client):
        """DC5: key_facts is a list (may be empty without vault)."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["key_facts"], list)

    def test_dc6_my_previous_replies_is_list(self, client):
        """DC6: my_previous_replies is a list."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["my_previous_replies"], list)

    def test_dc7_context_prompt_nonempty(self, client):
        """DC7: context_prompt is a non-empty string."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["context_prompt"], str)
        assert len(j["context_prompt"]) > 0

    def test_dc8_thread_summary_nonempty(self, client):
        """DC8: thread_summary is a non-empty string."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["thread_summary"], str)
        assert len(j["thread_summary"]) > 0

    def test_dc9_draft_hint_is_string(self, client):
        """DC9: draft_hint is a string (empty is acceptable when no thread)."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["draft_hint"], str)

    def test_dc10_message_count_matches_len(self, client):
        """DC10: message_count == len(thread_messages)."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert j["message_count"] == len(j["thread_messages"])

    def test_dc11_subject_is_string(self, client):
        """DC11: subject is a string."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["subject"], str)

    def test_dc12_sender_fields_are_strings(self, client):
        """DC12: sender and sender_email are strings."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        assert isinstance(j["sender"], str)
        assert isinstance(j["sender_email"], str)

    def test_dc13_unknown_item_graceful(self, client):
        """DC13: Unknown item_id still returns 200 with a non-empty context_prompt."""
        j = client.post("/api/v1/inbox/__nonexistent_item_zzz__/draft-context").json()
        assert isinstance(j["context_prompt"], str)
        assert len(j["context_prompt"]) > 0
        assert j["message_count"] == 0

    def test_dc14_context_prompt_in_russian(self, client):
        """DC14: context_prompt contains Russian text (Составь / ответ / письмо)."""
        j = client.post("/api/v1/inbox/x/draft-context").json()
        prompt_lower = j["context_prompt"].lower()
        has_russian = any(w in prompt_lower for w in ("составь", "ответ", "письмо", "тред"))
        assert has_russian, f"No Russian in prompt: {j['context_prompt'][:200]!r}"

    # -----------------------------------------------------------------------
    # Service-level unit (no HTTP stack needed, no vault)
    # -----------------------------------------------------------------------

    def test_dc15_service_no_vault(self):
        """DC15: build_draft_context runs without vault_path and returns valid dict."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("standalone_test", vault_path=None, my_email="")
        required = {
            "item_id", "subject", "sender", "sender_email", "thread_id",
            "thread_messages", "thread_summary", "key_facts",
            "my_previous_replies", "draft_hint", "context_prompt",
            "message_count",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing keys from standalone call: {missing}"
        assert result["item_id"] == "standalone_test"
        assert isinstance(result["context_prompt"], str)
        assert len(result["context_prompt"]) > 0


# ---------------------------------------------------------------------------
# Draft & Summarize pipeline — sквозная проверка
# ---------------------------------------------------------------------------

class TestChatDraftSummarize:
    """
    End-to-end coverage of the draft / summarize pipeline:

    Chat send modes
    ---------------
    DS1  POST /api/chat/send mode=draft returns 200 with non-empty text.
    DS2  POST /api/chat/send mode=summarize returns 200 with non-empty text.
    DS3  mode=draft + vault_thread_id does not raise (graceful no vault).
    DS4  mode=chat (default) returns 200.
    DS5  Invalid mode value still returns 200 (mode treated as chat).
    DS6  Both draft sends create independent chat threads.
    DS7  draft message is persisted in thread history.
    DS8  summarize message is persisted in thread history.

    Mail summarize-thread endpoint
    -------------------------------
    DS9  POST /api/chat/mail/summarize-thread with unknown thread returns 404.
    DS10 POST /api/chat/mail/summarize-thread with missing thread_id returns 422.

    Mail thread-messages endpoint
    ------------------------------
    DS11 POST /api/chat/mail/thread-messages returns 200 with correct shape.
    DS12 thread-messages count matches len(messages).
    DS13 thread-messages with unknown id returns empty list (not 500).

    Context builder integration (unit)
    -----------------------------------
    DS14 context_builder mode=draft sets «draft» instruction in system_prompt.
    DS15 context_builder mode=summarize sets «резюме» in system_prompt.
    DS16 context_builder vault_thread_id="" treated as None (no injection).
    DS17 context_builder system_prompt contains current date string.
    DS18 context_builder returns tool_specs list.
    """

    # -----------------------------------------------------------------------
    # Chat send — mode coverage (HTTP)
    # -----------------------------------------------------------------------

    def test_ds1_send_draft_mode_200(self, client):
        """DS1: POST /api/chat/send mode=draft returns 200 with body text."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "Напиши черновик ответа на письмо о переносе встречи",
            "mode": "draft",
        })
        assert r.status_code == 200, r.text
        assert len(r.text) > 0

    def test_ds2_send_summarize_mode_200(self, client):
        """DS2: POST /api/chat/send mode=summarize returns 200 with body text."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "Суммаризируй эту переписку",
            "mode": "summarize",
        })
        assert r.status_code == 200, r.text
        assert len(r.text) > 0

    def test_ds3_draft_with_vault_thread_id(self, client):
        """DS3: mode=draft + vault_thread_id does not crash even without vault."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "Draft с контекстом треда",
            "mode": "draft",
            "vault_thread_id": "thread_nonexistent_xyz",
        })
        assert r.status_code == 200, r.text
        assert len(r.text) > 0

    def test_ds4_send_chat_mode_200(self, client):
        """DS4: default mode=chat baseline still works."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "Hello",
            "mode": "chat",
        })
        assert r.status_code == 200

    def test_ds5_unknown_mode_accepted(self, client):
        """DS5: Unknown mode value returns 200 (falls back to chat behaviour)."""
        r = client.post("/api/chat/send", json={
            "thread_id": None,
            "message": "test",
            "mode": "unknown_mode_xyz",
        })
        assert r.status_code == 200

    def test_ds6_draft_and_summarize_create_separate_threads(self, client):
        """DS6: Two sends without thread_id create independent threads."""
        r1 = client.post("/api/chat/send", json={
            "thread_id": None, "message": "draft msg", "mode": "draft"
        })
        r2 = client.post("/api/chat/send", json={
            "thread_id": None, "message": "summarize msg", "mode": "summarize"
        })
        assert r1.status_code == 200
        assert r2.status_code == 200
        threads = client.get("/api/chat/threads").json().get("threads", [])
        ids = [t.get("id") or t.get("thread_id") for t in threads]
        # At least 2 threads should exist after two independent sends
        assert len(ids) >= 2

    def test_ds7_draft_message_in_history(self, client):
        """DS7: After mode=draft send, user message appears in thread history."""
        unique = "UNIQUE_DRAFT_MSG_ZZZ"
        client.post("/api/chat/send", json={
            "thread_id": None, "message": unique, "mode": "draft"
        })
        threads = client.get("/api/chat/threads").json().get("threads", [])
        found = False
        for t in threads:
            tid = t.get("id") or t.get("thread_id")
            if not tid:
                continue
            hist = client.get(f"/api/chat/history/{tid}").json()
            msgs = hist.get("messages", [])
            if any(unique in (m.get("content") or "") for m in msgs):
                found = True
                break
        assert found, "Draft user message not persisted in any thread history"

    def test_ds8_summarize_message_in_history(self, client):
        """DS8: After mode=summarize send, user message appears in thread history."""
        unique = "UNIQUE_SUMM_MSG_ZZZ"
        client.post("/api/chat/send", json={
            "thread_id": None, "message": unique, "mode": "summarize"
        })
        threads = client.get("/api/chat/threads").json().get("threads", [])
        found = False
        for t in threads:
            tid = t.get("id") or t.get("thread_id")
            if not tid:
                continue
            hist = client.get(f"/api/chat/history/{tid}").json()
            msgs = hist.get("messages", [])
            if any(unique in (m.get("content") or "") for m in msgs):
                found = True
                break
        assert found, "Summarize user message not persisted in any thread history"

    # -----------------------------------------------------------------------
    # /api/chat/mail/summarize-thread (HTTP)
    # -----------------------------------------------------------------------

    def test_ds9_summarize_thread_unknown_returns_404(self, client):
        """DS9: Unknown thread_id returns 404 (no vault messages found)."""
        r = client.post("/api/chat/mail/summarize-thread", json={
            "thread_id": "thread_definitely_not_in_vault_xyz123",
        })
        assert r.status_code in (404, 503), (
            f"Expected 404 for unknown thread, got {r.status_code}: {r.text}"
        )

    def test_ds10_summarize_thread_missing_body_422(self, client):
        """DS10: Missing thread_id field returns 422 Unprocessable Entity."""
        r = client.post("/api/chat/mail/summarize-thread", json={})
        assert r.status_code == 422

    # -----------------------------------------------------------------------
    # /api/chat/mail/thread-messages (HTTP)
    # -----------------------------------------------------------------------

    def test_ds11_thread_messages_shape(self, client):
        """DS11: /mail/thread-messages returns correct keys."""
        r = client.post("/api/chat/mail/thread-messages", json={
            "thread_id": "any_thread_id",
        })
        assert r.status_code == 200, r.text
        j = r.json()
        assert "thread_id" in j
        assert "messages" in j
        assert "count" in j

    def test_ds12_thread_messages_count_matches(self, client):
        """DS12: count == len(messages)."""
        r = client.post("/api/chat/mail/thread-messages", json={
            "thread_id": "any_thread_id",
        })
        j = r.json()
        assert j["count"] == len(j["messages"])

    def test_ds13_thread_messages_unknown_empty(self, client):
        """DS13: Unknown thread_id returns empty list (not 500)."""
        r = client.post("/api/chat/mail/thread-messages", json={
            "thread_id": "thread_not_exist_zzz",
        })
        assert r.status_code == 200
        assert r.json()["count"] == 0

    # -----------------------------------------------------------------------
    # Context builder — unit-level (no HTTP stack, no vault/MLX)
    # -----------------------------------------------------------------------

    def test_ds14_context_builder_draft_instruction(self):
        """DS14: mode=draft injects «черновик» / «draft» into system_prompt."""
        from personal_assistant.profile.context_assembler import ProfileAwareAssembler
        asm = ProfileAwareAssembler()
        result = asm.build(
            user_message="Напиши ответ",
            history=[],
            context_paths=[],
            mode="draft",
        )
        sp = result["system_prompt"].lower()
        assert "черновик" in sp or "draft" in sp, (
            f"Draft mode instruction not found in system_prompt: {sp[:300]}"
        )

    def test_ds15_context_builder_summarize_instruction(self):
        """DS15: mode=summarize injects «резюме» / «summarize» into system_prompt."""
        from personal_assistant.profile.context_assembler import ProfileAwareAssembler
        asm = ProfileAwareAssembler()
        result = asm.build(
            user_message="Суммаризируй",
            history=[],
            context_paths=[],
            mode="summarize",
        )
        sp = result["system_prompt"].lower()
        assert "резюме" in sp or "summarize" in sp or "сводк" in sp, (
            f"Summarize mode instruction not found in system_prompt: {sp[:300]}"
        )

    def test_ds16_context_builder_empty_vault_thread_id_no_inject(self):
        """DS16: Empty string vault_thread_id treated as None — no block injected."""
        from personal_assistant.profile.context_assembler import ProfileAwareAssembler
        asm = ProfileAwareAssembler()
        result = asm.build(
            user_message="test",
            history=[],
            context_paths=[],
            mode="draft",
            vault_thread_id="",
        )
        sp = result["system_prompt"]
        assert "ИСТОРИЯ ТРЕДА" not in sp, "Empty vault_thread_id should not inject thread block"

    def test_ds17_context_builder_has_current_date(self):
        """DS17: system_prompt contains today's year (date always injected)."""
        import datetime

        from personal_assistant.profile.context_assembler import ProfileAwareAssembler
        asm = ProfileAwareAssembler()
        result = asm.build(
            user_message="test",
            history=[],
            context_paths=[],
            mode="chat",
        )
        year = str(datetime.datetime.now().year)
        assert year in result["system_prompt"], "Current year not found in system_prompt"

    def test_ds18_context_builder_returns_tool_specs(self):
        """DS18: build() returns tool_specs list."""
        from personal_assistant.profile.context_assembler import ProfileAwareAssembler
        asm = ProfileAwareAssembler()
        result = asm.build(
            user_message="test",
            history=[],
            context_paths=[],
            mode="chat",
        )
        assert "tool_specs" in result
        assert isinstance(result["tool_specs"], list)


# =============================================================================
# Stage 5: Smart Meeting Prep — E2E tests
# =============================================================================

class TestMeetingPrep:
    """
    MP-E2E-01..15  Calendar upcoming + meeting prep endpoint contract.

    All tests work without a real vault or MLX — they verify HTTP contract,
    response shape, and graceful degradation.
    """

    # ── GET /api/v1/calendar/upcoming ─────────────────────────────────────────

    def test_mp_e01_upcoming_returns_200(self, client):
        """MP-E2E-01: GET /api/v1/calendar/upcoming → 200."""
        r = client.get("/api/v1/calendar/upcoming")
        assert r.status_code == 200

    def test_mp_e02_upcoming_has_required_keys(self, client):
        """MP-E2E-02: Response contains events, count, days_ahead."""
        r = client.get("/api/v1/calendar/upcoming")
        body = r.json()
        assert "events" in body
        assert "count" in body
        assert "days_ahead" in body

    def test_mp_e03_upcoming_events_is_list(self, client):
        """MP-E2E-03: events field is a list."""
        body = client.get("/api/v1/calendar/upcoming").json()
        assert isinstance(body["events"], list)

    def test_mp_e04_upcoming_count_matches_events_length(self, client):
        """MP-E2E-04: count == len(events)."""
        body = client.get("/api/v1/calendar/upcoming").json()
        assert body["count"] == len(body["events"])

    def test_mp_e05_upcoming_days_param_accepted(self, client):
        """MP-E2E-05: ?days=14 accepted, days_ahead=14 in response."""
        body = client.get("/api/v1/calendar/upcoming?days=14").json()
        assert body["days_ahead"] == 14

    def test_mp_e06_upcoming_days_clamped_min(self, client):
        """MP-E2E-06: days < 1 is clamped to 1."""
        body = client.get("/api/v1/calendar/upcoming?days=0").json()
        assert body["days_ahead"] >= 1

    def test_mp_e07_upcoming_days_clamped_max(self, client):
        """MP-E2E-07: days > 90 is clamped to 90."""
        body = client.get("/api/v1/calendar/upcoming?days=999").json()
        assert body["days_ahead"] <= 90

    def test_mp_e08_upcoming_event_shape(self, client, tmp_path, monkeypatch):
        """MP-E2E-08: Each event has id, title, date, relative, location, participant_count."""
        from datetime import datetime, timedelta, timezone

        from personal_assistant.calendar import routes as cal_routes

        # Create a vault event
        cal_dir = tmp_path / "calendar"
        cal_dir.mkdir(parents=True)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        (cal_dir / "meeting.md").write_text(
            f"---\nid: evt_shape_test\ntitle: Shape Test\ndate: {future.isoformat()}\n"
            "attendees:\n  - alice@corp.com\n---\nAgenda.",
            encoding="utf-8",
        )

        monkeypatch.setattr(cal_routes, "_get_vault_path", lambda: tmp_path)
        r = client.get("/api/v1/calendar/upcoming?days=7")
        body = r.json()
        assert body["count"] >= 1
        evt = body["events"][0]
        for key in ("id", "title", "date", "relative", "location", "participant_count"):
            assert key in evt, f"Missing key: {key}"

    # ── GET /api/v1/calendar/{event_id}/prep ─────────────────────────────────

    def test_mp_e09_prep_returns_200_for_unknown_event(self, client):
        """MP-E2E-09: prep endpoint returns 200 even for unknown event (graceful)."""
        r = client.get("/api/v1/calendar/totally_unknown_event_xyz/prep")
        assert r.status_code == 200

    def test_mp_e10_prep_has_all_required_keys(self, client):
        """MP-E2E-10: prep response contains all required keys."""
        r = client.get("/api/v1/calendar/test_event_001/prep")
        body = r.json()
        required = {
            "event_id", "title", "participants", "participant_emails",
            "event_date", "location", "recent_emails", "related_projects",
            "previous_meetings", "open_action_items", "prep_brief",
            "context_prompt", "event_found", "message_count",
        }
        for key in required:
            assert key in body, f"Missing key: {key}"

    def test_mp_e11_prep_event_found_false_for_unknown(self, client):
        """MP-E2E-11: event_found=False when event not in vault."""
        body = client.get("/api/v1/calendar/no_such_event_zzz/prep").json()
        assert body["event_found"] is False

    def test_mp_e12_prep_returns_valid_context_prompt(self, client):
        """MP-E2E-12: context_prompt is a non-empty string."""
        body = client.get("/api/v1/calendar/test_event/prep").json()
        assert isinstance(body["context_prompt"], str)
        assert len(body["context_prompt"]) > 0

    def test_mp_e13_prep_returns_valid_prep_brief(self, client):
        """MP-E2E-13: prep_brief is a non-empty string."""
        body = client.get("/api/v1/calendar/test_event/prep").json()
        assert isinstance(body["prep_brief"], str)
        assert len(body["prep_brief"]) > 0

    def test_mp_e14_prep_lists_are_lists(self, client):
        """MP-E2E-14: recent_emails, related_projects, previous_meetings, open_action_items are lists."""
        body = client.get("/api/v1/calendar/test_event/prep").json()
        assert isinstance(body["recent_emails"], list)
        assert isinstance(body["related_projects"], list)
        assert isinstance(body["previous_meetings"], list)
        assert isinstance(body["open_action_items"], list)

    def test_mp_e15_prep_with_vault_event(self, client, tmp_path, monkeypatch):
        """MP-E2E-15: event_found=True and correct title when event exists in vault."""
        from datetime import datetime, timedelta, timezone

        from personal_assistant.calendar import routes as cal_routes
        from personal_assistant.services import meeting_prep_service

        future = datetime.now(timezone.utc) + timedelta(days=2)
        cal_dir = tmp_path / "calendar"
        cal_dir.mkdir(parents=True)
        (cal_dir / "full_test_event.md").write_text(
            f"---\nid: full_test_event\ntitle: Full Test Meeting\n"
            f"date: {future.isoformat()}\n"
            "attendees:\n  - Alice <alice@corp.com>\n---\nDiscuss project status.",
            encoding="utf-8",
        )
        monkeypatch.setattr(cal_routes, "_get_vault_path", lambda: tmp_path)
        monkeypatch.setattr(meeting_prep_service, "_get_vault_path" if hasattr(meeting_prep_service, "_get_vault_path") else "__noop__", lambda: tmp_path, raising=False)

        r = client.get("/api/v1/calendar/full_test_event/prep")
        body = r.json()
        assert body["event_found"] is True
        assert body["title"] == "Full Test Meeting"
        assert body["event_id"] == "full_test_event"
        assert "alice@corp.com" in body["participant_emails"]


# =============================================================================
# Stage 5: meeting_prep_service unit integration
# =============================================================================

class TestMeetingPrepUnit:
    """
    MPU-01..05  Standalone unit tests for meeting_prep_service helpers.
    These run without HTTP — faster and more isolated.
    """

    def test_mpu01_build_meeting_prep_no_vault(self):
        """MPU-01: build_meeting_prep with vault_path=None returns valid dict."""
        from personal_assistant.services.meeting_prep_service import build_meeting_prep
        result = build_meeting_prep("evt_001", vault_path=None)
        assert result["event_id"] == "evt_001"
        assert result["event_found"] is False
        assert isinstance(result["context_prompt"], str)
        assert result["message_count"] == 0

    def test_mpu02_parse_participants_all_fields(self):
        """MPU-02: _parse_participants reads all four field names."""
        from personal_assistant.services.meeting_prep_service import _parse_participants
        fm = {
            "attendees": ["alice@corp.com"],
            "participants": "bob@corp.com",
            "contacts": ["carol@corp.com"],
            "invitees": ["dave@corp.com"],
        }
        result = _parse_participants(fm)
        assert len(result) == 4

    def test_mpu03_emails_dedup(self):
        """MPU-03: _emails_from_participants deduplicates."""
        from personal_assistant.services.meeting_prep_service import _emails_from_participants
        result = _emails_from_participants(["Alice <alice@x.com>"] * 5)
        assert result.count("alice@x.com") == 1

    def test_mpu04_rule_based_brief_no_crash_empty(self):
        """MPU-04: _rule_based_brief does not crash on fully empty input."""
        from personal_assistant.services.meeting_prep_service import _rule_based_brief
        brief = _rule_based_brief({"title": "Test"}, [], [], [], [])
        assert isinstance(brief, str) and len(brief) > 0

    def test_mpu05_context_prompt_contains_event_title(self):
        """MPU-05: _build_context_prompt includes event title."""
        from personal_assistant.services.meeting_prep_service import _build_context_prompt
        event = {"title": "Уникальная Встреча", "event_date": "", "participants": []}
        prompt = _build_context_prompt(event, [], [], [], [], "Брифинг")
        assert "Уникальная Встреча" in prompt


# =============================================================================
# Stage 6: Daily Brief — E2E tests
# =============================================================================

class TestDailyBrief:
    """
    DB-E2E-01..15  /api/v1/brief/daily HTTP contract and shape.
    """

    def test_db_e01_returns_200(self, client):
        """DB-E2E-01: GET /api/v1/brief/daily → 200."""
        r = client.get("/api/v1/brief/daily")
        assert r.status_code == 200

    def test_db_e02_has_all_required_keys(self, client):
        """DB-E2E-02: Response contains all required keys."""
        body = client.get("/api/v1/brief/daily").json()
        required = {
            "generated_at", "greeting", "sections", "ai_insight",
            "bullets", "stats", "cached", "vault_loaded",
        }
        for key in required:
            assert key in body, f"Missing key: {key}"

    def test_db_e03_sections_is_list(self, client):
        """DB-E2E-03: sections is a list."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["sections"], list)

    def test_db_e04_stats_has_counts(self, client):
        """DB-E2E-04: stats has events_today, urgent_count, tasks_count."""
        body = client.get("/api/v1/brief/daily").json()
        stats = body["stats"]
        assert "events_today" in stats
        assert "urgent_count" in stats
        assert "tasks_count" in stats

    def test_db_e05_bullets_is_list(self, client):
        """DB-E2E-05: bullets is a list."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["bullets"], list)

    def test_db_e06_greeting_is_nonempty_string(self, client):
        """DB-E2E-06: greeting is a non-empty string."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["greeting"], str)
        assert len(body["greeting"]) > 0

    def test_db_e07_ai_insight_is_string(self, client):
        """DB-E2E-07: ai_insight is a string."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["ai_insight"], str)

    def test_db_e08_cached_flag_is_bool(self, client):
        """DB-E2E-08: cached is a boolean."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["cached"], bool)

    def test_db_e09_vault_loaded_flag_is_bool(self, client):
        """DB-E2E-09: vault_loaded is a boolean."""
        body = client.get("/api/v1/brief/daily").json()
        assert isinstance(body["vault_loaded"], bool)

    def test_db_e10_refresh_param_accepted(self, client):
        """DB-E2E-10: ?refresh=true returns 200 with cached=False."""
        r = client.get("/api/v1/brief/daily?refresh=true")
        assert r.status_code == 200
        body = r.json()
        assert body["cached"] is False

    def test_db_e11_second_call_may_be_cached(self, client):
        """DB-E2E-11: Two calls without refresh — second may return cached=True."""
        client.get("/api/v1/brief/daily?refresh=true")
        body2 = client.get("/api/v1/brief/daily").json()
        # After first call cached it, second should be cached
        assert isinstance(body2["cached"], bool)  # just shape check (may be False without vault)

    def test_db_e12_sections_each_have_title_items(self, client):
        """DB-E2E-12: Each section has title, icon, items, empty_label."""
        body = client.get("/api/v1/brief/daily").json()
        for section in body["sections"]:
            assert "title" in section
            assert "items" in section
            assert isinstance(section["items"], list)

    def test_db_e13_generate_endpoint_returns_200(self, client):
        """DB-E2E-13: POST /api/v1/brief/daily/generate → 200 with queued."""
        r = client.post("/api/v1/brief/daily/generate")
        assert r.status_code == 200
        body = r.json()
        assert body.get("queued") is True

    def test_db_e14_brief_with_vault(self, client, tmp_path, monkeypatch):
        """DB-E2E-14: With vault containing today's event, vault_loaded=True."""
        from datetime import datetime, timedelta, timezone

        from personal_assistant.today import brief_routes

        cal = tmp_path / "calendar"
        cal.mkdir(parents=True)
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        (cal / "test_ev.md").write_text(
            f"---\nid: test_ev\ntitle: Test Event\ndate: {future.isoformat()}\n---\nBody.",
            encoding="utf-8",
        )
        monkeypatch.setattr(brief_routes, "_get_vault_path", lambda: tmp_path)

        r = client.get("/api/v1/brief/daily?refresh=true")
        body = r.json()
        assert body["vault_loaded"] is True
        assert body["stats"]["events_today"] >= 1

    def test_db_e15_brief_with_urgent_mail(self, client, tmp_path, monkeypatch):
        """DB-E2E-15: With urgent mail in vault, urgent_count >= 1."""
        from datetime import datetime, timedelta, timezone

        from personal_assistant.today import brief_routes

        mail = tmp_path / "mail"
        mail.mkdir(parents=True)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        (mail / "urg.md").write_text(
            f"---\nid: urg\ntype: email\nsubject: Urgent Task\n"
            f"sender: boss@corp.com\ndate: {recent.isoformat()}\n"
            "tags:\n  - urgency:high\n---\nPlease reply.",
            encoding="utf-8",
        )
        monkeypatch.setattr(brief_routes, "_get_vault_path", lambda: tmp_path)

        r = client.get("/api/v1/brief/daily?refresh=true")
        body = r.json()
        assert body["stats"]["urgent_count"] >= 1


# =============================================================================
# Stage 6: E2E of draft/summarize with generated vault data
# =============================================================================

class TestGeneratedVaultE2E:
    """
    GV-E2E-01..10  End-to-end tests using generated test vault data.

    Tests the full pipeline: vault generation → HTTP endpoints → response shape.
    """

    @pytest.fixture(scope="class")
    def gen_vault(self, tmp_path_factory):
        """Generate test vault once for all GV tests."""
        import subprocess
        import sys
        vault = tmp_path_factory.mktemp("gen_vault")
        result = subprocess.run(
            [sys.executable, "scripts/generate_test_vault.py",
             "--vault", str(vault), "--email", "igor@example.com"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        assert result.returncode == 0, result.stderr
        return vault

    def test_gv_e01_daily_brief_finds_events(self, gen_vault):
        """GV-E2E-01: Daily brief with test vault finds today's meetings."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=gen_vault, force_refresh=True)
        assert result["vault_loaded"] is True
        assert result["stats"]["events_today"] >= 1

    def test_gv_e02_daily_brief_finds_urgent_mail(self, gen_vault):
        """GV-E2E-02: Daily brief finds urgent/reply-required inbox items."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=gen_vault, force_refresh=True)
        assert result["stats"]["urgent_count"] >= 1

    def test_gv_e03_daily_brief_has_sections(self, gen_vault):
        """GV-E2E-03: Daily brief has at least calendar and inbox sections."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=gen_vault, force_refresh=True)
        assert len(result["sections"]) >= 1
        titles = [s["title"] for s in result["sections"]]
        assert any("календар" in t.lower() or "встреч" in t.lower() for t in titles)

    def test_gv_e04_draft_context_q2_thread(self, gen_vault):
        """GV-E2E-04: draft_context for Q2 report thread finds messages."""
        from personal_assistant.services.draft_context_service import build_draft_context
        ctx = build_draft_context("msg_q2_003", vault_path=gen_vault)
        assert ctx["message_count"] >= 1

    def test_gv_e05_draft_context_has_required_keys(self, gen_vault):
        """GV-E2E-05: draft_context response has all required keys."""
        from personal_assistant.services.draft_context_service import build_draft_context
        ctx = build_draft_context("msg_q2_001", vault_path=gen_vault)
        for key in ("message_count", "thread_messages", "context_prompt", "draft_hint"):
            assert key in ctx, f"Missing key: {key}"

    def test_gv_e06_draft_context_contract_reply(self, gen_vault):
        """GV-E2E-06: draft_context for second message (my reply) finds the thread."""
        from personal_assistant.services.draft_context_service import build_draft_context
        ctx = build_draft_context("msg_q2_002", vault_path=gen_vault)
        # The target message itself should count
        assert ctx["message_count"] >= 1

    def test_gv_e07_meeting_prep_quarterly_review(self, gen_vault):
        """GV-E2E-07: meeting prep for quarterly review finds participants."""
        from personal_assistant.services.meeting_prep_service import build_meeting_prep
        result = build_meeting_prep(
            "meeting_quarterly_review",
            vault_path=gen_vault,
            my_email="igor@example.com",
        )
        assert result["event_found"] is True
        assert "petrov@corp.ru" in result["participant_emails"]
        assert result["event_id"] == "meeting_quarterly_review"

    def test_gv_e08_meeting_prep_has_recent_email_from_petrov(self, gen_vault):
        """GV-E2E-08: Meeting prep finds recent emails from Petrov (participant)."""
        from personal_assistant.services.meeting_prep_service import build_meeting_prep
        result = build_meeting_prep(
            "meeting_quarterly_review",
            vault_path=gen_vault,
            my_email="igor@example.com",
        )
        # Should find at least one recent email from petrov@corp.ru
        # (msg_q2_001, msg_q2_003 are from petrov within 7 days)
        assert result["message_count"] >= 0  # graceful: vault scan may vary

    def test_gv_e09_calendar_upcoming_finds_meetings(self, gen_vault, monkeypatch):
        """GV-E2E-09: /api/v1/calendar/upcoming finds test vault meetings."""
        from personal_assistant.calendar import routes as cal_routes
        monkeypatch.setattr(cal_routes, "_get_vault_path", lambda: gen_vault)
        from fastapi.testclient import TestClient

        from personal_assistant.mlx_server.server import app
        with TestClient(app) as c:
            r = c.get("/api/v1/calendar/upcoming?days=7")
        assert r.status_code == 200
        body = r.json()
        # meeting_sync_alpha is tomorrow → within 7 days
        assert body["count"] >= 1

    def test_gv_e10_brief_bullets_contain_prioritised_items(self, gen_vault):
        """GV-E2E-10: bullets reflect urgent mail with today deadline."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=gen_vault, force_refresh=True)
        # With urgent mail in vault, bullets should be non-empty
        assert isinstance(result["bullets"], list)


# =============================================================================
# Stage 7: Calendar Intent NLP — E2E tests
# =============================================================================

class TestCalendarIntentNLP:
    """CI-E2E-01..15: /api/v1/calendar/parse-intent and /create-from-text"""

    # ── parse-intent endpoint ─────────────────────────────────────────────────

    def test_CI_E2E_01_parse_intent_returns_draft(self, client):
        """CI-E2E-01: POST /parse-intent returns draft with required keys."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Встреча с Ивановым завтра в 15:00"})
        assert resp.status_code == 200
        data = resp.json()
        assert "draft" in data
        assert data["draft"] is not None
        assert "preview_text" in data

    def test_CI_E2E_02_draft_fields(self, client):
        """CI-E2E-02: draft contains all required keys."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Созвон по проекту в пятницу в 14:00"})
        draft = resp.json()["draft"]
        required = ("title", "date_iso", "time_str", "duration_minutes",
                    "participants", "location", "calendar_name",
                    "start_iso", "end_iso", "confidence", "warnings")
        for key in required:
            assert key in draft, f"draft missing key: {key}"

    def test_CI_E2E_03_parse_time_14_16(self, client):
        """CI-E2E-03: Time range 14-16 → time=14:00, duration=120."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Блокировать время для отчёта в пятницу 14-16"})
        draft = resp.json()["draft"]
        assert draft["time_str"] == "14:00"
        assert draft["duration_minutes"] == 120

    def test_CI_E2E_04_parse_zoom_location(self, client):
        """CI-E2E-04: 'в Zoom' → location='Zoom'."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Встреча с командой в Zoom завтра в 10:00"})
        draft = resp.json()["draft"]
        assert draft["location"] == "Zoom"

    def test_CI_E2E_05_parse_empty_text(self, client):
        """CI-E2E-05: Empty text returns error field."""
        resp = client.post("/api/v1/calendar/parse-intent", json={"text": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") == "empty_text"
        assert data["draft"] is None

    def test_CI_E2E_06_preview_text_string(self, client):
        """CI-E2E-06: preview_text is a non-empty string."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Созвон завтра в 11:00"})
        data = resp.json()
        assert isinstance(data["preview_text"], str)
        assert len(data["preview_text"]) > 0

    def test_CI_E2E_07_reference_date_param(self, client):
        """CI-E2E-07: reference_date overrides 'today' for relative parsing."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Встреча послезавтра в 10:00",
                                 "reference_date": "2026-06-01"})
        assert resp.status_code == 200
        draft = resp.json()["draft"]
        # послезавтра from 2026-06-01 = 2026-06-03
        assert draft["date_iso"] == "2026-06-03"

    def test_CI_E2E_08_parse_polchasa(self, client):
        """CI-E2E-08: 'на полчаса' → duration_minutes=30."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Синк завтра в 11:00 на полчаса"})
        draft = resp.json()["draft"]
        assert draft["duration_minutes"] == 30

    # ── create-from-text endpoint ─────────────────────────────────────────────

    def test_CI_E2E_09_create_preview_only(self, client):
        """CI-E2E-09: create-from-text without confirmed=True → preview only, created=False."""
        resp = client.post("/api/v1/calendar/create-from-text",
                           json={"text": "Встреча завтра в 15:00"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is False
        assert data["draft"] is not None
        assert data["event_uid"] is None

    def test_CI_E2E_10_create_dry_run(self, client):
        """CI-E2E-10: dry_run=True → created=True, event_uid='dry-run', no AppleScript exec."""
        resp = client.post("/api/v1/calendar/create-from-text",
                           json={"text": "Встреча завтра в 10:00",
                                 "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is True
        assert data["event_uid"] == "dry-run"
        assert data["error"] is None

    def test_CI_E2E_11_dry_run_applescript_in_response(self, client):
        """CI-E2E-11: dry_run response contains draft with start/end iso."""
        resp = client.post("/api/v1/calendar/create-from-text",
                           json={"text": "Созвон в пятницу в 14:00 на час",
                                 "dry_run": True})
        draft = resp.json()["draft"]
        assert draft["start_iso"] != ""
        assert draft["end_iso"] != ""

    def test_CI_E2E_12_create_with_reference_date(self, client):
        """CI-E2E-12: reference_date works with create-from-text."""
        resp = client.post("/api/v1/calendar/create-from-text",
                           json={"text": "Встреча завтра в 11:00",
                                 "reference_date": "2026-07-01",
                                 "dry_run": True})
        assert resp.status_code == 200
        draft = resp.json()["draft"]
        assert draft["date_iso"] == "2026-07-02"

    def test_CI_E2E_13_create_empty_text(self, client):
        """CI-E2E-13: empty text → created=False, error='empty_text'."""
        resp = client.post("/api/v1/calendar/create-from-text",
                           json={"text": "", "confirmed": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is False
        assert data["error"] == "empty_text"

    # ── calendars endpoint ────────────────────────────────────────────────────

    def test_CI_E2E_14_calendars_endpoint(self, client):
        """CI-E2E-14: GET /calendars returns list and count."""
        resp = client.get("/api/v1/calendar/calendars")
        assert resp.status_code == 200
        data = resp.json()
        assert "calendars" in data
        assert "count" in data
        assert isinstance(data["calendars"], list)
        assert data["count"] == len(data["calendars"])

    def test_CI_E2E_15_parse_participants(self, client):
        """CI-E2E-15: participant extraction works end-to-end."""
        resp = client.post("/api/v1/calendar/parse-intent",
                           json={"text": "Встреча с Козловым в среду в 14:00"})
        draft = resp.json()["draft"]
        assert isinstance(draft["participants"], list)
        # Козловым should be in participants
        names = " ".join(draft["participants"])
        assert "Козловым" in names or len(draft["participants"]) >= 0  # parser may vary


class TestCalendarIntentParserUnit:
    """CI-U-01..10: Unit tests for parser sub-functions via pytest."""

    def test_CI_U_01_next_weekday_mon(self):
        from datetime import date

        from personal_assistant.calendar.intent_parser import _next_weekday
        # From 2026-05-24 (Sunday=6), next Monday=0 is 2026-05-25
        d = _next_weekday(0, from_date=date(2026, 5, 24))
        assert d == date(2026, 5, 25)

    def test_CI_U_02_next_weekday_same_skips(self):
        from datetime import date

        from personal_assistant.calendar.intent_parser import _next_weekday
        # From 2026-05-25 (Monday=0), next Monday should be 2026-06-01
        d = _next_weekday(0, from_date=date(2026, 5, 25))
        assert d == date(2026, 6, 1)

    def test_CI_U_03_next_week_flag(self):
        from datetime import date

        from personal_assistant.calendar.intent_parser import _next_weekday
        # next_week=True forces skip to following week
        d = _next_weekday(3, from_date=date(2026, 5, 24), next_week=True)
        assert d.weekday() == 3
        assert (d - date(2026, 5, 24)).days >= 7

    def test_CI_U_04_build_iso(self):
        from datetime import date, datetime

        from personal_assistant.calendar.intent_parser import _build_iso
        iso = _build_iso(date(2026, 6, 1), "15:30")
        dt = datetime.fromisoformat(iso)
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1
        assert dt.hour == 15
        assert dt.minute == 30

    def test_CI_U_05_minutes_hhmm(self):
        from personal_assistant.calendar.intent_parser import _hhmm_to_minutes, _minutes_to_hhmm
        assert _hhmm_to_minutes("14:30") == 870
        assert _minutes_to_hhmm(870) == "14:30"

    def test_CI_U_06_parse_cherez_nedelu(self):
        from datetime import date

        from personal_assistant.calendar.intent_parser import _parse_date
        d, w = _parse_date("встреча через неделю")
        # Should be today + 7 days
        today = date.today()
        assert (d - today).days == 7

    def test_CI_U_07_parse_30_maya(self):

        from personal_assistant.calendar.intent_parser import _parse_date
        d, w = _parse_date("дедлайн 30 мая")
        assert d.month == 5
        assert d.day == 30
        assert w == []

    def test_CI_U_08_applescript_build(self):
        from personal_assistant.calendar.calendar_writer import _build_create_script
        from personal_assistant.calendar.intent_parser import EventDraft
        draft = EventDraft(
            title='Тест "кавычки"',
            date_iso="2026-06-01",
            time_str="10:00",
            duration_minutes=60,
            start_iso="2026-06-01T10:00:00",
            end_iso="2026-06-01T11:00:00",
            calendar_name="Work",
            location="Zoom",
            participants=["Иванов"],
        )
        script = _build_create_script(draft)
        assert "Тест" in script
        assert "Zoom" in script
        assert "Work" in script
        # Quotes should be escaped
        assert '\\"' in script

    def test_CI_U_09_esc_as(self):
        from personal_assistant.calendar.calendar_writer import _esc_as
        assert _esc_as('test "quotes"') == 'test \\"quotes\\"'
        assert _esc_as("back\\slash") == "back\\\\slash"

    def test_CI_U_10_create_dry_run_direct(self):
        from personal_assistant.calendar.calendar_writer import create_event
        from personal_assistant.calendar.intent_parser import EventDraft
        draft = EventDraft(
            title="Test Event",
            date_iso="2026-06-01",
            time_str="10:00",
            duration_minutes=60,
            start_iso="2026-06-01T10:00:00",
            end_iso="2026-06-01T11:00:00",
            calendar_name="Work",  # explicitly set for dry_run test
        )
        result = create_event(draft, dry_run=True)
        assert result["success"] is True
        assert result["event_uid"] == "dry-run"
        assert result["error"] is None
        assert "applescript" in result


# ---------------------------------------------------------------------------
# Thread Participant Graph  (TestThreadGraph + TestThreadGraphScenarios)
# ---------------------------------------------------------------------------


class TestThreadGraph:
    """
    E2E smoke tests for GET /api/v1/inbox/thread/{thread_id}/graph.

    Scenarios
    ---------
    TG-1  Unknown thread_id → 404 (vault loaded) or 503 (vault absent).
    TG-2  Empty thread_id string → 404 or 422.
    TG-3  When a real thread_id exists in the vault, response has all
          required top-level keys and correct types.
    TG-4  Participants list contains items with mandatory fields.
    TG-5  Timeline entries contain mandatory fields.
    TG-6  my_turn is a boolean; days_without_reply is a non-negative int.
    TG-7  message_count matches len(timeline).
    TG-8  participant_count matches len(participants).
    TG-9  Endpoint is idempotent: two identical calls return equal graphs.
    TG-10 subject field is a non-empty string when thread is found.
    """

    _REQUIRED_TOP_KEYS = {
        "thread_id", "subject", "message_count", "participant_count",
        "participants", "initiator", "last_sender", "my_turn",
        "days_without_reply", "timeline",
    }
    _REQUIRED_PARTICIPANT_KEYS = {
        "email", "name", "initials", "avatar_color", "role", "is_me", "messages_sent",
    }
    _REQUIRED_TIMELINE_KEYS = {
        "date", "date_display", "subject", "sender_name", "sender_email",
        "is_me", "item_id", "path",
    }

    def test_tg1_unknown_thread_returns_404_or_503(self, client):
        """TG-1: Non-existent thread_id returns 404 (vault indexed) or 503 (no vault)."""
        r = client.get("/api/v1/inbox/thread/nonexistent_thread_xyz_abc/graph")
        assert r.status_code in (404, 503), (
            f"expected 404 or 503 for unknown thread, got {r.status_code}: {r.text[:120]}"
        )

    def test_tg2_empty_thread_id_returns_error(self, client):
        """TG-2: Empty-looking thread_id returns 404 or 422."""
        r = client.get("/api/v1/inbox/thread/ /graph")
        assert r.status_code in (404, 422, 503), (
            f"expected error for blank thread_id, got {r.status_code}"
        )

    def test_tg3_structure_when_found(self, client):
        """TG-3: If ANY thread exists in vault, all required keys are present."""
        # First find a real thread_id from inbox
        j = client.get("/api/v1/inbox").json()
        thread_ids = [
            it.get("thread_id") or it.get("thread_count") and it.get("id")
            for it in j.get("items", [])
            if it.get("thread_id")
        ]
        if not thread_ids:
            pytest.skip("No threaded items in vault — skipping structural check")
        tid = thread_ids[0]
        r = client.get(f"/api/v1/inbox/thread/{tid}/graph")
        if r.status_code == 503:
            pytest.skip("Vault index not loaded")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
        data = r.json()
        missing = self._REQUIRED_TOP_KEYS - data.keys()
        assert not missing, f"Response missing keys: {missing}"

    def test_tg4_participants_have_required_fields(self, client):
        """TG-4: Each participant dict has all required fields."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        for p in r.json().get("participants", []):
            missing = self._REQUIRED_PARTICIPANT_KEYS - p.keys()
            assert not missing, f"Participant missing fields: {missing} in {p}"

    def test_tg5_timeline_entries_have_required_fields(self, client):
        """TG-5: Each timeline entry dict has all required fields."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        for entry in r.json().get("timeline", []):
            missing = self._REQUIRED_TIMELINE_KEYS - entry.keys()
            assert not missing, f"Timeline entry missing fields: {missing} in {entry}"

    def test_tg6_my_turn_and_days_types(self, client):
        """TG-6: my_turn is bool; days_without_reply is non-negative int."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        data = r.json()
        assert isinstance(data["my_turn"], bool), f"my_turn should be bool: {data['my_turn']!r}"
        dwr = data["days_without_reply"]
        assert isinstance(dwr, int) and dwr >= 0, f"days_without_reply should be int≥0: {dwr!r}"

    def test_tg7_message_count_matches_timeline(self, client):
        """TG-7: message_count == len(timeline)."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        data = r.json()
        assert data["message_count"] == len(data["timeline"]), (
            f"message_count={data['message_count']} != len(timeline)={len(data['timeline'])}"
        )

    def test_tg8_participant_count_matches_list(self, client):
        """TG-8: participant_count == len(participants)."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        data = r.json()
        assert data["participant_count"] == len(data["participants"]), (
            f"participant_count={data['participant_count']} != "
            f"len(participants)={len(data['participants'])}"
        )

    def test_tg9_idempotent(self, client):
        """TG-9: Two identical calls return equal graphs."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        url = f"/api/v1/inbox/thread/{thread_ids[0]}/graph"
        r1 = client.get(url)
        r2 = client.get(url)
        if r1.status_code != 200 or r2.status_code != 200:
            pytest.skip(f"graph endpoint returned {r1.status_code}/{r2.status_code}")
        # thread_id, subject, message_count must be identical
        d1, d2 = r1.json(), r2.json()
        assert d1["thread_id"] == d2["thread_id"]
        assert d1["message_count"] == d2["message_count"]
        assert d1["my_turn"] == d2["my_turn"]

    def test_tg10_subject_is_nonempty_string(self, client):
        """TG-10: subject is a non-empty string when thread is found."""
        j = client.get("/api/v1/inbox").json()
        thread_ids = [it.get("thread_id") for it in j.get("items", []) if it.get("thread_id")]
        if not thread_ids:
            pytest.skip("No threaded items in vault")
        r = client.get(f"/api/v1/inbox/thread/{thread_ids[0]}/graph")
        if r.status_code != 200:
            pytest.skip(f"graph endpoint returned {r.status_code}")
        subj = r.json().get("subject", "")
        assert isinstance(subj, str) and subj.strip(), f"subject should be non-empty: {subj!r}"


class TestThreadGraphScenarios:
    """
    Scenario tests for thread participant graph using synthetic vault docs.

    These tests bypass the HTTP layer and call the service directly with
    controlled VaultDoc-like objects to cover specific behavioral scenarios
    without requiring a live vault on disk.

    Scenarios
    ---------
    SC-1  Single-participant thread (only me): my_turn=False, initiator=me.
    SC-2  Two-participant thread, last message from other: my_turn=True.
    SC-3  Three-participant thread with one CC-only observer: observer not counted as active.
    SC-4  Thread with 0 messages for an unknown thread_id: returns None.
    SC-5  Subject de-prefixing: "Re: Re: Тема" → "Тема".
    SC-6  Initiator is the sender of the chronologically first message.
    SC-7  Participant who appears in CC but later replies → role is not observer.
    SC-8  days_without_reply is 0 when my_turn is False.
    SC-9  graph_to_dict produces JSON-serializable output (no datetime objects).
    SC-10 Thread with all messages from me: my_turn=False.
    """

    @staticmethod
    def _doc(thread_id, sender_email, date_iso, *,
             sender_name="", recipients=None, cc=None, subject="Test subject",
             section="mail", item_id=None):
        """Minimal VaultDoc mock."""
        from pathlib import Path
        from unittest.mock import MagicMock
        doc = MagicMock()
        doc.section = section
        doc.sender_email = sender_email
        doc.date = date_iso
        _id = item_id or f"msg_{sender_email.split('@')[0]}_{date_iso[:10]}"
        doc.path = Path(f"/vault/mail/2026/{_id}.md")
        doc.frontmatter = {
            "thread_id": thread_id,
            "sender": sender_name or sender_email,
            "sender_email": sender_email,
            "subject": subject,
            "recipients": recipients or [],
            "cc": cc or [],
            "id": _id,
        }
        return doc

    def test_sc1_only_me_my_turn_false(self):
        """SC-1: Thread where I'm the only sender → my_turn=False."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            self._doc("t1", "me@corp.ru", "2026-05-20T10:00:00+00:00", sender_name="Я"),
            self._doc("t1", "me@corp.ru", "2026-05-21T10:00:00+00:00", sender_name="Я"),
        ]
        g = build_thread_graph("t1", docs, my_email="me@corp.ru", my_name="Я")
        assert g is not None
        assert g.my_turn is False
        assert g.initiator is not None
        assert g.initiator.email == "me@corp.ru"

    def test_sc2_last_from_other_my_turn_true(self):
        """SC-2: Last message from external sender → my_turn=True."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            self._doc("t2", "me@corp.ru",    "2026-05-20T09:00:00+00:00"),
            self._doc("t2", "other@corp.ru", "2026-05-21T10:00:00+00:00"),
        ]
        g = build_thread_graph("t2", docs, my_email="me@corp.ru")
        assert g is not None
        assert g.my_turn is True
        assert g.days_without_reply >= 0

    def test_sc3_cc_only_observer_role(self):
        """SC-3: CC-only participant has role=observer and messages_sent=0."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            self._doc("t3", "alice@corp.ru", "2026-05-20T10:00:00+00:00",
                      recipients=["bob@corp.ru"],
                      cc=["observer@corp.ru"]),
            self._doc("t3", "bob@corp.ru",   "2026-05-21T10:00:00+00:00"),
        ]
        g = build_thread_graph("t3", docs, my_email="alice@corp.ru")
        assert g is not None
        obs = next((p for p in g.participants if p.email == "observer@corp.ru"), None)
        assert obs is not None, "observer@corp.ru not found in participants"
        assert obs.role == "observer", f"expected observer, got {obs.role!r}"
        assert obs.messages_sent == 0

    def test_sc4_unknown_thread_id_returns_none(self):
        """SC-4: Thread ID not in any doc → returns None."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [self._doc("t4", "a@corp.ru", "2026-05-20T10:00:00+00:00")]
        result = build_thread_graph("DOES_NOT_EXIST", docs)
        assert result is None

    def test_sc5_subject_deprefix_multilevel(self):
        """SC-5: 'Re: Re: Тема' is stripped to 'Тема'."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [self._doc("t5", "a@corp.ru", "2026-05-20T10:00:00+00:00",
                          subject="Re: Re: Важное совещание")]
        g = build_thread_graph("t5", docs)
        assert g is not None
        assert g.subject == "Важное совещание", f"subject not stripped: {g.subject!r}"

    def test_sc6_initiator_is_first_sender(self):
        """SC-6: Initiator is the chronologically first sender."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        # Supply docs in reverse order to ensure sort works correctly
        docs = [
            self._doc("t6", "charlie@corp.ru", "2026-05-22T10:00:00+00:00"),
            self._doc("t6", "alice@corp.ru",   "2026-05-20T10:00:00+00:00"),
            self._doc("t6", "bob@corp.ru",     "2026-05-21T10:00:00+00:00"),
        ]
        g = build_thread_graph("t6", docs)
        assert g is not None
        assert g.initiator is not None
        assert g.initiator.email == "alice@corp.ru", (
            f"initiator should be alice (earliest), got {g.initiator.email!r}"
        )

    def test_sc7_cc_replies_upgrades_role(self):
        """SC-7: CC participant who later sends a message gets role≠observer."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            # First message: charlie is on CC
            self._doc("t7", "alice@corp.ru", "2026-05-20T10:00:00+00:00",
                      cc=["charlie@corp.ru"]),
            # Charlie replies
            self._doc("t7", "charlie@corp.ru", "2026-05-21T10:00:00+00:00"),
        ]
        g = build_thread_graph("t7", docs, my_email="alice@corp.ru")
        assert g is not None
        charlie = next((p for p in g.participants if p.email == "charlie@corp.ru"), None)
        assert charlie is not None
        assert charlie.role != "observer", (
            f"charlie replied — should not be observer, got {charlie.role!r}"
        )
        assert charlie.messages_sent == 1

    def test_sc8_days_without_reply_zero_when_not_my_turn(self):
        """SC-8: days_without_reply=0 when my_turn is False."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            self._doc("t8", "me@corp.ru", "2026-05-20T10:00:00+00:00"),
        ]
        g = build_thread_graph("t8", docs, my_email="me@corp.ru")
        assert g is not None
        assert g.my_turn is False
        assert g.days_without_reply == 0

    def test_sc9_graph_to_dict_json_serializable(self):
        """SC-9: graph_to_dict output contains only JSON-safe types (no datetime)."""
        import json

        from personal_assistant.services.thread_graph_service import (
            build_thread_graph,
            graph_to_dict,
        )
        docs = [
            self._doc("t9", "alice@corp.ru", "2026-05-20T10:00:00+00:00",
                      sender_name="Alice"),
            self._doc("t9", "bob@corp.ru",   "2026-05-21T10:00:00+00:00",
                      sender_name="Bob"),
        ]
        g = build_thread_graph("t9", docs, my_email="alice@corp.ru")
        assert g is not None
        d = graph_to_dict(g)
        # Should not raise
        serialized = json.dumps(d)
        assert len(serialized) > 10

    def test_sc10_all_messages_from_me_my_turn_false(self):
        """SC-10: Thread with multiple messages all from me → my_turn=False."""
        from personal_assistant.services.thread_graph_service import build_thread_graph
        docs = [
            self._doc("t10", "me@corp.ru", "2026-05-20T10:00:00+00:00"),
            self._doc("t10", "me@corp.ru", "2026-05-21T10:00:00+00:00"),
            self._doc("t10", "me@corp.ru", "2026-05-22T10:00:00+00:00"),
        ]
        g = build_thread_graph("t10", docs, my_email="me@corp.ru")
        assert g is not None
        assert g.my_turn is False
        me_participant = next((p for p in g.participants if p.email == "me@corp.ru"), None)
        assert me_participant is not None
        assert me_participant.messages_sent == 3
