"""Calendar parsing and first-turn booking date regression checks."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import receptionist as app


@pytest.mark.parametrize('text,expected',[('11:30','11:30'),('08:00','08:00'),('20:30','20:30'),('8 pm','20:00'),('8','20:00')])
def test_explicit_24_hour_times_preserve_morning_hours(text,expected):
    assert app.parse_time(text)==expected


@pytest.mark.parametrize('text,expected',[
    ('half past eight','20:30'),('half past 8','20:30'),
    ('half past eight in the morning','08:30'),('half past eight in the evening','20:30'),
    ('half past twelve pm','12:30'),('08:30','08:30'),
])
def test_half_past_spoken_time(text,expected):
    assert app.parse_time(text)==expected


@pytest.mark.parametrize('text,expected',[('830','20:30'),('930','21:30'),('2030','20:30'),('0830','08:30'),('830 am','08:30'),('830 pm','20:30'),('2530',None)])
def test_compact_times_from_live_speech(text,expected):
    assert app.parse_time(text)==expected


@pytest.mark.parametrize('text, expected', [
    ('December 1, 2026', date(2026, 12, 1)),
    ('Dec 1 2026', date(2026, 12, 1)),
    ('Dec. 1st, 2026', date(2026, 12, 1)),
    ('1 December 2026', date(2026, 12, 1)),
    ('on 1st of December, 2026', date(2026, 12, 1)),
    ('December 1', date(2026, 12, 1)),
    ('January 1', date(2027, 1, 1)),
    ('February 29, 2028', date(2028, 2, 29)),
    ('February 29, 2027', None),
    ('April 31, 2026', None),
    ('December 32, 2026', None),
    ('12/01/2026', None),
    ('2026-12-01', date(2026, 12, 1)),
    ('tomorrow', date(2026, 10, 27)),
    ('Tuesday', date(2026, 10, 27)),
])
def test_booking_date_formats(text, expected):
    assert app.parse_date(text, today=date(2026, 10, 26)) == expected


@pytest.mark.parametrize('text',[
    'the twenty eighth of October, twenty twenty six',
    'October twenty-eighth, twenty twenty-six',
    'twenty eighth October two thousand twenty six',
    'the 28th of October, 2026',
])
def test_asr_keeps_natural_date_words(text):
    assert app.parse_date(text,today=date(2026,10,27))==date(2026,10,28)


def test_first_request_keeps_natural_date_and_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(app.booking_db, 'DB_PATH', tmp_path / 'bookings.sqlite3')
    monkeypatch.setattr(app, 'BOOKINGS_FILE', tmp_path / 'legacy.jsonl')
    monkeypatch.setattr(app, 'local_today', lambda r: date(2026, 10, 26))
    restaurant = app.load_restaurant(app.HERE / 'restaurants/demo_restaurant.yaml')
    values = [
        {'party_size': 2, 'date': 'December 1, 2026', 'time': '8 pm',
         'name': 'UI Test Guest', 'phone': '9900112233'},
    ]
    chat = Mock(side_effect=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(v))) for v in values])
    bot = app.Receptionist(restaurant, chat=chat)
    reply, _ = bot.turn("I'd like to book a table for 2 on December 1, 2026 at 8 pm. Name UI Test Guest, phone 9900112233.")
    assert bot.awaiting_confirmation
    assert '2026-12-01' in reply and '20:00' in reply
    assert 'Is that correct?' in reply
    assert bot.tools.booking_store().list(bot.tools.business_id) == []


def test_next_tuesday_from_tuesday_is_next_week():
    assert app.parse_date('next Tuesday',today=date(2026,10,27))==date(2026,11,3)
    assert app.parse_date('Tuesday',today=date(2026,10,27))==date(2026,10,27)


@pytest.mark.parametrize('text,expected',[('six thirty','18:30'),('six thirty in the morning','06:30'),('seven fifteen','19:15'),('six forty five','18:45')])
def test_spoken_hour_and_minutes(text,expected):
    assert app.parse_time(text)==expected


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
