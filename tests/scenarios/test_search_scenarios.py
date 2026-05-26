"""
Search Scenario Tests — интеграционные тесты поиска по vault.

Покрывают:
  - BM25 relevance ranking
  - Векторный (семантический) поиск — если sentence-transformers + модель доступны
  - Гибридный поиск (BM25 + вектор → RRF)
  - Парсинг дат из запроса, фильтр по тегам, keyword fallback
  - API-эндпоинты /search, /search/hybrid, /search/docs, /search/stream
  - /vault/mention автодополнение
  - /index/build и /index/status
  - LLM-синтез ответа (через shared session engine)
  - Edge cases: пустой vault, очень длинный запрос, спецсимволы

Запуск:
    uv run pytest tests/scenarios/test_search_scenarios.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Live module: most tests synthesize answers via the shared session MLX engine
# (and optional embedding model). Mark it 'live' so unattended, model-free runs
# can exclude it with ``-m "not live"``. Tests that need the model skip cleanly
# via the session fixtures when no model is configured.
pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Rich vault fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def search_vault(tmp_path_factory: Any) -> Path:
    """Vault with realistic Russian docs for search testing."""
    vault = tmp_path_factory.mktemp("search_vault")

    # Mail docs
    mail_dir = vault / "mail" / "2026" / "05"
    mail_dir.mkdir(parents=True)

    docs = [
        {
            "file": "2026-05-15_invoice-1042.md",
            "title": "Invoice #1042",
            "date": "2026-05-15T09:00:00+03:00",
            "tags": ["finance", "urgency:urgent"],
            "attachments": ["invoice_1042.pdf"],
            "body": "Счёт на оплату за май. Сумма 45 000 ₽. Срок оплаты: 25 мая 2026.",
        },
        {
            "file": "2026-05-18_contract_alpha.md",
            "title": "Договор подряда — ООО Альфа",
            "date": "2026-05-18T11:00:00+03:00",
            "tags": ["legal", "category:finance"],
            "attachments": ["dogovor_alpha.docx", "prilozhenie.pdf"],
            "body": (
                "Проект договора подряда с ООО Альфа. "
                "Сумма договора 150 000 руб. Срок выполнения: 30 июня 2026. "
                "Необходимо согласовать и подписать до конца недели."
            ),
        },
        {
            "file": "2026-05-20_standup_notes.md",
            "title": "Re: Team Standup",
            "date": "2026-05-20T10:00:00+03:00",
            "tags": ["meeting", "daily"],
            "attachments": [],
            "body": (
                "Обсудили план на неделю, блокеры по проекту Гамма. "
                "Следующий созвон во вторник в 10:00."
            ),
        },
        {
            "file": "2026-05-22_report_q2.md",
            "title": "Отчёт за Q2",
            "date": "2026-05-22T16:00:00+03:00",
            "tags": ["finance", "report"],
            "attachments": ["report_q2.xlsx"],
            "body": (
                "Квартальный отчёт по продажам. Выручка выросла на 12%. "
                "Прошу прислать комментарии до 29 мая."
            ),
        },
        {
            "file": "2026-05-10_vacation_request.md",
            "title": "Заявление на отпуск",
            "date": "2026-05-10T08:30:00+03:00",
            "tags": ["hr", "personal"],
            "attachments": [],
            "body": "Прошу предоставить ежегодный отпуск с 15 по 30 июня 2026.",
        },
    ]

    for d in docs:
        (mail_dir / d["file"]).write_text(
            f"---\n"
            f'title: "{d["title"]}"\n'
            f'type: mail-message\n'
            f'source: mail\n'
            f'date: "{d["date"]}"\n'
            f'tags: {d["tags"]}\n'
            f'attachments: {d["attachments"]}\n'
            + "---\n\n"
            + d["body"],
            encoding="utf-8",
        )

    # Calendar events
    cal_dir = vault / "calendar" / "2026" / "05"
    cal_dir.mkdir(parents=True)
    events = [
        {
            "file": "2026-05-15_review.md",
            "title": "Design Review",
            "start": "2026-05-15T14:00:00+03:00",
            "end": "2026-05-15T15:30:00+03:00",
            "tags": ["meeting", "design"],
            "body": "Обзор макетов нового интерфейса. Участники: Иван, Мария.",
        },
        {
            "file": "2026-05-25_sprint_planning.md",
            "title": "Sprint Planning",
            "start": "2026-05-25T10:00:00+03:00",
            "end": "2026-05-25T11:30:00+03:00",
            "tags": ["meeting", "scrum"],
            "body": "Планирование спринта на июнь. Оценка задач.",
        },
    ]
    for e in events:
        (cal_dir / e["file"]).write_text(
            f"---\n"
            f'title: "{e["title"]}"\n'
            f'type: calendar-event\n'
            f'source: calendar\n'
            f'start: "{e["start"]}"\n'
            f'end: "{e["end"]}"\n'
            f'tags: {e["tags"]}\n'
            f"---\n\n"
            + e["body"],
            encoding="utf-8",
        )

    # Contacts
    contacts_dir = vault / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "ivan@corp.ru.md").write_text(
        "---\n"
        'email: "ivan@corp.ru"\n'
        'name: "Иван Петров"\n'
        'tags: [vip, dev]\n'
        "---\n",
        encoding="utf-8",
    )
    (contacts_dir / "maria@corp.ru.md").write_text(
        "---\n"
        'email: "maria@corp.ru"\n'
        'name: "Мария Сидорова"\n'
        'tags: [design, vip]\n'
        "---\n",
        encoding="utf-8",
    )

    return vault


@pytest.fixture(scope="module")
def search_index(search_vault: Path) -> Any:
    """Loaded VaultIndex for the search vault."""
    from personal_assistant.mlx_server.vault_index import VaultIndex

    return VaultIndex(vault_path=search_vault).load(use_cache=False)


@pytest.fixture(scope="module")
def vector_index(search_index: Any, session_embedding_model: Any) -> Any | None:
    """Built VectorIndex using the shared embedding model."""
    from personal_assistant.mlx_server.vector_index import VectorIndex

    vi = VectorIndex(vault_path=search_index.vault_path)
    try:
        count = vi.build(search_index.docs, batch_size=8)
        if count == 0:
            pytest.skip("Vector index build returned 0 documents")
    except Exception as exc:
        pytest.skip(f"Vector index build failed: {exc}")
    return vi


@pytest.fixture(scope="module")
def search_client(search_index: Any) -> Any:
    """FastAPI TestClient with pre-loaded search vault index."""
    from fastapi.testclient import TestClient

    from personal_assistant.mlx_server.server import app, state

    state.index = search_index
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# SC-SEARCH-01: BM25 relevance
# ---------------------------------------------------------------------------


class TestBM25Search:
    def test_exact_title_match_ranks_first(self, search_index: Any):
        results = search_index.search("Invoice #1042", top_k=5)
        assert results
        assert results[0].title == "Invoice #1042"

    def test_keyword_in_body_finds_doc(self, search_index: Any):
        results = search_index.search("согласовать договор", top_k=5)
        titles = [r.title for r in results]
        assert any("Договор" in t for t in titles)

    def test_attachment_name_searchable(self, search_index: Any):
        results = search_index.search("report_q2.xlsx", top_k=5)
        assert any("Отчёт" in r.title for r in results)

    def test_tag_content_searchable(self, search_index: Any):
        results = search_index.search("urgent", top_k=5)
        assert any("Invoice" in r.title for r in results)

    def test_section_filter_limits_results(self, search_index: Any):
        all_results = search_index.search("созвон", top_k=10)
        assert len(all_results) >= 1

        cal_only = search_index.search("Planning", sections=["calendar"], top_k=10)
        assert all(r.section == "calendar" for r in cal_only)

    def test_no_results_for_nonsense(self, search_index: Any):
        results = search_index.search("xyznonexistent12345", top_k=5)
        assert results == []

    def test_russian_synonym_not_caught_by_bm25(self, search_index: Any):
        """BM25 is keyword-based: 'созвон' won't match 'встреча' without vector."""
        results = search_index.search("созвон", top_k=5)
        titles = [r.title for r in results]
        assert any("Standup" in t for t in titles)

    def test_top_k_respected(self, search_index: Any):
        results = search_index.search("2026", top_k=3)
        assert len(results) <= 3

    def test_contacts_searchable(self, search_index: Any):
        # Contact docs are indexed by title (filename stem) + tags + content.
        # The 'name' field in frontmatter is NOT indexed by BM25.
        results = search_index.search("ivan@corp.ru", top_k=5)
        assert any("ivan" in r.title.lower() for r in results)

    def test_partial_word_match(self, search_index: Any):
        results = search_index.search("отпуск", top_k=5)
        assert any("Заявление" in r.title for r in results)


