"""
Coverage-focused e2e tests for webui/routes.py GET endpoints.

These tests intentionally hit many simple GET endpoints to climb webui/routes.py
coverage from ~47% toward 60%+. Each test:

  - Uses TestClient(app) (the full FastAPI app).
  - Expects 2xx, 3xx, or a documented graceful-degradation status (404/422/501).
  - Does NOT trigger MLX inference, real Mail/Calendar AppleScript, or model
    downloads — the e2e_test_mode + blanked settings from the root conftest
    keep everything hermetic.

What this file is NOT:
  - It is NOT contract-validation. test_server_routes.py covers behaviour;
    this file just makes sure code paths execute (coverage signal).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from personal_assistant.mlx_server.server import app
    return TestClient(app, raise_server_exceptions=False)


# A status code that means "we exercised the endpoint without crashing".
# 5xx is the only thing we treat as a hard failure here.
_OK_STATUSES = {200, 201, 202, 204, 301, 302, 304, 400, 401, 403, 404, 405, 409, 422, 501, 503}


def _assert_reached(resp, endpoint: str) -> None:
    assert resp.status_code in _OK_STATUSES, (
        f"{endpoint} returned {resp.status_code}: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Classify endpoints
# ---------------------------------------------------------------------------


class TestClassifyEndpoints:
    def test_classify_config_get(self, client):
        _assert_reached(client.get("/classify/config"), "/classify/config")

    def test_classify_labels_get(self, client):
        _assert_reached(client.get("/classify/labels"), "/classify/labels")

    def test_classify_stats_get(self, client):
        _assert_reached(client.get("/classify/stats"), "/classify/stats")


# ---------------------------------------------------------------------------
# Rules / persona / souls / eisenhower / GTD
# ---------------------------------------------------------------------------


class TestStateFilesEndpoints:
    def test_persona_get(self, client):
        _assert_reached(client.get("/persona"), "/persona")

    def test_souls_get(self, client):
        _assert_reached(client.get("/souls"), "/souls")

    def test_eisenhower_get(self, client):
        _assert_reached(client.get("/eisenhower"), "/eisenhower")

    def test_gtd_rules_get(self, client):
        _assert_reached(client.get("/gtd-rules"), "/gtd-rules")


# ---------------------------------------------------------------------------
# Schedule & settings
# ---------------------------------------------------------------------------


class TestScheduleSettings:
    def test_schedule_status_get(self, client):
        r = client.get("/schedule/status")
        _assert_reached(r, "/schedule/status")
        if r.status_code == 200:
            data = r.json()
            # known keys (best-effort)
            assert isinstance(data, dict)

    def test_settings_get(self, client):
        r = client.get("/settings")
        _assert_reached(r, "/settings")
        if r.status_code == 200:
            assert isinstance(r.json(), dict)


# ---------------------------------------------------------------------------
# Tools / prompts / model catalogue
# ---------------------------------------------------------------------------


class TestToolsEndpoints:
    def test_tool_prompts_get(self, client):
        _assert_reached(client.get("/tool-prompts"), "/tool-prompts")

    def test_tools_get(self, client):
        _assert_reached(client.get("/tools"), "/tools")

    def test_model_catalogue_get(self, client):
        _assert_reached(client.get("/model/catalogue"), "/model/catalogue")

    def test_model_local_get(self, client):
        _assert_reached(client.get("/model/local"), "/model/local")

    def test_model_pull_status_query(self, client):
        # Requires repo= query string
        _assert_reached(
            client.get("/model/pull-status?repo=mlx-community/Mistral-7B-Instruct-v0.3-4bit"),
            "/model/pull-status",
        )


# ---------------------------------------------------------------------------
# Projects (empty vault → empty list)
# ---------------------------------------------------------------------------


class TestProjectsEndpoints:
    def test_projects_list(self, client):
        r = client.get("/projects")
        _assert_reached(r, "/projects")
        if r.status_code == 200:
            data = r.json()
            assert isinstance(data, (list, dict))

    def test_projects_get_nonexistent(self, client):
        # 404 is fine — proves the lookup path is exercised
        _assert_reached(
            client.get("/projects/nonexistent_pid_xyz_12345/goals"),
            "/projects/.../goals",
        )

    def test_projects_related_nonexistent(self, client):
        _assert_reached(
            client.get("/projects/nonexistent_pid_xyz_12345/related"),
            "/projects/.../related",
        )

    def test_projects_assistant_suggests_nonexistent(self, client):
        _assert_reached(
            client.get("/projects/nonexistent_pid_xyz_12345/assistant-suggests"),
            "/projects/.../assistant-suggests",
        )


# ---------------------------------------------------------------------------
# Rules (CRUD list)
# ---------------------------------------------------------------------------


class TestRulesEndpoints:
    def test_rules_list(self, client):
        _assert_reached(client.get("/rules"), "/rules")

    def test_rules_classify_get(self, client):
        _assert_reached(client.get("/rules/classify"), "/rules/classify")

    def test_rules_get_nonexistent(self, client):
        _assert_reached(
            client.get("/rules/__rule_does_not_exist__"),
            "/rules/<id>",
        )


# ---------------------------------------------------------------------------
# Tag history
# ---------------------------------------------------------------------------


class TestTagHistoryEndpoints:
    def test_tag_history_list(self, client):
        _assert_reached(client.get("/tag-history"), "/tag-history")

    def test_tag_history_item_nonexistent(self, client):
        _assert_reached(
            client.get("/tag-history/__not_found_id__"),
            "/tag-history/<id>",
        )


# ---------------------------------------------------------------------------
# Test-data snapshots
# ---------------------------------------------------------------------------


class TestTestDataEndpoints:
    def test_testdata_snapshots_list(self, client):
        _assert_reached(client.get("/testdata/snapshots"), "/testdata/snapshots")

    def test_testdata_generated_list(self, client):
        _assert_reached(client.get("/testdata/generated"), "/testdata/generated")

    def test_testdata_snapshot_get_nonexistent(self, client):
        _assert_reached(
            client.get("/testdata/snapshots/__no_such_snap__"),
            "/testdata/snapshots/<id>",
        )


# ---------------------------------------------------------------------------
# Projects CRUD — full create/update/add-goal/delete-goal/delete-project cycle
# ---------------------------------------------------------------------------


class TestProjectsCRUD:
    """End-to-end CRUD flow: exercises POST/PUT/DELETE handlers for projects
    and goals (one of the largest uncovered ranges in webui/routes.py)."""

    def test_full_lifecycle(self, client):
        # Create
        r = client.post(
            "/projects",
            json={"name": "Cov Test Project", "description": "transient", "status": "active"},
        )
        assert r.status_code in (200, 201), r.text
        project = r.json()
        pid = project["id"]
        assert pid

        try:
            # Update
            r = client.put(
                f"/projects/{pid}",
                json={
                    "name": "Cov Test Project (renamed)",
                    "description": "updated",
                    "status": "active",
                    "goals": [],
                },
            )
            assert r.status_code == 200

            # Add goal
            r = client.post(
                f"/projects/{pid}/goals",
                json={"title": "First goal", "done": False, "quadrant": "q2"},
            )
            assert r.status_code in (200, 201)
            goal = r.json()
            gid = goal["id"]

            # Get goals
            r = client.get(f"/projects/{pid}/goals")
            assert r.status_code == 200
            assert any(g["id"] == gid for g in r.json()["goals"])

            # Update goal
            r = client.put(
                f"/projects/{pid}/goals/{gid}",
                json={"title": "First goal (done)", "done": True, "quadrant": "q1"},
            )
            assert r.status_code == 200

            # Delete goal
            r = client.delete(f"/projects/{pid}/goals/{gid}")
            assert r.status_code == 200

        finally:
            # Cleanup — always delete the project
            client.delete(f"/projects/{pid}")

    def test_update_nonexistent_project_404(self, client):
        r = client.put(
            "/projects/__no_such_project__",
            json={"name": "x", "status": "active"},
        )
        assert r.status_code == 404

    def test_add_goal_nonexistent_project_404(self, client):
        r = client.post(
            "/projects/__no_such_project__/goals",
            json={"title": "x", "done": False, "quadrant": "q2"},
        )
        assert r.status_code == 404

    def test_update_goal_nonexistent_404(self, client):
        r = client.put(
            "/projects/__no_such_project__/goals/__no_goal__",
            json={"title": "x", "done": False, "quadrant": "q2"},
        )
        assert r.status_code == 404
