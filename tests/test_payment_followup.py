import copy
from uuid import uuid4
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from tests.test_answer_followup import begin
from conversation_followup import BOOKING_OFFER


def timeout(client,monkeypatch,sid,wait):
    import conversation_waits as waits
    now=[100.];monkeypatch.setattr(waits,'clock',lambda:now[0])
    assert client.post(f'/api/sessions/{sid}/waits/{wait["token"]}/arm').status_code==200
    now[0]=115.
    response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'[wait elapsed]','input_kind':'timeout','wait_token':wait['token']})
    response.raise_for_status();return response.json()


@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('accept',['Yes','Sure','No thanks'])
def test_questions_only_payment_silence_offers_booking_without_starting_one(client,monkeypatch,channel,accept):
    sid,s,b=begin(client,channel)
    r=say(client,sid,'What do I pay you?')
    assert 'search_knowledge' in r['structured']['tools_called']
    assert r['conversation_wait']['kind']=='reservation_offer'
    r=timeout(client,monkeypatch,sid,r['conversation_wait'])
    assert r['reply']==BOOKING_OFFER and not b.reserving and not s.pending_reservation_request
    assert not r['structured']['tools_called']
    r=say(client,sid,accept)
    if accept=='No thanks':assert r['survey']['status']=='offered' and not b.reserving
    elif channel=='voice':assert 'have a reservation' in r['reply'] and s.pending_reservation_request
    else:assert 'phone' in r['reply'].lower() and b.reserving
    assert not b.tools.booking_store().list(b.tools.business_id)
    b.chat.assert_not_called()


@pytest.mark.parametrize('channel',['chat','voice'])
def test_payment_silence_resumes_readback_with_all_fields_then_one_write(client,monkeypatch,channel):
    sid,s,b=begin(client,channel,'confirmation');before=copy.deepcopy(b.slots)
    r=say(client,sid,'Can I pay by credit card?')
    r=timeout(client,monkeypatch,sid,r['conversation_wait'])
    assert 'Is that correct?' in r['reply'] and b.slots==before
    assert not b.tools.booking_store().list(b.tools.business_id)
    say(client,sid,'Yes, please confirm the reservation.')
    rows=b.tools.booking_store().list(b.tools.business_id);assert len(rows)==1
    r=say(client,sid,'What do I pay you?')
    assert r['conversation_wait'] is None
    assert say(client,sid,'No more questions')['survey']['status']=='offered'
    assert b.tools.booking_store().list(b.tools.business_id)==rows


def test_offer_cannot_consume_real_question_or_another_sessions_answer(client,monkeypatch):
    sid,s,b=begin(client,'voice')
    r=say(client,sid,'Can I pay cash?');timeout(client,monkeypatch,sid,r['conversation_wait'])
    other,other_session,_=begin(client,'voice')
    assert not other_session.awaiting_booking_offer
    r=say(client,sid,'Yes, but can I bring my dog?')
    assert 'search_knowledge' in r['structured']['tools_called'] and not s.pending_reservation_request
    assert not r['conversation_wait']  # offer once; never nag or invent consent


@pytest.mark.parametrize('channel',['chat','voice'])
def test_accepting_offer_preserves_previously_checked_availability(client,monkeypatch,channel):
    from datetime import timedelta
    from customer_identity import local_today
    from receptionist import parse_date,parse_time
    sid,s,b=begin(client,channel)
    day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    r=say(client,sid,f'Do you have a table for two on {day} at 7 pm?')
    assert 'check_table' in r['structured']['tools_called'] and b._available_offer
    r=say(client,sid,'Can I pay cash?')
    timeout(client,monkeypatch,sid,r['conversation_wait'])
    say(client,sid,'Yes')
    if channel=='voice':say(client,sid,'No')
    assert b.slots['party_size']==2 and parse_time(b.slots['time'])=='19:00'
    assert parse_date(b.slots['date'],today=local_today(b.restaurant)).isoformat()==day
    assert not b.tools.booking_store().list(b.tools.business_id)
    b.chat.assert_not_called()


def test_another_question_clears_booking_offer_consent(client,monkeypatch):
    sid,s,b=begin(client,'voice')
    r=say(client,sid,'Can I pay cash?');timeout(client,monkeypatch,sid,r['conversation_wait'])
    say(client,sid,'I have another question.')
    assert not s.awaiting_booking_offer
    r=say(client,sid,'Yes')
    assert 'ask' in r['reply'] and not s.pending_reservation_request and not b.reserving


def test_confirmed_restart_clears_offer_state(client,monkeypatch):
    sid,s,b=begin(client,'voice')
    r=say(client,sid,'Can I pay cash?');timeout(client,monkeypatch,sid,r['conversation_wait'])
    say(client,sid,'Restart this conversation.');say(client,sid,'Yes')
    assert not s.awaiting_booking_offer and not s.booking_offer_made
