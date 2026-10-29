"""Policy routing and no-write/confirmation contracts, using private fixtures."""
import copy,json
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from policy_requests import policy_question,policy_topics,select_quotes
from information_requests import pending_state

QUESTIONS=[
 'If traffic delays us, when would you give our table away?',
 'How much notice should we give before cancelling?',
 'Are small dogs welcome inside the dining room?',
 'May our party supply its own champagne?',
 'We bought a cake at a bakery; may we bring it along?',
 'Do you have booster or high chairs for toddlers?',
 'Does your till accept UPI?',
 'Could you promise us exclusive use of a separate dining room?',
 'Can each of us settle our own part of the check?',
 'Is wheelchair seating guaranteed?',
 'Does private dining require manager approval?',
 'What is your dress code?',
]
@pytest.mark.parametrize('text',QUESTIONS)
def test_domain_questions_are_policy(text):assert policy_question(text)

@pytest.mark.parametrize('text',[
 'Book a table for two next Saturday at eight.',
 'Can you cancel my reservation?',
 'Please update my booking.',
 'What wines do you serve?',
 'What cakes are on the menu?',
 'Can I order a birthday cake?',
 'Please connect me to a manager about a private room.',
 'What is American foreign policy?',
 'The name is Bill.',
 'May I book a table for four?',
])
def test_operational_menu_and_out_of_scope_contrasts(text):assert not policy_question(text)

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('text',QUESTIONS)
def test_policy_escapes_phone_collection_without_draft_mutation(client,channel,text):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];s=api.sessions[sid];b=s.bot
 b.reserving=True;b.slots={'date':'2026-11-18','time':'19:00','party_size':2};b._awaiting_slot='phone'
 b.menu_agent.turn=Mock(return_value=('A complete retrieved policy sentence.',['search_knowledge']))
 before=pending_state(b)
 result=say(client,sid,text)
 assert result['routing']['intent']=='policy' and 'phone' not in result['reply'].lower()
 assert pending_state(b)==before and result['structured']['tools_called']==['search_knowledge']
 assert b.menu_agent.turn.call_args.kwargs['policy'] and b.menu_agent.turn.call_args.kwargs['direct']
 b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_gate_and_readback_resume_need_explicit_confirmation(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];s=api.sessions[sid];b=s.bot
 if channel=='voice':s.pending_reservation_request='Book a table for two next Saturday at eight'
 b.menu_agent.turn=Mock(return_value=('Retrieved policy.',['search_knowledge']))
 say(client,sid,'What is your cancellation policy?')
 assert not b.reserving and not b.reservation_changes.active
 if channel=='voice':assert s.pending_reservation_request=='Book a table for two next Saturday at eight'
 b.reserving=True;b.awaiting_confirmation=True;s.pending_reservation_request=None
 b.slots={'date':'2026-11-18','time':'19:00','party_size':2,'name':'Private Person','phone':'+15555550120'}
 b._last_agent_reply='Please confirm the original table. Is that correct?'
 b.tools.book_table=Mock(side_effect=AssertionError('An interruption cannot commit'))
 before=pending_state(b)
 say(client,sid,'May I bring an outside birthday cake?')
 assert pending_state(b)==before
 reply=say(client,sid,'Yes')
 assert 'confirm the original table' in reply['reply'] and pending_state(b)==before
 b.tools.book_table.assert_not_called()


def test_sentence_selection_retains_private_dining_conditions():
 candidates=[{'source':'groups.md','text':'Private dining requires approval.'},{'source':'groups.md','text':'We cannot promise a private room.'}]
 assert {c['text'] for c in select_quotes('Could you promise a private room?',candidates)}=={c['text'] for c in candidates}


def test_retrieval_failure_does_not_fabricate_an_answer(client):
 sid=client.post('/api/sessions').json()['session_id'];b=api.sessions[sid].bot
 b.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':False,'chunks':[]})
 result=say(client,sid,'May I bring an outside birthday cake?')
 assert not result['sources'] and 'not sure' in result['reply']
 assert result['structured']['tools_called']==['search_knowledge']
 b.chat.assert_not_called()


def test_unfamiliar_policy_can_escape_voice_gate_with_one_shared_decision(client):
 from tests.test_decision_engine import response
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid];b=s.bot
 s.pending_reservation_request='Book a table for two next Saturday at eight'
 b.chat=Mock(return_value=response(intent='policy'))
 b.menu_agent.chat=Mock(side_effect=AssertionError('No second generation'))
 b.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'vouchers.md','text':'Gift vouchers require advance arrangements.'}]})
 result=say(client,sid,'Do you offer gift vouchers?')
 assert result['routing']['intent']=='policy' and result['sources']==['vouchers.md']
 assert 'Returning to your original request' in result['reply'] and 'anything else' not in result['reply']
 assert s.pending_reservation_request and not b.reserving and not b.reservation_changes.active
 b.chat.assert_called_once();b.menu_agent.chat.assert_not_called()
