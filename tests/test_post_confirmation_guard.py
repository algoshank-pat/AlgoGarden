import copy
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say


def booked(c,channel):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Private Guest','phone':'2025550173','party_size':6,'date':'2027-02-20','time':'19:30'}
 say(c,sid,'Yes')
 return sid,b


@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('approval',[
 'Yes, please book it.','Yeah, go ahead and book it.','Please reserve that table.',
 'Could you confirm my reservation?','Please confirm it.','I already confirmed it.',
 'We have already booked the table.','Yes, that is correct.','Yes, that looks right.',
 'I would like to confirm my booking.',
])
def test_repeated_confirmation_after_information_can_never_reopen_creation(client,channel,approval):
 sid,b=booked(client,channel);s=api.sessions[sid];saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 say(client,sid,"What's your address?");say(client,sid,'Where can I park?')
 say(client,sid,"Let's return to my reservation.")
 r=say(client,sid,approval)
 assert 'confirmed' in r['reply'] and r['structured']['tools_called']==['lookup_reservation']
 assert not s.pending_reservation_request and not b.reserving and not s.awaiting_reservation_return
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 assert say(client,sid,'No, thank you.')['survey']['status']=='offered'
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()


@pytest.mark.parametrize('question',["What's your address?",'Where can I park?','Do you have gluten-free food?','Can I bring my dog?'])
def test_post_booking_information_invites_only_questions_not_booking_help(client,question):
 sid,b=booked(client,'voice');saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 r=say(client,sid,question)
 assert r['reply'].endswith('Do you have any other questions?')
 assert 'help with a reservation' not in r['reply'] and 'return to your reservation' not in r['reply']
 r=say(client,sid,'Yes')
 assert 'What else would you like to ask' in r['reply'] and not r['structured']['tools_called']
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()


def test_confirmed_phase_is_session_local_and_never_overrides_fresh_cancelled_status(client):
 sid,b=booked(client,'voice');saved=b.tools.booking_store().list(b.tools.business_id)
 other=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 assert not getattr(api.sessions[other].bot,'_has_saved_reservation',False)
 with b.tools.booking_store().connection() as db:db.execute("UPDATE reservations SET status='cancelled' WHERE id=?",(saved[0]['id'],));db.commit()
 r=say(client,sid,'Yes, please book it.')
 assert 'cancelled' in r['reply'] and 'already confirmed' not in r['reply']
 assert len(b.tools.booking_store().list(b.tools.business_id))==1


@pytest.mark.parametrize('answer',['Yes','No, thank you.'])
def test_ambiguous_new_creation_after_saved_booking_requires_separate_intent(client,answer):
 sid,b=booked(client,'voice');saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 r=say(client,sid,'Can I book a table for two on February 21, 2027 at 8 pm?')
 assert r['reply'].endswith('Is this a separate booking?')
 assert not b.reserving and not api.sessions[sid].pending_reservation_request
 r=say(client,sid,answer)
 if answer=='Yes':
  assert b.reserving and b.slots['party_size']==2 and 'phone' in r['reply'].lower()
 else:assert r['survey']['status']=='offered' and not b.reserving
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()


@pytest.mark.parametrize('text',[
 'How many people did I book for?',"What's my reservation date?",'Which day is our booking?',
 'I am looking for my reservation.','My booking, please.',
])
def test_saved_booking_variations_remain_read_only_without_gate(client,text):
 sid,b=booked(client,'voice');saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 r=say(client,sid,text)
 assert r['structured']['tools_called']==['lookup_reservation'] and 'confirmed' in r['reply']
 assert 'separate booking?' not in r['reply'] and 'have a reservation with us?' not in r['reply']
 assert not b.reserving and not api.sessions[sid].pending_reservation_request
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()


def test_model_cannot_reopen_saved_booking_for_unfamiliar_approval(client):
 import json
 from tests.test_decision_engine import response
 sid,b=booked(client,'voice');saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 b.chat=Mock(return_value=response())
 r=say(client,sid,"Lovely, let's make that happen.")
 assert 'confirmed' in r['reply'] and not b.reserving and not api.sessions[sid].pending_reservation_request
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_called_once()
 context=json.loads(b.chat.call_args.kwargs['messages'][-1]['content'])['context']
 assert context['has_saved_reservation'] is True
