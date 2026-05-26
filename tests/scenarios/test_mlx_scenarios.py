"""
MLX Scenario Tests — проверяют реальный mlx-lm инференс на Apple Silicon.

Пропускаются автоматически если:
  - mlx-lm не установлен
  - PA_MLX_MODEL_PATH не задан или указывает на несуществующую директорию
  - Не macOS / не Apple Silicon

Запуск:
    uv run pytest tests/scenarios/test_mlx_scenarios.py -v

Осторожно: загрузка модели занимает 5–30 с, генерация — 1–10 с на токен.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _skip_reason() -> str | None:
    """Return skip reason or None if MLX is available for real inference."""
    if sys.platform != "darwin":
        return "requires macOS"
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return "mlx-lm not installed"

    # Read from the snapshot taken by the root conftest at import time —
    # ``settings.mlx_model_path`` is blanked there to keep unit/e2e hermetic,
    # but the original env var is preserved in ``ORIG_PA_MLX_MODEL_PATH`` so
    # live MLX runs invoked as ``PA_MLX_MODEL_PATH=… pytest …`` still resolve.
    from tests.conftest import ORIG_PA_MLX_MODEL_PATH

    model_path = ORIG_PA_MLX_MODEL_PATH.strip()
    if not model_path:
        return "PA_MLX_MODEL_PATH not set"
    if not Path(model_path).exists():
        return f"model path does not exist: {model_path}"
    return None


# Live module: requires real local MLX inference (model weights + macOS arm64).
# Mark it 'live' so unattended runs can exclude it with ``-m "not live"``. The
# skip probe is cheap (import + path check, no osascript), so it is safe to
# evaluate at import time.
_SKIP_REASON = _skip_reason()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        _SKIP_REASON is not None,
        reason=f"MLX real-inference skipped: {_SKIP_REASON}",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(session_mlx_engine: Any) -> Any:
    """Alias for the shared session-scoped MLX engine."""
    return session_mlx_engine


@pytest.fixture(scope="module")
def vault_index(tmp_path_factory: Any) -> Any:
    """Build a VaultIndex with realistic Russian emails."""
    from personal_assistant.mlx_server.vault_index import VaultIndex

    vault = tmp_path_factory.mktemp("scenario_vault")
    mail_dir = vault / "mail" / "2026" / "05"
    mail_dir.mkdir(parents=True)

    # Email 1: request with deadline
    (mail_dir / "2026-05-20_report.md").write_text(
        "---\n"
        'title: "Отчёт за май"\n'
        'type: mail-message\n'
        'source: mail\n'
        'sender_name: "Иван Петров"\n'
        'from: "ivan@corp.ru"\n'
        'date: "2026-05-20T14:30:00+03:00"\n'
        "tags: []\n"
        "---\n\n"
        "Коллеги, прошу прислать отчёт по проекту до 25 мая. "
        "Сумма по договору: 150 000 руб. Необходимо согласовать сроки.",
        encoding="utf-8",
    )

    # Email 2: FYI
    (mail_dir / "2026-05-21_fyi.md").write_text(
        "---\n"
        'title: "Обновление инфраструктуры"\n'
        'type: mail-message\n'
        'source: mail\n'
        'sender_name: "DevOps"\n'
        'from: "devops@corp.ru"\n'
        'date: "2026-05-21T09:00:00+03:00"\n'
        "tags: []\n"
        "---\n\n"
        "К сведению: сервера обновлены, downtime не наблюдается.",
        encoding="utf-8",
    )

    # Calendar event
    cal_dir = vault / "calendar" / "2026" / "05"
    cal_dir.mkdir(parents=True)
    (cal_dir / "2026-05-22_standup.md").write_text(
        "---\n"
        'title: "Daily Standup"\n'
        'type: calendar-event\n'
        'source: calendar\n'
        'start: "2026-05-22T10:00:00+03:00"\n'
        'end: "2026-05-22T10:30:00+03:00"\n'
        "tags: []\n"
        "---\n\n"
        "Обсудили план на неделю и блокеры.",
        encoding="utf-8",
    )

    idx = VaultIndex(vault_path=vault).load(use_cache=False)
    return idx


# ---------------------------------------------------------------------------
# SC-MLX-01: Engine loads and basic generation works
# ---------------------------------------------------------------------------


class TestMLXEngineRealInference:
    def test_model_loads_and_is_loaded_true(self, engine: Any):
        assert engine.is_loaded is True
        assert engine.model_name != "not configured"

    def test_generate_returns_non_empty_string(self, engine: Any):
        result = engine.generate("2 + 2 =", max_tokens=20, temperature=0.1)
        assert isinstance(result, str)
        assert len(result.strip()) > 0

    def test_chat_returns_russian_response(self, engine: Any):
        result = engine.chat(
            [{"role": "user", "content": "Сколько будет 5 умножить на 6?"}],
            max_tokens=40,
            temperature=0.1,
        )
        assert isinstance(result, str)
        assert len(result.strip()) > 0
        # Model should mention 30 somewhere (loose check)
        assert "30" in result or "тридцать" in result.lower()

    def test_ask_with_context_truncates_and_answers(self, engine: Any):
        context = "Москва — столица России.\n" * 1000
        result = engine.ask(
            question="Какой город столица России?",
            context=context,
            max_tokens=30,
            temperature=0.1,
        )
        assert isinstance(result, str)
        assert "Москва" in result

    def test_stream_yields_multiple_chunks(self, engine: Any):
        chunks = list(
            engine.stream(
                [{"role": "user", "content": "Привет! Как дела?"}],
                max_tokens=30,
                temperature=0.3,
            )
        )
        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)
        full = "".join(chunks)
        assert len(full.strip()) > 0


# ---------------------------------------------------------------------------
# SC-MLX-02: Draft reply generation
# ---------------------------------------------------------------------------


class TestMLXDraftReply:
    def test_draft_reply_produces_russian_text(self, engine: Any, vault_index: Any):
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply
        from personal_assistant.mlx_server.vault_index import VaultDoc

        docs = [d for d in vault_index.docs if d.section == "mail"]
        assert docs, "No mail docs in vault index"
        doc: VaultDoc = docs[0]

        result = draft_reply(
            doc=doc,
            engine=engine,
            index=vault_index,
            instructions="ответь кратко, подтверди получение",
            tone="professional",
            max_tokens=256,
        )
        assert result.draft
        assert isinstance(result.draft, str)
        assert len(result.draft) > 20
        # Should be in Russian (loose heuristic: common Cyrillic words)
        lower = result.draft.lower()
        assert any(
            kw in lower
            for kw in [
                "спасибо",
                "получил",
                "подтверждаю",
                "здравствуйте",
                "уважаемый",
                "ок",
                "отчёт",
                "договор",
            ]
        ), f"Draft does not look Russian: {result.draft[:200]!r}"

    def test_draft_reply_subject_is_re(self, engine: Any, vault_index: Any):
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply

        docs = [d for d in vault_index.docs if d.section == "mail"]
        assert docs
        result = draft_reply(docs[0], engine, vault_index, max_tokens=64)
        assert result.subject.startswith("Re: ")


# ---------------------------------------------------------------------------
# SC-MLX-03: Summarization
# ---------------------------------------------------------------------------


class TestMLXSummarize:
    def test_summarize_thread_returns_russian_bullets(self, engine: Any, vault_index: Any):
        from personal_assistant.mlx_server.tasks.summarize import summarize_docs

        docs = [d for d in vault_index.docs if d.section == "mail"]
        assert len(docs) >= 2

        result = summarize_docs(
            docs=docs[:2],
            engine=engine,
            index=vault_index,
            topic="переписка",
            max_tokens=512,
        )
        assert result.summary
        assert isinstance(result.summary, str)
        assert len(result.summary) > 30
        # Should contain list markers or key phrases
        lower = result.summary.lower()
        assert any(
            ch in lower for ch in ["1.", "2.", "3.", "•", "-", "ключевые", "тезисы", "задачи"]
        ), f"Summary lacks structure: {result.summary[:300]!r}"


# ---------------------------------------------------------------------------
# SC-MLX-04: Structured extraction (prompt-only, no broken generate_sync)
# ---------------------------------------------------------------------------


class TestMLXExtraction:
    def test_mlx_extracts_json_from_email_body(self, engine: Any):
        """Проверяем что модель может выдать валидный JSON по extraction prompt."""
        from personal_assistant.mlx_server.tasks.extract import (
            _build_prompt,
            _parse_extraction_json,
        )

        body = (
            "Уважаемый коллега, прошу подготовить презентацию до 30 мая 2026. "
            "Бюджет проекта: 250 000 руб. Срочно!"
        )
        prompt = _build_prompt(body)
        raw = engine.generate(prompt, max_tokens=400, temperature=0.1)

        assert raw
        # Try to parse whatever the model returned
        try:
            result = _parse_extraction_json(raw)
        except Exception as exc:
            pytest.fail(f"Model output could not be parsed as extraction JSON: {exc}\nRaw: {raw[:500]}")

        assert result.intent in {"request", "deadline", "unknown"}
        assert result.tone in {"formal", "urgent", "neutral"}
        # Should detect the action item
        assert any("презентац" in item.text.lower() for item in result.action_items), (
            f"Expected 'презентация' in action_items, got: {[i.text for i in result.action_items]}"
        )

    def test_mlx_extracts_entities_with_amounts(self, engine: Any):
        from personal_assistant.mlx_server.tasks.extract import (
            _build_prompt,
            _parse_extraction_json,
        )

        body = "Счёт на оплату: 45 000 ₽. Реквизиты ООО Альфа. Срок: 2026-06-15."
        prompt = _build_prompt(body)
        raw = engine.generate(prompt, max_tokens=400, temperature=0.1)

        try:
            result = _parse_extraction_json(raw)
        except Exception as exc:
            pytest.fail(f"Parse failed: {exc}\nRaw: {raw[:500]}")

        amounts = [a.lower() for a in result.entities.amounts]
        orgs = [o.lower() for o in result.entities.organizations]
        assert any("45" in a or "45000" in a for a in amounts), (
            f"Expected amount '45 000' in entities, got: {amounts}"
        )
        assert any("альфа" in o for o in orgs), f"Expected 'ООО Альфа' in orgs, got: {orgs}"


# ---------------------------------------------------------------------------
# SC-MLX-05: LLM-assisted classification
# ---------------------------------------------------------------------------


class TestMLXLLMClassify:
    def test_llm_classify_single_returns_known_category(self, engine: Any):
        from personal_assistant.mlx_server.tasks.llm_classify_service import llm_classify_single

        result = llm_classify_single(
            subject="Счёт на оплату №1042",
            preview="Прошу оплатить счёт до конца недели. Сумма 150 000 руб.",
            config={
                "llm_classify": {
                    "enabled": True,
                    "categories": ["finance", "meeting", "legal", "hr", "it", "info"],
                    "prompt": (
                        "Классифицируй письмо. Ответь ТОЛЬКО одним словом из списка:\n"
                        "{categories}\n\nТема: {subject}\nПисьмо: {preview}\n\nКатегория:"
                    ),
                }
            },
            engine=engine,
        )
        assert result.category
        assert result.category.lower() in {"finance", "meeting", "legal", "hr", "it", "info"}
        assert not result.error


# ---------------------------------------------------------------------------
# SC-MLX-06: Calendar intent parser — MLX refinement path
# ---------------------------------------------------------------------------


class TestMLXIntentParser:
    def test_mlx_refinement_parses_complex_phrase(self, engine: Any):
        from personal_assistant.calendar.intent_parser import parse_event_intent

        # Ambiguous phrase where rule-based may struggle
        text = "Встреча с заказчиком через две недели в 14:30, переговорная Б-301"
        draft = parse_event_intent(text, mlx_engine=engine)

        assert draft.title
        assert draft.date_iso  # should have a date
        assert draft.time_str  # should have time
        assert "заказчик" in draft.title.lower() or "встреча" in draft.title.lower()

    def test_mlx_refinement_handles_duration(self, engine: Any):
        from personal_assistant.calendar.intent_parser import parse_event_intent

        text = "Созвон с командой на полтора часа завтра в 11:00"
        draft = parse_event_intent(text, mlx_engine=engine)

        assert draft.duration_minutes == 90 or draft.duration_minutes == 60
        assert draft.time_str == "11:00"


# ---------------------------------------------------------------------------
# SC-MLX-07: Tool calling in chat (date_calc)
# ---------------------------------------------------------------------------


class TestMLXToolCalling:
    def test_model_mentions_tool_call_or_date(self, engine: Any):
        """Проверяем что модель может сгенерировать вызов date_calc или дать дату."""
        system = (
            "Ты — полезный ассистент. У тебя есть инструмент date_calc, "
            "который вычисляет дату по относительному выражению. "
            "Если пользователь спрашивает про дату, используй инструмент."
        )
        result = engine.chat(
            [{"role": "user", "content": "Какая дата будет через 3 дня?"}],
            system=system,
            max_tokens=80,
            temperature=0.2,
        )
        lower = result.lower()
        # Model should either call the tool or mention a date
        assert any(
            kw in lower
            for kw in [
                "date_calc",
                "<tool_call>",
                "2026",
                "28",
                "29",
                "30",
                "через 3 дня",
            ]
        ), f"Model did not handle date query well: {result[:300]!r}"
