import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import receptionist as app
from booking_db import BookingStore
from customer_identity import normalize_phone, local_today


def response(fields):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(fields)))


@pytest.fixture
def restaurant(tmp_path, monkeypatch):
    monkeypatch.setattr(app.booking_db, 'DB_PATH', tmp_path / 'db.sqlite3')
    monkeypatch.setattr(app, 'BOOKINGS_FILE', tmp_path / 'bookings.jsonl')
    monkeypatch.setattr(app, 'local_today', lambda r: date(2026, 10, 25))
    return app.load_restaurant(app.HERE / 'restaurants/demo_restaurant.yaml')


def make_bot(restaurant):
    bot = app.Receptionist(restaurant, chat=Mock(return_value=response({'phone': '9900112233'})))
    bot.reserving = True
    return bot


def existing(restaurant):
    tools = app.Tools(restaurant)
    result = tools.book_table('Returning test', '9900112233', 2, '2026-12-01', '8 pm', request_key='original')
    assert result['booked']
    return tools, result


def test_phone_formats_match():
    assert normalize_phone('99001 12233', 'IN') == normalize_phone('+91 99001-12233', 'IN')
    assert normalize_phone('(312) 555-0142', 'US') == '+13125550142'
    assert normalize_phone('not a phone', 'IN') is None
    assert normalize_phone('990010867','IN') is None
    assert normalize_phone('+91990010867','IN') is None


@pytest.mark.parametrize('utterance',[
    'Six one two, six six six, one-four-two-four',
    'Six one two six six six one four two four',
    '612, six six six, 1424.',
    'My phone number is six one two six six six one four two four.',
])
def test_spoken_phone_moves_booking_to_name_without_llm(restaurant,utterance):
    bot=make_bot(restaurant)
    bot._awaiting_slot='phone'
    bot.slots={'date':'tomorrow','time':'6 pm'}
    bot.chat=Mock(side_effect=AssertionError('Literal phone must not need inference'))
    reply,tools=bot.turn(utterance)
    assert bot.slots['phone']=='6126661424'
    assert bot.slots['date']=='tomorrow' and bot.slots['time']=='6 pm'
    assert 'name' in reply.lower()
    bot.chat.assert_not_called()


def test_spoken_phone_lookup_uses_same_parser(restaurant):
    bot=make_bot(restaurant)
    bot.reserving=False
    bot.reservation_changes.begin('lookup')
    bot.chat=Mock(side_effect=AssertionError('No inference for phone lookup'))
    bot.tools.lookup_reservations_by_phone=Mock(return_value={'found':False,'reservations':[]})
    bot.turn('Six one two, six six six, one-four-two-four')
    assert bot.reservation_changes.phone==normalize_phone('6126661424','IN')
    bot.tools.lookup_reservations_by_phone.assert_called_once()
    bot.chat.assert_not_called()


@pytest.mark.parametrize('utterance',['Same number.','I just gave you.','six one two','one table for two tomorrow at six'])
def test_spoken_phone_does_not_guess(utterance):
    from customer_identity import phone_from_utterance
    assert phone_from_utterance(utterance) is None


def test_new_booking_phone_reprompt_does_not_imply_existing_booking(restaurant):
    bot=make_bot(restaurant)
    bot.chat=Mock(return_value=response({'date':'tomorrow'}))
    reply,_=bot.turn('Tomorrow.')
    assert reply=='What phone number can I use for your reservation?'
    bot.chat=Mock(return_value=response({'time':'8 pm'}))
    reply,_=bot.turn("Eight o'clock at night.")
    assert reply=='What phone number can I use for your reservation?'
    assert bot.slots['date']=='tomorrow' and bot.slots['time']=='8 pm'


def test_live_draft_rejection_and_time_correction_do_not_change_existing_booking(restaurant):
    tools,original=existing(restaurant)
    bot=make_bot(restaurant)
    bot._recognized_phone=normalize_phone('9900112233')
    bot.slots={'phone':'9900112233','party_size':4,'date':'tomorrow','time':'9 pm'}
    bot._awaiting_slot='name'
    bot.chat=Mock(side_effect=AssertionError('Explicit spelling and time need no LLM'))
    reply,_=bot.turn('Shishank, S-H-A-S-H-A-N-K.')
    assert bot.slots['name']=='Shashank' and 'S-H-' not in reply and '21:00' in reply
    reply,_=bot.turn('No')
    assert 'What would you like to change' in reply and not bot.awaiting_confirmation
    assert not bot.reservation_changes.active
    reply,called=bot.turn('No, I would like to change my reservation to 830.')
    assert '20:30' in reply and 'Shashank' in reply and 'Is that correct?' in reply
    assert bot.slots['party_size']==4 and bot.slots['date']=='tomorrow'
    assert bot.awaiting_confirmation and not bot.reservation_changes.active
    assert 'change_reservation' not in called and 'book_table' not in called
    assert tools.lookup_reservation(original['id'],'9900112233')['reservation']['version']==1
    assert len(tools.booking_store().list(tools.business_id))==1
    bot.chat.assert_not_called()


