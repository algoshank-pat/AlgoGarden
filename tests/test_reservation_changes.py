import json
import sqlite3
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import receptionist as app
import local_audit


def response(fields):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(fields)))


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'db.sqlite3')
    monkeypatch.setattr(app,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
    monkeypatch.setattr(app,'local_today',lambda r:date(2026,10,26))
    r=app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml')
    r['seating']={**r['seating'],'seats_per_slot':4,'existing_bookings':{}}
    tools=app.Tools(r)
    booking=tools.book_table('Alex','9900112233',2,'2026-12-01','8 pm',request_key='original')
    bot=app.Receptionist(r,chat=Mock())
    return r,tools,booking,bot


def test_cancel_needs_matched_reference_and_explicit_yes(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'reference':booking['id'][:8],'phone':'+91 99001 12233'})
    events=[]
    with local_audit.capture(events):
        reply,_=bot.turn('Cancel my reservation')
        assert 'say yes' in reply
        assert tools.booking_store().seats(tools.business_id,'2026-12-01','20:00')==2
        reply,called=bot.turn('yes')
    assert called==['change_reservation'] and 'cancelled' in reply
    rows=tools.booking_store().list(tools.business_id)
    assert len(rows)==1 and rows[0]['status']=='cancelled'
    assert tools.booking_store().seats(tools.business_id,'2026-12-01','20:00')==0
    names=[event['name'] for event in events]
    assert names.index('reservation.change_read_back')<names.index('reservation.change_confirmed')<names.index('change_reservation.completed')


def test_update_keeps_id_and_audits_before_after(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'reference':booking['id'],'phone':'9900112233', 'date':'2026-12-02','time':'9 pm','party_size':3})
    reply,_=bot.turn('Update my reservation')
    assert '2026-12-01' in reply and '2026-12-02' in reply
    assert tools.booking_store().list(tools.business_id)[0]['time']=='20:00'
    reply,_=bot.turn('yes')
    row=tools.booking_store().list(tools.business_id)[0]
    assert row['id']==booking['id'] and row['time']=='21:00' and row['version']==2
    history=tools.booking_store().history(tools.business_id)
    assert history[-1]['kind']=='updated'
    assert history[-1]['payload']['before']['time']=='20:00'
    assert history[-1]['payload']['after']['time']=='21:00'


def test_correction_requires_new_readback(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'reference':booking['id'][:8],'phone':'9900112233','time':'9 pm'}),response({'time':'9:30 pm'})]
    bot.turn('Change my reservation')
    first_key=bot.reservation_changes.request_key
    reply,_=bot.turn('Actually 9:30 pm')
    assert '21:30' in reply and 'Is that correct?' in reply
    assert bot.reservation_changes.request_key!=first_key
    assert tools.booking_store().list(tools.business_id)[0]['time']=='20:00'
    bot.turn('Yes, please.')
    assert tools.booking_store().list(tools.business_id)[0]['time']=='21:30'


def test_wrong_phone_or_reference_cannot_change(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'reference':booking['id'][:8],'phone':'9880055667'})
    reply,_=bot.turn('Cancel my reservation')
    assert 'couldn’t match' in reply and not bot.reservation_changes.pending
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'


def test_phone_lookup_uses_real_mcp_and_requires_confirmation(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({})
    reply,called=bot.turn('Cancel my reservation. My phone number is 9900112233.')
    assert called==['lookup_reservations_by_phone']
    assert '2026-12-01' in reply and 'say yes' in reply
    assert bot.reservation_changes.pending
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'
    bot.turn('yes')
    assert tools.booking_store().list(tools.business_id)[0]['status']=='cancelled'


def test_phone_lookup_multiple_requires_selection_and_preserves_other_booking(setup):
    _,tools,booking,bot=setup
    other=tools.book_table('Alex','9900112233',2,'2026-12-02','8 pm',request_key='second')
    bot.chat.side_effect=[response({'phone':'9900112233'}),response({'reservation_choice':2})]
    reply,called=bot.turn('Cancel my reservation')
    assert 'multiple reservations' in reply and not bot.reservation_changes.pending
    assert called==['lookup_reservations_by_phone']
    reply,_=bot.turn('The second one')
    assert '2026-12-02' in reply and bot.reservation_changes.pending
    bot.turn('yes')
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['status']=='active'
    assert tools.lookup_reservation(other['id'],'9900112233')['reservation']['status']=='cancelled'


def test_phone_lookup_view_then_update_without_reference(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'phone':'9900112233'}),response({'time':'9 pm'})]
    reply,called=bot.turn('Check my reservation')
    assert '2026-12-01' in reply and called==['lookup_reservations_by_phone']
    reply,_=bot.turn('Change it to 9 pm')
    assert '21:00' in reply and bot.reservation_changes.pending
    bot.turn('yes')
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['time']=='21:00'


