"""
Unit tests for LLM-assisted Semantic Classification Service (Stage 8).

Test IDs:
  LC01-LC08  — compute_rule_confidence
  LC09-LC12  — needs_llm_classification
  LC13-LC18  — LLMClassifyCache
  LC19-LC24  — llm_classify_single (with mock engine)
  LC25-LC30  — batch_llm_classify_vault (with mock engine + temp vault)
  LC31-LC34  — get_classify_stats
  LC35-LC38  — _extract_subject_preview
  LC39-LC42  — _write_llm_tag
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")

from personal_assistant.mlx_server.tasks.llm_classify_service import (
    _DEFAULT_CATEGORIES,
    LLMClassifyCache,
    _extract_subject_preview,
    _write_llm_tag,
    batch_llm_classify_vault,
    compute_rule_confidence,
    get_classify_stats,
    llm_classify_single,
    needs_llm_classification,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CLASSIFIERS_CFG = {
    "urgency": {
        "urgent": {"keywords": ["срочно", "asap", "urgent"]},
        "important": {"keywords": ["important", "важно"]},
    },
    "category": {
        "finance": {"keywords": ["invoice", "счёт", "оплата"]},
        "meeting": {"keywords": ["meeting", "встреча", "zoom"]},
    },
}


def _mock_engine(response: str = "finance") -> MagicMock:
    """Return a mock MLXEngine that returns *response* from ask()."""
    eng = MagicMock()
    eng.ask.return_value = response
    return eng


def _make_md(path: Path, subject: str, body: str, extra_fm: str = "") -> None:
    """Write a minimal .md vault file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\nid: test_{path.stem}\nsubject: {subject}\n{extra_fm}---\n"
    path.write_text(fm + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# LC01-LC08 — compute_rule_confidence
# ---------------------------------------------------------------------------

class TestComputeRuleConfidence:
    def test_LC01_all_match(self):
        text = "срочно! важная встреча, нужно оплатить счёт"
        score = compute_rule_confidence(text, CLASSIFIERS_CFG)
        assert score == 1.0  # all 2 classifiers matched

    def test_LC02_none_match(self):
        score = compute_rule_confidence("привет мир", CLASSIFIERS_CFG)
        assert score == 0.0

    def test_LC03_one_of_two(self):
        score = compute_rule_confidence("встреча в zoom", CLASSIFIERS_CFG)
        # category matched (meeting), urgency not → 1/2
        assert score == pytest.approx(0.5)

    def test_LC04_empty_text(self):
        score = compute_rule_confidence("", CLASSIFIERS_CFG)
        assert score == 0.0

    def test_LC05_empty_config(self):
        score = compute_rule_confidence("срочно встреча", {})
        assert score == 0.0

    def test_LC06_contacts_match(self):
        cfg = {
            "urgency": {
                "urgent": {
                    "keywords": [],
                    "contacts": ["boss@corp.ru"],
                }
            }
        }
        score = compute_rule_confidence("от boss@corp.ru", cfg)
        assert score == 1.0

    def test_LC07_case_insensitive(self):
        score = compute_rule_confidence("СРОЧНО INVOICE", CLASSIFIERS_CFG)
        assert score == 1.0

    def test_LC08_partial_classifiers(self):
        cfg = {
            "urgency": {"urgent": {"keywords": ["asap"]}},
            "category": {"finance": {"keywords": ["invoice"]}},
            "action": {"reply": {"keywords": ["reply"]}},
        }
        score = compute_rule_confidence("asap invoice", cfg)
        assert score == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# LC09-LC12 — needs_llm_classification
# ---------------------------------------------------------------------------

class TestNeedsLLMClassification:
    def test_LC09_below_threshold(self):
        assert needs_llm_classification("hello world", CLASSIFIERS_CFG, threshold=0.4)

    def test_LC10_above_threshold(self):
        text = "срочно встреча по счёту важно invoice"
        assert not needs_llm_classification(text, CLASSIFIERS_CFG, threshold=0.4)

    def test_LC11_exactly_threshold(self):
        # 0.5 >= 0.4 → does NOT need LLM
        text = "встреча в zoom"  # category matches, urgency not → 0.5
        assert not needs_llm_classification(text, CLASSIFIERS_CFG, threshold=0.4)

    def test_LC12_empty_config(self):
        # empty config → confidence 0 → needs LLM
        assert needs_llm_classification("anything", {}, threshold=0.1)


# ---------------------------------------------------------------------------
# LC13-LC18 — LLMClassifyCache
# ---------------------------------------------------------------------------

class TestLLMClassifyCache:
    def test_LC13_empty_on_init(self, tmp_path):
        cache = LLMClassifyCache(tmp_path / "cache.json")
        assert len(cache) == 0

    def test_LC14_put_and_get(self, tmp_path):
        cache = LLMClassifyCache(tmp_path / "cache.json")
        key = cache.make_key("Test subject", "Test preview")
        cache.put(key, "finance", 0.9)
        entry = cache.get(key)
        assert entry["category"] == "finance"
        assert entry["confidence"] == 0.9

    def test_LC15_miss_returns_none(self, tmp_path):
        cache = LLMClassifyCache(tmp_path / "cache.json")
        assert cache.get("nonexistent_key") is None

    def test_LC16_flush_persists(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        cache = LLMClassifyCache(cache_path)
        key = cache.make_key("subject", "preview")
        cache.put(key, "hr", 1.0)
        cache.flush()
        # Reload from disk
        cache2 = LLMClassifyCache(cache_path)
        assert cache2.get(key)["category"] == "hr"

    def test_LC17_make_key_deterministic(self):
        k1 = LLMClassifyCache.make_key("sub", "prev")
        k2 = LLMClassifyCache.make_key("sub", "prev")
        assert k1 == k2
        assert len(k1) == 64  # SHA-256 hex

    def test_LC18_key_differs_for_different_content(self):
        k1 = LLMClassifyCache.make_key("sub1", "prev")
        k2 = LLMClassifyCache.make_key("sub2", "prev")
        assert k1 != k2


# ---------------------------------------------------------------------------
# LC19-LC24 — llm_classify_single
# ---------------------------------------------------------------------------

class TestLLMClassifySingle:
    def _config(self, categories=None):
        return {
            "classifiers": CLASSIFIERS_CFG,
            "llm_classify": {
                "enabled": True,
                "threshold": 0.4,
                "categories": categories or _DEFAULT_CATEGORIES,
            },
        }

    def test_LC19_returns_matched_category(self, tmp_path):
        engine = _mock_engine("finance")
        config = self._config()
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = llm_classify_single("invoice question", "please pay", config, engine, cache=cache)
        assert result.category == "finance"
        assert not result.error
        assert not result.cached

    def test_LC20_cache_hit_no_engine_call(self, tmp_path):
        config = self._config()
        cache = LLMClassifyCache(tmp_path / "cache.json")
        key = LLMClassifyCache.make_key("invoice", "body")
        cache.put(key, "finance", 1.0)
        cache.flush()

        engine = _mock_engine("should_not_be_called")
        result = llm_classify_single("invoice", "body", config, engine, cache=cache)
        assert result.category == "finance"
        assert result.cached
        engine.ask.assert_not_called()

    def test_LC21_engine_error_returns_error_result(self, tmp_path):
        engine = MagicMock()
        engine.ask.side_effect = RuntimeError("Model not loaded")
        config = self._config()
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = llm_classify_single("test", "body", config, engine, cache=cache)
        assert result.error
        assert result.category == ""

    def test_LC22_unknown_response_stored_as_is(self, tmp_path):
        engine = _mock_engine("novelcategory")
        config = self._config(categories=["finance", "hr"])
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = llm_classify_single("test", "body", config, engine, cache=cache)
        assert result.category == "novelcategory"

    def test_LC23_result_written_to_cache(self, tmp_path):
        engine = _mock_engine("meeting")
        config = self._config()
        cache = LLMClassifyCache(tmp_path / "cache.json")
        llm_classify_single("встреча завтра", "zoom call", config, engine, cache=cache)
        key = LLMClassifyCache.make_key("встреча завтра", "zoom call")
        assert cache.get(key) is not None
        assert cache.get(key)["category"] == "meeting"

    def test_LC24_no_cache_still_works(self, tmp_path):
        engine = _mock_engine("hr")
        config = self._config()
        result = llm_classify_single("новый сотрудник", "найм", config, engine, cache=None)
        assert result.category == "hr"
        assert not result.error


# ---------------------------------------------------------------------------
# LC25-LC30 — batch_llm_classify_vault
# ---------------------------------------------------------------------------

class TestBatchLLMClassifyVault:
    def _config(self):
        return {
            "classifiers": CLASSIFIERS_CFG,
            "llm_classify": {
                "enabled": True,
                "threshold": 0.4,
                "batch_size": 2,
                "categories": _DEFAULT_CATEGORIES,
            },
        }

    def _vault(self, tmp_path, n_mail=3, n_calendar=1) -> Path:
        vault = tmp_path / "vault"
        for i in range(n_mail):
            _make_md(
                vault / "mail" / f"msg_{i:03d}.md",
                subject=f"Test mail {i}",
                body=f"Neutral content {i}, no keywords here.",
            )
        for i in range(n_calendar):
            _make_md(
                vault / "calendar" / f"event_{i:03d}.md",
                subject=f"Meeting {i}",
                body="встреча zoom",
            )
        return vault

    def test_LC25_dry_run_no_llm_calls(self, tmp_path):
        engine = _mock_engine("finance")
        vault = self._vault(tmp_path)
        # Use isolated cache so global real-vault entries don't interfere
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = batch_llm_classify_vault(
            vault, engine, self._config(), dry_run=True, cache=cache
        )
        engine.ask.assert_not_called()
        # dry_run: no new LLM calls; cached_hits may still appear but no new ones
        assert result.errors == 0

    def test_LC26_classifies_low_confidence_docs(self, tmp_path):
        engine = _mock_engine("info")
        vault = self._vault(tmp_path)
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = batch_llm_classify_vault(vault, engine, self._config(), cache=cache)
        # "Neutral content" docs have 0 confidence → should be classified
        assert result.classified > 0

    def test_LC27_skips_calendar_with_keywords(self, tmp_path):
        """Calendar doc with 'встреча zoom' already has high confidence, skip LLM."""
        engine = _mock_engine("meeting")
        vault = self._vault(tmp_path, n_mail=0, n_calendar=1)
        cache = LLMClassifyCache(tmp_path / "cache.json")
        # calendar event has "встреча zoom" → category matches → conf=0.5 >= 0.4
        batch_llm_classify_vault(vault, engine, self._config(), cache=cache)
        # Should not call LLM for this doc
        engine.ask.assert_not_called()

    def test_LC28_batch_result_fields(self, tmp_path):
        engine = _mock_engine("finance")
        vault = self._vault(tmp_path)
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = batch_llm_classify_vault(vault, engine, self._config(), cache=cache)
        assert hasattr(result, "total")
        assert hasattr(result, "classified")
        assert hasattr(result, "cached_hits")
        assert hasattr(result, "errors")
        assert hasattr(result, "duration_seconds")
        assert result.duration_seconds >= 0

    def test_LC29_cache_prevents_reprocessing(self, tmp_path):
        engine = _mock_engine("finance")
        vault = self._vault(tmp_path, n_mail=1, n_calendar=0)
        cache = LLMClassifyCache(tmp_path / "cache.json")
        # First pass
        batch_llm_classify_vault(vault, engine, self._config(), cache=cache)
        first_call_count = engine.ask.call_count
        # Second pass — same docs, should use cache
        batch_llm_classify_vault(vault, engine, self._config(), cache=cache)
        # No new LLM calls
        assert engine.ask.call_count == first_call_count

    def test_LC30_to_dict_serialisable(self, tmp_path):
        engine = _mock_engine("finance")
        vault = self._vault(tmp_path)
        cache = LLMClassifyCache(tmp_path / "cache.json")
        result = batch_llm_classify_vault(vault, engine, self._config(), dry_run=True, cache=cache)
        data = result.to_dict()
        assert isinstance(data, dict)
        json.dumps(data)  # must be JSON-serialisable


# ---------------------------------------------------------------------------
# LC31-LC34 — get_classify_stats
# ---------------------------------------------------------------------------

class TestGetClassifyStats:
    def test_LC31_empty_vault(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        stats = get_classify_stats(vault)
        assert stats["total_docs"] == 0
        assert stats["ai_classified"] == 0
        assert stats["category_distribution"] == {}

    def test_LC32_counts_ai_classified(self, tmp_path):
        vault = tmp_path / "vault"
        _make_md(
            vault / "mail" / "ai_doc.md",
            subject="Test",
            body="body",
            extra_fm="tags:\n  - ai_classified\n  - llm_category:finance\n",
        )
        stats = get_classify_stats(vault)
        assert stats["ai_classified"] == 1
        assert stats["category_distribution"].get("finance") == 1

    def test_LC33_cache_stats_included(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cache = LLMClassifyCache(tmp_path / "cache.json")
        cache.put("k1", "finance", 1.0)
        cache.flush()
        stats = get_classify_stats(vault, cache=cache)
        assert "cache" in stats
        assert stats["cache"]["total_entries"] == 1

    def test_LC34_mixed_docs(self, tmp_path):
        vault = tmp_path / "vault"
        _make_md(vault / "mail" / "a.md", "subj", "body")
        _make_md(vault / "mail" / "b.md", "subj", "body", "tags:\n  - ai_classified\n")
        stats = get_classify_stats(vault)
        assert stats["total_docs"] == 2
        assert stats["ai_classified"] == 1


# ---------------------------------------------------------------------------
# LC35-LC38 — _extract_subject_preview
# ---------------------------------------------------------------------------

class TestExtractSubjectPreview:
    def test_LC35_frontmatter_subject(self):
        raw = "---\nid: x\nsubject: Счёт за март\n---\nТело письма"
        sub, preview = _extract_subject_preview(raw)
        assert sub == "Счёт за март"
        assert "Тело" in preview

    def test_LC36_fallback_to_first_line(self):
        raw = "# Встреча завтра\nТело письма здесь"
        sub, preview = _extract_subject_preview(raw)
        assert "Встреча завтра" in sub

    def test_LC37_title_in_frontmatter(self):
        raw = "---\ntitle: Проект Q3\n---\nТело"
        sub, preview = _extract_subject_preview(raw)
        assert sub == "Проект Q3"

    def test_LC38_preview_truncated(self):
        long_body = "A" * 1000
        raw = f"---\nsubject: Test\n---\n{long_body}"
        _, preview = _extract_subject_preview(raw)
        assert len(preview) <= 600


# ---------------------------------------------------------------------------
# LC39-LC42 — _write_llm_tag
# ---------------------------------------------------------------------------

class TestWriteLLMTag:
    def test_LC39_adds_tag_to_frontmatter(self, tmp_path):
        md = tmp_path / "test.md"
        _make_md(md, "Subject", "Body content")
        _write_llm_tag(md, "finance")
        raw = md.read_text(encoding="utf-8")
        assert "llm_category:finance" in raw
        assert "ai_classified" in raw

    def test_LC40_replaces_stale_llm_tag(self, tmp_path):
        md = tmp_path / "test.md"
        _make_md(md, "Subject", "Body", "tags:\n  - llm_category:hr\n")
        _write_llm_tag(md, "finance")
        raw = md.read_text(encoding="utf-8")
        assert "llm_category:finance" in raw
        assert "llm_category:hr" not in raw

    def test_LC41_no_frontmatter_safe(self, tmp_path):
        md = tmp_path / "plain.md"
        md.write_text("No frontmatter here", encoding="utf-8")
        _write_llm_tag(md, "finance")  # should not raise
        # File unchanged
        assert "---" not in md.read_text(encoding="utf-8")

    def test_LC42_existing_tags_preserved(self, tmp_path):
        md = tmp_path / "test.md"
        _make_md(md, "Subject", "Body", "tags:\n  - urgency:urgent\n")
        _write_llm_tag(md, "hr")
        raw = md.read_text(encoding="utf-8")
        assert "urgency:urgent" in raw
        assert "llm_category:hr" in raw
