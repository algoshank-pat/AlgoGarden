"""Whole-utterance approval, scoped to the pending readback, with private writes."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import Mock
import copy
import pytest
import chat_api as api
from customer_identity import local_today
from conversation_controls import reservation_confirmation,handle
from conversation_followup import QUESTION
from tests.test_customer_feedback import client,say

APPROVALS=[
 'Yes, please confirm the reservation.',
 'Please confirm my booking.',
 'Yes, confirm it.',
 'Yes, please confirm.',
 'I confirm the reservation.',
 'Yes, go ahead and book it.',
 'Please go ahead and reserve the table.',
 'Okay, finalize our reservation, thank you.',
 'Sure, book that table, please.',
 'Yes',
]
UNSAFE=[
 'Yes, but make it four people.',
 'Yes, please confirm the reservation for three instead.',
 'Yes, please confirm the reservation at eight instead.',
 'Yes, please confirm only if we can sit outside.',
 'Yes, please confirm the reservation. Can I bring my dog?',
 'Yes, do not confirm the reservation.',
 "No, don't book it.",
 'Yes, please confirm the reservation, actually no.',
 'Can you confirm my reservation?',
 'Yes, please confirm the reservation?',
 'I said yes yesterday.',
 'Yes, that name is correct.',
 'Yes, confirm my phone number.',
 'Not yet, please confirm the reservation later.',
 'No more questions, thank you.',
]

@pytest.fixture(autouse=True)
def isolation(monkeypatch,tmp_path):
 monkeypatch.setenv('RECEPTIONIST_FIXTURE_DIR',str(tmp_path))
 monkeypatch.setenv('LANGSMITH_TRACING','false')
 monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')

def draft(c,channel='voice'):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];s=api.sessions[sid];b=s.bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Alex Morgan','phone':'2025550194','date':(local_today(b.restaurant)+timedelta(days=9)).isoformat(),'time':'19:00','party_size':2}
 return sid,s,b

@pytest.mark.parametrize('phrase',APPROVALS)
@pytest.mark.parametrize('channel',['chat','voice'])
def test_complete_approvals_save_once_then_close_to_survey(client,channel,phrase):
 sid,s,b=draft(client,channel);original=dict(b.slots)
 payload={'request_id':str(uuid4()),'text':phrase}
 r=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
 assert not r.get('failed') and r['structured']['tools_called']==['book_table']
 rows=b.tools.booking_store().list(b.tools.business_id)
 assert len(rows)==1 and all(rows[0][k]==v for k,v in original.items() if k!='phone')
 assert rows[0]['phone'].endswith(original['phone'])
 assert r['reply'].endswith(QUESTION)
 assert client.post(f'/api/sessions/{sid}/turns',json=payload).json()==r
 # A distinct repeat acknowledgement also cannot create another reservation.
 repeated=say(client,sid,'Yes, please book it.')
 assert 'already confirmed' in repeated['reply'] and b.tools.booking_store().list(b.tools.business_id)==rows
 closing=say(client,sid,'No more questions, thank you.')
 assert closing['survey']['status']=='offered' and not closing['ended']
 assert say(client,sid,'No thanks')['survey']['status']=='declined'
 assert b.tools.booking_store().list(b.tools.business_id)==rows
 b.chat.assert_not_called()

@pytest.mark.parametrize('phrase',UNSAFE)
def test_mixed_and_negative_utterances_are_not_confirmation(phrase):
 assert not reservation_confirmation(phrase)

@pytest.mark.parametrize('phrase',UNSAFE)
def test_unsafe_utterances_never_write_at_the_boundary(client,phrase):
 sid,s,b=draft(client);before=dict(b.slots)
 b.tools.book_table=Mock(side_effect=AssertionError('Unsafe confirmation cannot write'))
 handle(b,phrase)
 b.tools.book_table.assert_not_called()
 assert b.slots==before

@pytest.mark.parametrize('state',['name','phone','alternative'])
def test_new_approval_words_are_not_global_yes_rules(client,state):
 sid,s,b=draft(client);b.awaiting_confirmation=False
 if state=='name':
  b.slots.pop('name');b._awaiting_slot='name';b._name_candidate='Alex Morgan';b._name_readback='I heard Alex Morgan. Is that name correct?'
 elif state=='phone':b.slots.pop('phone');b._awaiting_slot='phone'
 else:b._offered_alternatives=[{'date':b.slots['date'],'time':'20:00','party_size':2}]
 b.tools.book_table=Mock(side_effect=AssertionError('No final readback has been accepted'))
 handle(b,APPROVALS[0]);b.tools.book_table.assert_not_called()
 assert not b.tools.booking_store().list(b.tools.business_id)

@pytest.mark.parametrize('interruption',['policy','paused'])
def test_approval_after_interruption_first_repeats_the_readback(client,interruption):
 sid,s,b=draft(client);original=copy.deepcopy(b.slots)
 if interruption=='policy':say(client,sid,'Can I bring my dog?')
 else:b.turn('Hold on a moment')  # Bot-level paused state; API hesitation has its separate timed contract.
 r=say(client,sid,APPROVALS[0])
 assert 'Is that correct?' in r['reply'] and not r['structured']['tools_called']
 assert b.slots==original and not b.tools.booking_store().list(b.tools.business_id)
 assert say(client,sid,APPROVALS[0])['structured']['tools_called']==['book_table']

@pytest.mark.parametrize('channel',['chat','voice'])
def test_no_more_questions_before_booking_cannot_route_to_policy(client,channel):
 sid,s,b=draft(client,channel);original=dict(b.slots)
 r=say(client,sid,'No more questions, thank you.')
 assert 'Is that correct?' in r['reply'] and r['survey']['status']=='not_offered'
 assert not r['structured']['tools_called'] and b.slots==original
 assert not b.tools.booking_store().list(b.tools.business_id)
 b.chat.assert_not_called()

@pytest.mark.parametrize('action,approval',[
 ('update','Yes, please confirm the changes.'),
 ('cancel','Yes, please confirm the cancellation.'),
])
def test_pending_change_approval_uses_its_own_action(client,action,approval):
 sid,s,b=draft(client);say(client,sid,'Yes');rows=b.tools.booking_store().list(b.tools.business_id)
 change=b.reservation_changes;change.begin(action,rows[0]['phone']);change.current=rows[0]
 change.changes={'time':'20:00'} if action=='update' else {};change.pending=True;change.request_key=str(uuid4())
 s.awaiting_more_help=False
 r=say(client,sid,approval)
 assert r['structured']['tools_called']==['change_reservation'] and r['reply'].endswith(QUESTION)
 saved=b.tools.booking_store().list(b.tools.business_id)
 assert len(saved)==1 and saved[0]['version']==rows[0]['version']+1
 assert (saved[0]['time']=='20:00') if action=='update' else (saved[0]['status']=='cancelled')
 assert not reservation_confirmation('Please book it.',action)