def test_existing_date_alone_does_not_offer_noop_update(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'date':'2026-12-01'}),
                          response({'time':'half past eight','party_size':4})]
    bot.turn('I booked for December 1. Can you change it?')
    reply,_=bot.turn('My number is 9900112233')
    assert 'I found your reservation' in reply and not bot.reservation_changes.pending
    reply,_=bot.turn('Make it four people at half past eight')
    assert '4 guests' in reply and '20:30' in reply and bot.reservation_changes.pending
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['version']==1


def test_natural_change_after_phone_lookup_requires_confirmation(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'phone':'9900112233'}),response({'party_size':4,'time':'8 pm'})]
    reply,_=bot.turn('Check my reservation')
    assert 'modify or cancel' in reply
    reply,_=bot.turn('Make it four people, and could we come at half past eight instead?')
    assert '4 guests' in reply and '20:30' in reply and 'correct' in reply.lower()
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['version']==1
    bot.turn('Yes, please.')
    updated=tools.lookup_reservation(booking['id'],'9900112233')['reservation']
    assert updated['party_size']==4 and updated['time']=='20:30' and updated['version']==2
    assert len(tools.booking_store().list(tools.business_id))==1


def test_mcp_lookup_scopes_restaurant_phone_and_active_dates(setup):
    _,tools,booking,_=setup
    from reservation_mcp_client import lookup_reservations_by_phone
    store=tools.booking_store()
    assert not lookup_reservations_by_phone(store,'another','2026-10-26','9900112233')['found']
    assert not lookup_reservations_by_phone(store,tools.business_id,'2026-10-26','9880055667')['found']
    assert not lookup_reservations_by_phone(store,tools.business_id,'2026-12-02','9900112233')['found']
    store.change(tools.business_id,booking['id'],'9900112233','cancel',1,{},4,0,'cancel-test')
    assert not lookup_reservations_by_phone(store,tools.business_id,'2026-10-26','9900112233')['found']


def test_abort_change_preserves_reservation(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'reference':booking['id'][:8],'phone':'9900112233'})
    bot.turn('Cancel my reservation')
    assert bot.turn('no')[0]=='No changes were made to your reservation.'
    assert not bot.reservation_changes.active
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'


def test_full_target_preserves_original(setup):
    _,tools,booking,bot=setup
    tools.book_table('Other','9880055667',4,'2026-12-02','9 pm',request_key='other')
    bot.chat.return_value=response({'reference':booking['id'][:8],'phone':'9900112233','date':'2026-12-02','time':'9 pm'})
    reply,_=bot.turn('Update my reservation')
    assert 'fully booked' in reply and not bot.reservation_changes.pending
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['date']=='2026-12-01'


def test_capacity_rechecked_when_confirmed(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'reference':booking['id'],'phone':'9900112233','date':'2026-12-02','time':'9 pm'})
    bot.turn('Update my reservation')
    assert bot.reservation_changes.pending
    tools.book_table('Other','9880055667',4,'2026-12-02','9 pm',request_key='other')
    reply,_=bot.turn('yes')
    assert 'No changes' in reply
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['date']=='2026-12-01'


def test_cancel_retry_is_persistent_and_does_not_reactivate(setup):
    _,tools,booking,_=setup
    store=tools.booking_store()
    args=(tools.business_id,booking['id'],'9900112233','cancel',1,{},4,0,'cancel-key')
    first=store.change(*args)
    second=store.change(*args)
    assert first['changed'] and second['replayed']
    assert len(store.history(tools.business_id))==2
    replay=tools.book_table('Alex','9900112233',2,'2026-12-01','8 pm',request_key='original')
    assert not replay['booked'] and replay['status']=='cancelled'
    assert store.seats(tools.business_id,'2026-12-01','20:00')==0


def test_stale_version_cannot_cancel_new_update(setup):
    _,tools,booking,_=setup
    store=tools.booking_store()
    assert store.change(tools.business_id,booking['id'],'9900112233','update',1,{'party_size':3},4,0,'u')['changed']
    stale=store.change(tools.business_id,booking['id'],'9900112233','cancel',1,{},4,0,'c')
    assert stale['conflict']
    row=store.list(tools.business_id)[0]
    assert row['party_size']==3 and row['status']=='active'