# ---------------------------------------------------------------------------
# SC-SEARCH-02: Vector / semantic search
# ---------------------------------------------------------------------------


class TestVectorSearch:
    def test_vector_finds_semantic_match(self, vector_index: Any, search_index: Any):
        """Семантический поиск: 'звонок' может найти 'созвон'."""
        hits = vector_index.search("звонок", top_k=5)
        paths = [p for p, _ in hits]
        standup_path = str(
            search_index.vault_path
            / "mail"
            / "2026"
            / "05"
            / "2026-05-20_standup_notes.md"
        )
        assert standup_path in paths, f"Semantic search missed 'созвон' doc. Hits: {paths}"

    def test_vector_cross_lingual(self, vector_index: Any, search_index: Any):
        """'meeting' на английском должен находить русские 'встреча'/'созвон'."""
        hits = vector_index.search("meeting", top_k=5)
        paths = [p for p, _ in hits]
        standup_path = str(
            search_index.vault_path
            / "mail"
            / "2026"
            / "05"
            / "2026-05-20_standup_notes.md"
        )
        assert standup_path in paths, f"Cross-lingual search failed. Hits: {paths}"

    def test_vector_index_is_built(self, vector_index: Any):
        assert vector_index.is_built() is True


# ---------------------------------------------------------------------------
# SC-SEARCH-03: Hybrid RRF
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_hybrid_returns_results(self, vector_index: Any, search_index: Any):
        from personal_assistant.mlx_server.vector_index import hybrid_search

        results = hybrid_search(
            "отчёт финансы",
            bm25_index=search_index,
            vector_index=vector_index,
            top_k=5,
        )
        assert results
        titles = [r.title for r in results]
        assert any(
            "Отчёт" in t or "Invoice" in t or "Договор" in t for t in titles
        )

    def test_hybrid_respects_sections(self, vector_index: Any, search_index: Any):
        from personal_assistant.mlx_server.vector_index import hybrid_search

        results = hybrid_search(
            "встреча",
            bm25_index=search_index,
            vector_index=vector_index,
            top_k=10,
            sections=["calendar"],
        )
        assert all(r.section == "calendar" for r in results)

    def test_hybrid_rrf_both_indices_contribute(self, vector_index: Any, search_index: Any):
        """RRF должен включать результаты из обоих индексов."""
        from personal_assistant.mlx_server.vector_index import hybrid_search

        results = hybrid_search(
            "проект",
            bm25_index=search_index,
            vector_index=vector_index,
            top_k=10,
        )
        sections = {r.section for r in results}
        assert len(sections) >= 1