def test_new_session_welcomes_existing_customer_without_new_booking(restaurant):
    tools, original = existing(restaurant)
    bot = make_bot(restaurant)
    reply, called = bot.turn('My phone is 9900112233')
    assert 'Welcome back' in reply and 'existing reservation' in reply
    assert called == ['recognize_customer']
    assert bot._existing_choice and not bot.awaiting_confirmation
    assert len(tools.booking_store().list(tools.business_id)) == 1
    # Ambiguous assent must not authorize a new booking.
    bot.turn('yes')
    assert len(tools.booking_store().list(tools.business_id)) == 1


def test_modify_looks_up_by_phone_and_preserves_existing(restaurant):
    tools, original = existing(restaurant)
    bot = make_bot(restaurant)
    bot.turn('9900112233')
    reply, called = bot.turn('Modify the existing reservation')
    assert called == ['lookup_reservations_by_phone'] and not bot.ended
    assert 'like to change' in reply
    assert tools.booking_store().list(tools.business_id)[0]['id'] == original['id']


def test_keep_does_not_write_or_transfer(restaurant):
    tools, _ = existing(restaurant)
    bot = make_bot(restaurant)
    bot.turn('9900112233')
    reply, called = bot.turn('Keep it unchanged')
    assert not called and not bot.reserving
    assert 'unchanged' in reply
    assert len(tools.booking_store().list(tools.business_id)) == 1


def test_cancel_at_existing_choice_looks_up_phone_without_deleting(restaurant):
    tools, _ = existing(restaurant)
    bot = make_bot(restaurant)
    bot.turn('9900112233')
    reply, called = bot.turn('cancel')
    assert called == ['lookup_reservations_by_phone']
    assert 'say yes' in reply
    assert len(tools.booking_store().list(tools.business_id)) == 1


def test_separate_request_still_requires_readback_and_yes(restaurant, monkeypatch):
    tools, _ = existing(restaurant)
    bot = make_bot(restaurant)
    bot.turn('9900112233')
    bot.chat.return_value = response({'party_size': 2, 'date': '2026-12-02', 'time': '8 pm', 'name': 'Returning test'})
    reply, called = bot.turn('A separate booking on December 2')
    assert bot.awaiting_confirmation
    assert 'Is that correct?' in reply
    assert len(tools.booking_store().list(tools.business_id)) == 1
    assert bot.turn('yes')[1] == ['book_table']
    assert len(tools.booking_store().list(tools.business_id)) == 2


def test_repeat_same_day_without_booking_gets_welcome(restaurant):
    first = make_bot(restaurant)
    assert 'Welcome back' not in first.turn('9900112233')[0]
    second = make_bot(restaurant)
    assert 'Welcome back' in second.turn('9900112233')[0]
    assert not second._existing_choice


def test_next_day_visit_without_reservation_not_marked_returning(tmp_path):
    store = BookingStore(tmp_path / 'db.sqlite3')
    assert not store.recognize('a','9900112233','2026-10-25','one')['returning']
    assert not store.recognize('a','9900112233','2026-10-26','two')['returning']


def test_recognition_scoped_to_restaurant_and_phone(tmp_path):
    store = BookingStore(tmp_path / 'db.sqlite3')
    store.recognize('a','9900112233','2026-10-25','one')
    assert not store.recognize('b','9900112233','2026-10-25','two')['returning']
    assert not store.recognize('a','9880055667','2026-10-25','three')['returning']


def test_duplicate_customer_booking_with_new_request_key_is_blocked(restaurant):
    tools, first = existing(restaurant)
    second = tools.book_table('Different name', '+91 99001 12233', 4, '2026-12-01', '20:00', request_key='new-session')
    assert not second['booked'] and second['duplicate']
    assert second['existing_id'] == first['id']
    assert len(tools.booking_store().list(tools.business_id)) == 1


def test_timezone_day_uses_restaurant_calendar(monkeypatch):
    import customer_identity
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(customer_identity, 'datetime', Clock)
    assert local_today({'timezone':'Asia/Kolkata'}) == date(2026,10,26)
    assert local_today({'timezone':'America/Chicago'}) == date(2026,10,25)


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