@pytest.mark.parametrize('action',['update','cancel'])
def test_operation_and_audit_rollback_together(setup,action):
    _,tools,booking,_=setup
    store=tools.booking_store()
    kind='updated' if action=='update' else 'cancelled'
    with store.connection() as db:
        db.execute(f"CREATE TRIGGER fail_audit BEFORE INSERT ON booking_events WHEN NEW.kind='{kind}' BEGIN SELECT RAISE(ABORT,'audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.change(tools.business_id,booking['id'],'9900112233',action,1,{'party_size':3} if action=='update' else {},4,0,'op')
    row=store.list(tools.business_id)[0]
    assert row['version']==1 and row['status']=='active' and row['party_size']==2
    assert len(store.history(tools.business_id))==1


def test_unknown_reference_and_other_business_do_not_match(setup):
    _,tools,booking,_=setup
    store=tools.booking_store()
    assert not store.lookup('another',booking['id'],'9900112233')['found']
    assert not store.lookup(tools.business_id,'%%%%%%%%','9900112233')['found']
    assert not store.lookup(tools.business_id,booking['id'][:4],'9900112233')['found']


def test_invalid_reference_correction_invalidates_confirmation(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'reference':booking['id'],'phone':'9900112233'}),response({'reference':'bad-code'})]
    bot.turn('Cancel my reservation')
    reply,_=bot.turn('Actually use bad-code')
    assert 'reference' in reply
    assert not bot.reservation_changes.pending
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'


def test_missing_reference_asks_phone_without_handoff(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({})
    reply,called=bot.turn("Cancel my reservation but I don't have my reference")
    assert 'phone number' in reply and called==[] and not bot.ended
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'


def test_switching_update_to_cancel_needs_a_new_readback(setup):
    _,tools,booking,bot=setup
    bot.chat.side_effect=[response({'reference':booking['id'],'phone':'9900112233','time':'9 pm'}),response({})]
    bot.turn('Update my reservation')
    reply,_=bot.turn('Cancel my reservation instead')
    assert reply.startswith('Cancel the reservation')
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'
    bot.turn('yes')
    assert tools.booking_store().list(tools.business_id)[0]['status']=='cancelled'


@pytest.mark.parametrize('requested,expected',[('5 pm','18:00'),('10 pm','21:00')])
def test_outside_hours_update_alternative_waits_for_confirmation(setup,requested,expected):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'phone':'9900112233','time':requested})
    reply,_=bot.turn('Update my reservation to '+requested)
    assert 'outside reservation hours' in reply and 'I can offer' in reply
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['version']==1
    bot.chat=Mock(side_effect=AssertionError('Option selection needs no inference'))
    reply,_=bot.turn('The third option works for me')
    assert 'choose one' in reply.lower()
    reply,called=bot.turn('The first option works for me')
    assert expected in reply and 'Is that correct?' in reply and 'change_reservation' not in called
    original=tools.lookup_reservation(booking['id'],'9900112233')['reservation']
    assert original['time']=='20:00' and original['version']==1
    bot.turn('Yes, please')
    updated=tools.lookup_reservation(booking['id'],'9900112233')['reservation']
    assert updated['id']==booking['id'] and updated['time']==expected and updated['version']==2
    bot.chat.assert_not_called()


def test_decline_alternative_then_confirm_different_time(setup):
    _,tools,booking,bot=setup
    bot.chat.return_value=response({'phone':'9900112233','time':'5 pm'})
    bot.turn('Update my reservation to five pm')
    bot.turn('The first option works for me')
    reply,called=bot.turn('No')
    assert 'No changes were made' in reply and not called
    assert bot.reservation_changes.active and not bot.reservation_changes.pending
    assert bot.reservation_changes.changes=={}
    bot.chat=Mock(side_effect=AssertionError('Stale yes must not extract or commit'))
    reply,called=bot.turn('Yes, please')
    assert not called and 'new date' in reply
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['version']==1
    bot.chat=Mock(return_value=response({'time':'half past eight'}))
    reply,called=bot.turn('How about half past eight instead?')
    assert '20:30' in reply and 'Is that correct?' in reply
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['time']=='20:00'
    bot.turn('Yes, please')
    final=tools.lookup_reservation(booking['id'],'9900112233')['reservation']
    assert final['time']=='20:30' and final['version']==2


def test_ambiguous_yes_before_selecting_reservation_repeats_choices(setup):
    _,tools,booking,bot=setup
    other=tools.book_table('Alex','9900112233',2,'2026-12-02','8 pm',request_key='other-choice')
    bot.chat.side_effect=[response({'phone':'9900112233'}),response({})]
    reply,_=bot.turn('Update my reservation')
    assert 'multiple reservations' in reply
    reply,called=bot.turn('Yes, please')
    assert 'Which reservation' in reply and not bot.reservation_changes.pending
    assert 'change_reservation' not in called
    assert tools.lookup_reservation(booking['id'],'9900112233')['reservation']['version']==1
    assert tools.lookup_reservation(other['id'],'9900112233')['reservation']['version']==1


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
