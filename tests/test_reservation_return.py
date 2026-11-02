import copy
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client, say
from information_requests import pending_state


def draft(client, channel, complete=True):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    b=api.sessions[sid].bot
    b.reserving=True
    b.slots={'date':'2027-02-20','time':'20:30','party_size':4}
    if complete:
        b.slots.update(name='Private Guest',phone='+12025550173')
        b.awaiting_confirmation=True
    else:
        b._awaiting_slot='phone'
    b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-20','time':'20:30'})
    b.menu_agent.turn=Mock(return_value=('Pets are not permitted indoors.',['search_knowledge']))
    return sid,b


@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('resume',['Yes',"Yes, let's continue.","Let's finish the reservation."])
def test_pets_and_gluten_then_return_and_separate_confirmation(client,channel,resume):
    sid,b=draft(client,channel); before=pending_state(b)
    for question in ['Can I bring my dog?','Do you have gluten-free options?']:
        result=say(client,sid,question)
        assert result['reply'].endswith(api.RETURN_TO_RESERVATION)
        assert result['reply'].count(api.RETURN_TO_RESERVATION)==1
        assert pending_state(b)==before
        b.tools.book_table.assert_not_called()
    assert 'cross-contact' in result['reply']
    returned=say(client,sid,resume)
    assert 'Private Guest' in returned['reply'] and '20:30' in returned['reply']
    assert 'correct' in returned['reply'].lower()
    assert pending_state(b)==before
    assert not api.sessions[sid].awaiting_reservation_return
    b.tools.book_table.assert_not_called();b.chat.assert_not_called()
    say(client,sid,'Yes')
    b.tools.book_table.assert_called_once()


@pytest.mark.parametrize('channel',['chat','voice'])
def test_return_asks_missing_phone_without_losing_fields(client,channel):
    sid,b=draft(client,channel,False); before=copy.deepcopy(b.slots)
    assert say(client,sid,'Can I bring my dog?')['reply'].endswith(api.RETURN_TO_RESERVATION)
    returned=say(client,sid,'Yes')
    assert 'phone' in returned['reply'].lower() and b.slots==before
    assert not b.awaiting_confirmation
    b.tools.book_table.assert_not_called();b.chat.assert_not_called()


def test_no_invites_questions_and_another_session_cannot_consume_return(client):
    sid,b=draft(client,'voice'); before=pending_state(b)
    say(client,sid,'Can I bring my dog?')
    other=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    assert not api.sessions[other].awaiting_reservation_return
    assert say(client,sid,'No')['reply']=='Go ahead. What else would you like to ask?'
    assert pending_state(b)==before
    assert say(client,sid,'Do you have gluten-free options?')['reply'].endswith(api.RETURN_TO_RESERVATION)
    b.tools.book_table.assert_not_called()


def test_no_pending_booking_does_not_offer_return(client):
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    b=api.sessions[sid].bot
    b.menu_agent.turn=Mock(return_value=('Pets are not permitted indoors.',['search_knowledge']))
    result=say(client,sid,'Can I bring my dog?')
    assert api.RETURN_TO_RESERVATION not in result['reply']
    assert not api.sessions[sid].awaiting_reservation_return and not b.reserving


@pytest.mark.parametrize('action',['update','cancel'])
def test_pending_change_is_read_back_without_mutating_original(client,action):
    sid,b=draft(client,'voice'); b.reserving=b.awaiting_confirmation=False
    b.reservation_changes.action=action
    b.reservation_changes.current={**b.slots,'reservation_id':'private-id'}
    b.reservation_changes.changes={'time':'21:00'} if action=='update' else {}
    b.reservation_changes.pending=True
    before=pending_state(b)
    b.tools.update_reservation=Mock();b.tools.cancel_reservation=Mock()
    say(client,sid,'Can I bring my dog?'); result=say(client,sid,'Yes')
    assert action in result['reply'] and 'correct' in result['reply'].lower()
    assert pending_state(b)==before
    b.tools.update_reservation.assert_not_called();b.tools.cancel_reservation.assert_not_called()
