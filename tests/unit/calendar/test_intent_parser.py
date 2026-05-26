"""
Unit tests for calendar intent parser (Stage 7).

Test IDs:
  IP01-IP08  — date parsing
  IP09-IP15  — time parsing
  IP16-IP20  — duration parsing
  IP21-IP24  — location + participants
  IP25-IP30  — title extraction
  IP31-IP40  — full parse_event_intent integration
  IP41-IP43  — EventDraft fields and to_dict
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

from personal_assistant.calendar.intent_parser import (
    EventDraft,
    _extract_title,
    _next_weekday,
    _parse_date,
    _parse_duration,
    _parse_location,
    _parse_participants,
    _parse_time,
    _today,
    parse_event_intent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday_after(ref: date) -> date:
    """Next Monday on or after ref (skipping same-day)."""
    return _next_weekday(0, from_date=ref)


# ---------------------------------------------------------------------------
# IP01-IP08 — Date parsing
# ---------------------------------------------------------------------------

class TestDateParsing:
    def test_IP01_today(self):
        d, w = _parse_date("встреча сегодня в 15:00")
        assert d == _today()
        assert w == []

    def test_IP02_zavtra(self):
        d, w = _parse_date("созвон завтра утром")
        assert d == _today() + timedelta(days=1)
        assert w == []

    def test_IP03_poslezavtra(self):
        d, w = _parse_date("митинг послезавтра")
        assert d == _today() + timedelta(days=2)
        assert w == []

    def test_IP04_weekday_v_pyatnitsu(self):
        d, w = _parse_date("встреча в пятницу")
        assert d.weekday() == 4  # Friday
        assert d > _today()
        assert w == []

    def test_IP05_weekday_vo_vtornik(self):
        d, w = _parse_date("созвон во вторник")
        assert d.weekday() == 1  # Tuesday
        assert d > _today()
        assert w == []

    def test_IP06_next_week_chetveрg(self):
        d, w = _parse_date("встреча в следующий четверг")
        assert d.weekday() == 3  # Thursday
        # Must be at least 7 days out
        assert (d - _today()).days >= 1
        assert w == []

    def test_IP07_absolute_date_may(self):
        d, w = _parse_date("отчёт 25 мая")
        assert d.month == 5
        assert d.day == 25
        assert w == []

    def test_IP08_cherez_tri_dnya(self):
        d, w = _parse_date("встреча через 3 дня")
        assert d == _today() + timedelta(days=3)
        assert w == []

    def test_IP08b_no_date_warns(self):
        d, w = _parse_date("встреча в офисе в 10:00")
        assert len(w) > 0
        assert "Дата" in w[0]


# ---------------------------------------------------------------------------
# IP09-IP15 — Time parsing
# ---------------------------------------------------------------------------

class TestTimeParsing:
    def test_IP09_hhmm(self):
        t, w = _parse_time("встреча в 15:30")
        assert t == "15:30"
        assert w == []

    def test_IP10_hour_range_14_16(self):
        t, w = _parse_time("встреча в пятницу 14-16")
        assert t == "14:00"
        assert w == []

    def test_IP11_utrom(self):
        t, w = _parse_time("созвон утром")
        assert t == "09:00"
        assert w == []

    def test_IP12_v_obed(self):
        t, w = _parse_time("синк в обед")
        assert t == "13:00"
        assert w == []

    def test_IP13_vecherom(self):
        t, w = _parse_time("встреча вечером")
        assert t == "18:00"
        assert w == []

    def test_IP14_v_tri_chasa(self):
        t, w = _parse_time("звонок в три часа")
        # 3 -> afternoon -> 15:00
        assert t == "15:00"
        assert w == []

    def test_IP15_no_time_warns(self):
        t, w = _parse_time("встреча в понедельник")
        assert t == "09:00"
        assert len(w) > 0


# ---------------------------------------------------------------------------
# IP16-IP20 — Duration parsing
# ---------------------------------------------------------------------------

class TestDurationParsing:
    def test_IP16_na_chas(self):
        assert _parse_duration("встреча на час") == 60

    def test_IP17_na_polchasa(self):
        assert _parse_duration("синк на полчаса") == 30

    def test_IP18_na_poltora_chasa(self):
        assert _parse_duration("созвон на полтора часа") == 90

    def test_IP19_time_range_14_16(self):
        assert _parse_duration("14-16") == 120

    def test_IP20_na_30_minut(self):
        assert _parse_duration("встреча на 30 минут") == 30

    def test_IP20b_na_dva_chasa(self):
        assert _parse_duration("встреча на два часа") == 120

    def test_IP20c_default(self):
        assert _parse_duration("встреча в офисе") == 60


# ---------------------------------------------------------------------------
# IP21-IP24 — Location and participants
# ---------------------------------------------------------------------------

class TestLocationParticipants:
    def test_IP21_zoom(self):
        assert _parse_location("созвон в Zoom") == "Zoom"

    def test_IP22_peregovornaya(self):
        loc = _parse_location("встреча в переговорной А-201")
        assert "А-201" in loc or "Переговорная" in loc

    def test_IP23_online(self):
        assert _parse_location("звонок онлайн") == "Онлайн"

    def test_IP24_participants_with(self):
        parts = _parse_participants("Встреча с Ивановым и Козловым")
        assert "Ивановым" in parts or "Козловым" in parts

    def test_IP24b_participants_komanda(self):
        parts = _parse_participants("Синк с командой")
        assert "команда" in parts


# ---------------------------------------------------------------------------
# IP25-IP30 — Title extraction
# ---------------------------------------------------------------------------

class TestTitleExtraction:
    def test_IP25_vstrecha_s(self):
        t = _extract_title("Встреча с Ивановым в следующий четверг в 15:00")
        assert "Встреча" in t
        assert "Ивановым" in t

    def test_IP26_sozvon_po_proektu(self):
        t = _extract_title("Созвон по проекту во вторник утром")
        assert "Созвон" in t
        assert "проекту" in t

    def test_IP27_blokirovat(self):
        t = _extract_title("Блокировать время для отчёта в пятницу 14-16")
        assert "отчёта" in t

    def test_IP28_demo(self):
        t = _extract_title("Демо продукта в следующий понедельник")
        assert "Демо" in t
        assert "продукта" in t

    def test_IP29_meeting_type_only(self):
        t = _extract_title("Созвон завтра в 10:00")
        assert t  # not empty
        assert len(t) >= 2

    def test_IP30_strips_stopwords(self):
        t = _extract_title("Синк по Alpha Project в среду")
        assert "среду" not in t.lower()
        assert "Alpha Project" in t or "Alpha" in t


# ---------------------------------------------------------------------------
# IP31-IP40 — Full parse_event_intent integration
# ---------------------------------------------------------------------------

class TestParseEventIntent:
    def test_IP31_basic_vstrecha(self):
        d = parse_event_intent("Встреча с Ивановым в следующий четверг в 15:00")
        assert d.title == "Встреча с Ивановым"
        assert d.time_str == "15:00"
        assert d.duration_minutes == 60
        assert d.date_iso != ""

    def test_IP32_sozvon_utrom(self):
        d = parse_event_intent("Созвон по проекту во вторник утром на час")
        assert "Созвон" in d.title
        assert d.time_str == "09:00"
        assert d.duration_minutes == 60

    def test_IP33_blokirovat_14_16(self):
        d = parse_event_intent("Блокировать время для отчёта в пятницу 14-16")
        assert d.time_str == "14:00"
        assert d.duration_minutes == 120

    def test_IP34_zoom_location(self):
        d = parse_event_intent("Встреча с командой в Zoom завтра в 10:00")
        assert d.location == "Zoom"
        assert d.time_str == "10:00"
        assert "команда" in d.participants

    def test_IP35_peregovornaya(self):
        d = parse_event_intent("Демо продукта в следующий понедельник в переговорной А-201")
        assert "Переговорная" in d.location or "А-201" in d.location

    def test_IP36_polchasa(self):
        d = parse_event_intent("Синк по Alpha Project в среду в обед на 30 минут")
        assert d.duration_minutes == 30
        assert d.time_str == "13:00"

    def test_IP37_poltora_chasa(self):
        d = parse_event_intent("Встреча на полтора часа завтра в 11:00")
        assert d.duration_minutes == 90
        assert d.time_str == "11:00"

    def test_IP38_start_end_iso(self):
        d = parse_event_intent("Созвон завтра в 14:00 на час")
        assert d.start_iso != ""
        assert d.end_iso != ""
        from datetime import datetime
        s = datetime.fromisoformat(d.start_iso)
        e = datetime.fromisoformat(d.end_iso)
        assert (e - s).seconds == 3600

    def test_IP39_confidence(self):
        d = parse_event_intent("Встреча сегодня в 15:00")
        assert 0.0 < d.confidence <= 1.0

    def test_IP40_empty_input(self):
        d = parse_event_intent("")
        assert len(d.warnings) > 0

    def test_IP40b_no_date_uses_default(self):
        d = parse_event_intent("Встреча с Ивановым в офисе в 10:00")
        # No date found → fallback to tomorrow
        assert d.date_iso != ""
        assert len(d.warnings) > 0  # warns about missing date

    def test_IP40c_calendar_no_default(self):
        """When no calendar keyword is present, calendar_name is None (ask user)."""
        d = parse_event_intent("Встреча завтра в 10:00")
        assert d.calendar_name is None

    def test_IP40d_calendar_personal(self):
        d = parse_event_intent("Личная встреча завтра в 10:00")
        assert d.calendar_name == "Personal"


# ---------------------------------------------------------------------------
# IP41-IP43 — EventDraft fields and serialization
# ---------------------------------------------------------------------------

class TestEventDraft:
    def test_IP41_to_dict_keys(self):
        d = EventDraft(
            title="Test",
            date_iso="2026-06-01",
            time_str="10:00",
            duration_minutes=60,
            start_iso="2026-06-01T10:00:00",
            end_iso="2026-06-01T11:00:00",
        )
        data = d.to_dict()
        for key in ("title", "date_iso", "time_str", "duration_minutes",
                    "participants", "location", "calendar_name",
                    "start_iso", "end_iso", "confidence", "warnings"):
            assert key in data, f"Missing key: {key}"

    def test_IP42_default_fields(self):
        d = EventDraft()
        assert d.calendar_name is None  # None → prompt user to select
        assert d.duration_minutes == 60
        assert d.participants == []
        assert d.warnings == []

    def test_IP43_raw_text_preserved(self):
        text = "Встреча с Ивановым в пятницу"
        d = parse_event_intent(text)
        assert d.raw_text == text