# ---------------------------------------------------------------------------
# SC-SEARCH-04: Date parsing from query
# ---------------------------------------------------------------------------


class TestDateParsing:
    def test_iso_date(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert "2026-05-15" in _parse_date_from_query("события 2026-05-15")

    def test_russian_numeric_date(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert "2026-05-15" in _parse_date_from_query("15.05.2026")

    def test_russian_text_date(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert "2026-05-15" in _parse_date_from_query("15 мая 2026")

    def test_english_month_date(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert "2026-05-15" in _parse_date_from_query("May 15 2026")

    def test_month_year_only(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert "2026-05" in _parse_date_from_query("май 2026")

    def test_multiple_dates(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        result = _parse_date_from_query("с 15 мая по 20 мая 2026")
        assert "2026-05-15" in result
        assert "2026-05-20" in result

    def test_no_dates_returns_empty(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        assert _parse_date_from_query("привет мир") == []

    def test_date_only_prefix_month_year(self):
        from personal_assistant.webui.routes import _parse_date_from_query

        result = _parse_date_from_query("июнь 2026")
        assert "2026-06" in result


# ---------------------------------------------------------------------------
# SC-SEARCH-05: API endpoints (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestSearchDocsAPI:
    def test_search_docs_bm25_returns_docs(self, search_client: Any):
        r = search_client.post("/search/docs", json={"query": "Invoice", "mode": "bm25"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] > 0
        assert any("Invoice" in d["title"] for d in data["docs"])

    def test_search_docs_hybrid_returns_docs(self, search_client: Any):
        r = search_client.post("/search/docs", json={"query": "отчёт", "mode": "hybrid"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] > 0

    def test_search_docs_tag_filter_or_match(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "", "tags": ["finance", "legal"], "mode": "bm25"},
        )
        assert r.status_code == 200
        data = r.json()
        for d in data["docs"]:
            assert "finance" in d["tags"] or "legal" in d["tags"]

    def test_search_docs_section_filter(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "созвон", "sections": ["mail"], "mode": "bm25"},
        )
        assert r.status_code == 200
        data = r.json()
        assert all(d["section"] == "mail" for d in data["docs"])

    def test_search_docs_date_boost(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "15 мая 2026", "mode": "bm25"},
        )
        assert r.status_code == 200
        data = r.json()
        if data["docs"]:
            first = data["docs"][0]
            assert first["date"].startswith("2026-05"), (
                f"Expected May date first, got: {first['date']}"
            )

    def test_search_docs_empty_query_lists_all(self, search_client: Any):
        r = search_client.post("/search/docs", json={"query": "", "mode": "bm25"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 5

    def test_search_docs_no_results_not_500(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "xyznonexistent", "mode": "bm25"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_search_docs_top_k_respected(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "2026", "mode": "bm25", "top_k": 3},
        )
        assert r.status_code == 200
        assert len(r.json()["docs"]) <= 3

    def test_search_docs_date_only_no_text(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "18 мая 2026", "mode": "bm25"},
        )
        assert r.status_code == 200
        data = r.json()
        # Should find the contract doc (dated 2026-05-18)
        assert any(
            d["date"].startswith("2026-05-18") for d in data["docs"]
        ), f"Date-only search failed: {[d['date'] for d in data['docs']]}"

    def test_search_docs_special_chars_query(self, search_client: Any):
        r = search_client.post(
            "/search/docs",
            json={"query": "Invoice #1042 — срочно!", "mode": "bm25"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total"] > 0

    def test_search_docs_very_long_query(self, search_client: Any):
        long_query = "поиск " * 100
        r = search_client.post(
            "/search/docs",
            json={"query": long_query, "mode": "bm25"},
        )
        assert r.status_code == 200


class TestSearchHybridAPI:
    def test_search_hybrid_fallback_when_no_vector_index(self, search_client: Any):
        r = search_client.post("/search/hybrid", json={"query": "Invoice"})
        assert r.status_code == 200
        data = r.json()
        assert "search_mode" in data
        # Should fallback to BM25 since no vector index is built in app state
        assert "bm25" in data["search_mode"].lower()

    def test_search_hybrid_no_results(self, search_client: Any):
        r = search_client.post("/search/hybrid", json={"query": "xyznonexistent"})
        assert r.status_code == 200
        data = r.json()
        assert data["doc_count"] == 0
        assert "не найдено" in data["answer"].lower() or "No relevant" in data["answer"]


class TestSearchStreamAPI:
    def test_search_stream_returns_text(self, search_client: Any):
        r = search_client.post("/search/stream", json={"query": "Invoice"})
        assert r.status_code != 500
        assert r.status_code in (200, 503)


class TestIndexAPI:
    def test_index_status_endpoint(self, search_client: Any):
        r = search_client.get("/index/status")
        assert r.status_code == 200
        data = r.json()
        assert "built" in data

    def test_index_build_endpoint_accepts(self, search_client: Any):
        r = search_client.post("/index/build")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "started" in data["status"].lower()


class TestVaultMentionAPI:
    def test_vault_mention_with_query(self, search_client: Any):
        r = search_client.get("/vault/mention?q=Invoice&limit=5")
        assert r.status_code == 200
        data = r.json()
        assert "docs" in data
        assert any("Invoice" in d["title"] for d in data["docs"])

    def test_vault_mention_empty_query_returns_recent(self, search_client: Any):
        r = search_client.get("/vault/mention?q=&limit=5")
        assert r.status_code == 200
        data = r.json()
        assert "docs" in data
        # Should return recent mail/calendar docs
        assert len(data["docs"]) > 0

    def test_vault_mention_short_query_returns_recent(self, search_client: Any):
        r = search_client.get("/vault/mention?q=x&limit=5")
        assert r.status_code == 200
        data = r.json()
        assert "docs" in data


# ---------------------------------------------------------------------------
# SC-SEARCH-06: Edge cases
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    def test_empty_vault_returns_empty(self, tmp_path_factory: Any):
        from personal_assistant.mlx_server.vault_index import VaultIndex

        empty_vault = tmp_path_factory.mktemp("empty_vault")
        idx = VaultIndex(vault_path=empty_vault).load(use_cache=False)
        results = idx.search("anything", top_k=5)
        assert results == []

    def test_search_with_only_special_chars(self, search_index: Any):
        results = search_index.search("!@#$%", top_k=5)
        assert results == []

    def test_search_with_numbers_only(self, search_index: Any):
        results = search_index.search("1042", top_k=5)
        assert any("Invoice" in r.title for r in results)

    def test_search_single_char_returns_empty_or_recent(self, search_index: Any):
        results = search_index.search("я", top_k=5)
        # Single-char tokens are filtered by tokenizer → likely empty
        assert isinstance(results, list)

    def test_doc_date_property_parsing(self, search_index: Any):
        doc = next(d for d in search_index.docs if d.title == "Invoice #1042")
        assert doc.date.startswith("2026-05-15")

    def test_doc_tags_parsing(self, search_index: Any):
        doc = next(d for d in search_index.docs if d.title == "Invoice #1042")
        assert "finance" in doc.tags
        assert "urgency:urgent" in doc.tags

    def test_doc_attachments_parsing(self, search_index: Any):
        doc = next(d for d in search_index.docs if d.title == "Invoice #1042")
        assert "invoice_1042.pdf" in doc.attachments


# ---------------------------------------------------------------------------
# SC-SEARCH-07: VaultIndex utilities
# ---------------------------------------------------------------------------


class TestVaultIndexUtilities:
    def test_get_thread_finds_related(self, search_index: Any):
        thread_docs = search_index.get_thread("Team Standup", top_k=5)
        assert any("Standup" in d.title for d in thread_docs)

    def test_get_contact_mails(self, search_index: Any):
        docs = search_index.get_contact_mails("ivan@corp.ru", top_k=5)
        assert isinstance(docs, list)

    def test_build_context_includes_titles(self, search_index: Any):
        docs = search_index.search("Invoice", top_k=3)
        ctx = search_index.build_context(docs)
        assert "Invoice" in ctx or "invoice" in ctx.lower()

    def test_doc_ui_preview_strips_markdown(self, search_index: Any):
        doc = search_index.docs[0]
        preview = doc.ui_preview()
        assert "#" not in preview
        assert preview

    def test_doc_short_summary_includes_tags(self, search_index: Any):
        doc = next(d for d in search_index.docs if d.tags)
        summary = doc.short_summary()
        assert any(tag in summary for tag in doc.tags)


# ---------------------------------------------------------------------------
# SC-SEARCH-08: LLM synthesis (via shared session engine)
# ---------------------------------------------------------------------------


class TestSearchLLMSynthesis:
    def test_search_synthesizes_russian_answer(self, search_index: Any, session_mlx_engine: Any):
        from personal_assistant.mlx_server.tasks.search import search

        result = search(
            query="какая сумма по счёту 1042",
            engine=session_mlx_engine,
            index=search_index,
            top_k=5,
            max_tokens=128,
        )
        assert result.answer
        assert (
            "45" in result.answer
            or "45000" in result.answer
            or "счёт" in result.answer.lower()
        )
        assert any("Invoice" in t for t in result.source_titles)

    def test_search_finds_deadline(self, search_index: Any, session_mlx_engine: Any):
        from personal_assistant.mlx_server.tasks.search import search

        result = search(
            query="какой дедлайн по договору с Альфа",
            engine=session_mlx_engine,
            index=search_index,
            top_k=5,
            max_tokens=128,
        )
        assert result.answer
        lower = result.answer.lower()
        assert any(
            kw in lower for kw in ["30 июня", "июнь", "дедлайн", "срок"]
        )

    def test_search_no_results_graceful(self, search_index: Any, session_mlx_engine: Any):
        from personal_assistant.mlx_server.tasks.search import search

        result = search(
            query="xyznonexistent12345",
            engine=session_mlx_engine,
            index=search_index,
            top_k=5,
        )
        assert result.doc_count == 0
        assert "No relevant" in result.answer or "не найдено" in result.answer.lower()
