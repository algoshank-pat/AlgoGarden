from datetime import date
import receptionist as app


def tools(tmp_path,monkeypatch):
    monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'bookings.sqlite3')
    monkeypatch.setattr(app,'local_today',lambda r:date(2026,10,26))
    r=app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml')
    r['seating']={**r['seating'],'seats_per_slot':4,'existing_bookings':{}}
    return app.Tools(r)


def test_same_day_later_then_next_two_days(tmp_path,monkeypatch):
    t=tools(tmp_path,monkeypatch)
    # 90-minute stay blocks 19:00 and 21:00 alternatives for a 20:00 booking.
    t.book_table('Full','9880055667',4,'2026-12-01','20:00',request_key='full')
    result=t.check_table(2,'2026-12-01','20:00')
    assert not result['available']
    assert result['alternatives']==[
        {'date':'2026-12-02','time':'20:00','party_size':2},
        {'date':'2026-12-03','time':'20:00','party_size':2}]
    assert len(t.booking_store().list(t.business_id))==1


def test_same_day_fallback_and_no_closed_or_full_suggestions(tmp_path,monkeypatch):
    t=tools(tmp_path,monkeypatch)
    # At 21:00, one hour later cannot fit a 90-minute stay before 23:00.
    result=t.reservation_alternatives(date(2026,12,1),'21:00',2)
    assert result[0]['time']=='20:00'
    t.r['hours']['weekly_off']='wednesday'
    t.book_table('Full','9880055667',4,'2026-12-03','21:00',request_key='full-next')
    assert t.reservation_alternatives(date(2026,12,1),'21:00',2)==[result[0]]


def test_update_suggestions_exclude_original_booking_and_keep_it_unchanged(tmp_path,monkeypatch):
    t=tools(tmp_path,monkeypatch)
    original=t.book_table('Alex','9900112233',4,'2026-12-01','20:00',request_key='original')
    t.book_table('Other','9880055667',4,'2026-12-02','20:00',request_key='other')
    prepared=t.prepare_reservation_change(original,{'date':'2026-12-02'})
    assert not prepared['valid'] and prepared['alternatives']
    assert t.lookup_reservation(original['id'],'9900112233')['reservation']['date']=='2026-12-01'
    own=t.reservation_alternatives(date(2026,12,1),'20:00',4,original)
    assert own[0]['date']=='2026-12-01' and own[0]['time']=='21:00'
    assert 'Would any of these work for you?' in app.alternative_question(prepared['alternatives'])


def test_spoken_first_option_needs_readback_and_confirmation(tmp_path,monkeypatch):
    from unittest.mock import Mock
    t=tools(tmp_path,monkeypatch)
    bot=app.Receptionist(t.r,chat=Mock(side_effect=AssertionError('Option selection should not require LLM inference')))
    bot.reserving=True
    bot.slots={'name':'Option Test','phone':'9900112233','party_size':2,'date':'2026-12-01'}
    bot._recognized_phone=app.normalize_phone('9900112233','IN')
    bot._offered_alternatives=[{'date':'2026-12-02','time':'20:00','party_size':2}]
    reply,_=bot.turn('The first option works for me.')
    assert '2026-12-02' in reply and '20:00' in reply and 'correct' in reply.lower()
    assert t.booking_store().list(t.business_id)==[]
    reply,_=bot.turn('Yes, please.')
    assert 'booked' in reply
    row=t.booking_store().list(t.business_id)[0]
    assert row['date']=='2026-12-02' and row['time']=='20:00'


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
