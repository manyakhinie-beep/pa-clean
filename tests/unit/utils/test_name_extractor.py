"""Unit tests for name extraction/normalisation (pure)."""

from __future__ import annotations

from personal_assistant.utils.name_extractor import (
    best_name,
    enrich_contact_name,
    extract_name,
    name_quality,
    normalize_name,
)


def test_extract_rfc822_display_name():
    assert extract_name("Иванов Иван <ivan@corp.ru>") == "Иванов Иван"


def test_extract_bare_email_is_none():
    assert extract_name("ivan@corp.ru") is None


def test_extract_uppercase_is_normalized():
    assert extract_name("ИВАНОВ ИВАН") == "Иванов Иван"


def test_extract_comma_separated():
    assert extract_name("Иванов, Иван") == "Иванов Иван"


def test_extract_noise_is_none():
    assert extract_name("noreply <noreply@x.com>") is None
    assert extract_name("postmaster") is None


def test_extract_empty_is_none():
    assert extract_name("") is None


def test_normalize_preserves_uppercase_initials():
    assert normalize_name("иванов И.И.") == "Иванов И.И."


def test_normalize_latin_title_case():
    assert normalize_name("ivan ivanov") == "Ivan Ivanov"


def test_name_quality_levels():
    assert name_quality("Иванов Иван Иванович") == 3
    assert name_quality("Иванов Иван") == 2
    assert name_quality("Иванов И.И.") == 2
    assert name_quality("Иванов") == 1
    assert name_quality("И.И.") == 1
    assert name_quality("") == 0
    assert name_quality("ivan@corp.ru") == 0


def test_best_name_higher_quality_wins():
    cands = [("ivan ivanov", "mail"), ("Иванов Иван Иванович", "contacts")]
    assert best_name(cands) == "Иванов Иван Иванович"


def test_best_name_source_priority_breaks_tie():
    cands = [("Ivan Ivanov", "mail"), ("Пётр Петров", "calendar")]
    assert best_name(cands) == "Пётр Петров"


def test_best_name_all_noise_is_none():
    assert best_name([("noreply@x.com", "mail")]) is None


def test_enrich_updates_when_better():
    chosen, updated = enrich_contact_name("Иванов", "mail", "Иванов Иван Иванович", "calendar")
    assert updated is True
    assert chosen == "Иванов Иван Иванович"


def test_enrich_keeps_existing_when_worse():
    chosen, updated = enrich_contact_name("Иванов Иван Иванович", "calendar", "Иванов", "mail")
    assert updated is False
    assert chosen == "Иванов Иван Иванович"
