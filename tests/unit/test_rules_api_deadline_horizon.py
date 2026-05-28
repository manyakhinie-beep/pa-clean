"""
Regression: deadline_horizon должно сохраняться через POST/PUT /rules.

Был баг: Pydantic-модель ``RuleBody`` не содержала поле
``deadline_horizon``, поэтому при сохранении правила из UI значение
поля «Срок» (например «На этой неделе») молча отбрасывалось — и при
следующей загрузке dropdown возвращался к дефолту «Любой».

Тесты ниже фиксируют контракт: что POST и PUT принимают, сохраняют
и возвращают ``deadline_horizon``.  То же для GTD-rules (там
Pydantic-модель ``GtdRulesBody`` уже принимает ``rules: list`` без
строгой схемы — но проверим явно, что значение доезжает).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch):
    """FastAPI client + перенаправление rules.json и gtd_rules.json в tmp."""
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.server import app
    settings.e2e_test_mode = True

    rules_path = tmp_path / "rules.json"
    gtd_path = tmp_path / "gtd_rules.json"
    monkeypatch.setattr(
        "personal_assistant.webui.routes._RULES_FILE_WEBUI", rules_path
    )
    monkeypatch.setattr(
        "personal_assistant.webui.routes._GTD_FILE", gtd_path
    )
    return TestClient(app), rules_path, gtd_path


# ----------------------------------------------------------------------
# POST /rules: создать правило с deadline_horizon
# ----------------------------------------------------------------------


def test_create_rule_preserves_deadline_horizon(tmp_path, monkeypatch):
    client, rules_path, _ = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/rules",
        json={
            "name": "Срочно на этой неделе",
            "keywords": ["счёт"],
            "eisenhower_quadrant": "q1",
            "action_type": "execute",
            "deadline_horizon": "this_week",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deadline_horizon"] == "this_week", (
        f"POST не сохранил deadline_horizon — вернулось: {body!r}"
    )

    # Persisted на диск с правильным значением
    saved = json.loads(rules_path.read_text(encoding="utf-8"))
    assert saved[0]["deadline_horizon"] == "this_week"


def test_create_rule_defaults_to_any_when_field_missing(tmp_path, monkeypatch):
    """Back-compat: правила, отправленные старым UI без поля, должны
    получить default 'any' — а не падать с ValidationError."""
    client, rules_path, _ = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/rules",
        json={"name": "Legacy rule", "keywords": ["foo"]},
    )
    assert resp.status_code == 200
    assert resp.json()["deadline_horizon"] == "any"


# ----------------------------------------------------------------------
# PUT /rules/{id}: обновить deadline_horizon
# ----------------------------------------------------------------------


def test_update_rule_can_change_deadline_horizon(tmp_path, monkeypatch):
    client, rules_path, _ = _client(tmp_path, monkeypatch)
    # Создаём правило с horizon=any
    created = client.post(
        "/rules",
        json={"name": "x", "keywords": ["x"], "deadline_horizon": "any"},
    ).json()
    rid = created["id"]

    # Обновляем на this_month
    resp = client.put(
        f"/rules/{rid}",
        json={
            "name": "x",
            "keywords": ["x"],
            "eisenhower_quadrant": "q1",
            "action_type": "execute",
            "deadline_horizon": "this_month",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["rule"]["deadline_horizon"] == "this_month"

    # GET /rules возвращает то же значение
    listed = client.get("/rules").json()["rules"]
    assert listed[0]["deadline_horizon"] == "this_month"


# ----------------------------------------------------------------------
# GTD rules — PUT /gtd-rules сохраняет deadline_horizon
# ----------------------------------------------------------------------


def test_gtd_rules_save_preserves_deadline_horizon(tmp_path, monkeypatch):
    client, _, gtd_path = _client(tmp_path, monkeypatch)
    payload = {
        "rules": [
            {
                "id": "g1",
                "keyword": "срочно",
                "action": "inbox",
                "quadrant": "q1",
                "deadline_horizon": "today",
            },
            {
                "id": "g2",
                "keyword": "договор",
                "action": "next",
                "quadrant": "q2",
                "deadline_horizon": "this_month",
            },
        ]
    }
    resp = client.put("/gtd-rules", json=payload)
    assert resp.status_code == 200

    saved = json.loads(gtd_path.read_text(encoding="utf-8"))
    assert saved["rules"][0]["deadline_horizon"] == "today"
    assert saved["rules"][1]["deadline_horizon"] == "this_month"

    # Round-trip — GET возвращает то же
    fetched = client.get("/gtd-rules").json()["rules"]
    assert fetched[0]["deadline_horizon"] == "today"
    assert fetched[1]["deadline_horizon"] == "this_month"


# ----------------------------------------------------------------------
# Frontend wiring — sync handler слушает change И input для <select>
# ----------------------------------------------------------------------


def test_gtd_rules_js_listens_for_change_event_on_select():
    """Прежде GTD-sync слушал только ``input`` — в некоторых WebKit
    билдах ``<select>`` диспатчит только ``change``, и выбор «Срок:
    На этой неделе» не попадал в gtdRules[idx] до клика «Применить»."""
    rules_js = (
        Path(__file__).resolve().parents[2]
        / "webui" / "frontend" / "js" / "rules.js"
    ).read_text(encoding="utf-8")
    # GTD sync должен слушать оба события
    assert "el.addEventListener('change', sync)" in rules_js, (
        "GTD rules sync handler must listen for 'change' (defensive against "
        "WebKit <select> quirks)"
    )
