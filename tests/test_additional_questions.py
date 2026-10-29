from unittest.mock import Mock
import pytest
import chat_api as api
from customer_intent import additional_questions,ADDITIONAL_QUESTIONS_REPLY
from information_requests import pending_state
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('text',[
 'I have additional questions',"I've got a few more questions.",'We have some other questions.',
 'Can I ask you another question?','May I ask a question?','One more question please.',
 'I would like to ask a few questions.',"I've got a quick follow-up question.",
 'Yes, I have more questions about my reservation.',
])
def test_natural_invitation_variants(text):assert additional_questions(text)

@pytest.mark.parametrize('text',[
 'I have no additional questions.',"I don't have any more questions.",
 'I have additional questions. Can I bring my dog?',
 'Can I ask if parking is free?', 'I have a reservation.',
 'Who is Albert Einstein?', 'My name is More Questions',
])
def test_does_not_swallow_negatives_or_actual_questions(text):assert not additional_questions(text)

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('state',['idle','voice_gate','phone','confirmation','more_help','survey_offer','survey_comment','survey_rating'])
def test_invitation_preserves_work_and_costs_no_inference(client,channel,state):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];s=api.sessions[sid];b=s.bot
 if state=='voice_gate':s.pending_reservation_request='Book a table for two tomorrow at seven'
 if state in {'phone','confirmation'}:
  b.reserving=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2}
  if state=='phone':b._awaiting_slot='phone'
  else:b.slots.update(name='Private Guest',phone='+12025550173');b.awaiting_confirmation=True;b._last_agent_reply='Please confirm your table. Is that correct?'
 if state=='more_help':s.awaiting_more_help=True
 if state.startswith('survey_'):s.survey.update(status={'survey_offer':'offered','survey_comment':'awaiting_comment','survey_rating':'awaiting_rating'}[state])
 original=pending_state(b);pending=s.pending_reservation_request;b.failures=3
 b.menu_agent.last_sources=['old-policy.md'];b.tools.book_table=Mock(side_effect=AssertionError('Invitation cannot book'))
 result=say(client,sid,'I have additional questions')
 assert result['reply']==ADDITIONAL_QUESTIONS_REPLY and not result['ended'] and not result['handoff']
 assert pending_state(b)==original and s.pending_reservation_request==pending
 assert not s.awaiting_more_help and b.failures==0
 assert result['sources']==[] and result['structured']['tools_called']==[] and not result['structured']['calls']
 assert result['routing']['reason']=='additional_questions'
 if state.startswith('survey_'):assert result['survey']['status']=='skipped' and result['survey']['rating'] is None and not result['survey'].get('comment')
 b.chat.assert_not_called();b.tools.book_table.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_actual_question_after_invitation_gets_answered(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 say(client,sid,'I have additional questions')
 answer=say(client,sid,'Can I bring my dog?')
 assert 'Pets are not permitted in the indoor dining area.' in answer['reply'] and answer['sources']==['kids_pets.md']
 assert not api.sessions[sid].bot.reserving
 api.sessions[sid].bot.chat.assert_not_called()

def test_yes_after_invitation_cannot_commit_pending_readback(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b.awaiting_confirmation=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2,'name':'Private Guest','phone':'+12025550173'}
 b._last_agent_reply='Please confirm your original booking. Is that correct?'
 b.tools.book_table=Mock(side_effect=AssertionError('Yes after invitation is not booking confirmation'))
 original=pending_state(b);say(client,sid,'I have additional questions')
 reply=say(client,sid,'Yes')
 assert reply['reply']==b._last_agent_reply and pending_state(b)==original
 b.tools.book_table.assert_not_called()

def test_combined_real_question_is_answered_instead_of_invited(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 result=say(client,sid,'I have additional questions. Can I bring my dog?')
 assert 'Pets are not permitted' in result['reply'] and result['reply']!=ADDITIONAL_QUESTIONS_REPLY

@pytest.mark.parametrize('phrase',["Let's go back to my reservation.",'Let us go back to my reservation.','Can we return to our booking?','Okay, resume my reservation please.','Continue with the booking.','Back to my reservation.'])
@pytest.mark.parametrize('channel',['chat','voice'])
def test_questions_then_resume_repeats_readback_without_committing(client,phrase,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b.awaiting_confirmation=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2,'name':'Private Guest','phone':'+12025550173'}
 original_readback='Table for 2 on 2027-02-17 at 19:00, under Private Guest. Is that correct?'
 b._last_agent_reply=original_readback;original=pending_state(b)
 b.tools.book_table=Mock(side_effect=AssertionError('No write before final confirmation'))
 assert say(client,sid,'I have additional questions')['reply']==ADDITIONAL_QUESTIONS_REPLY
 parking=say(client,sid,'Where can I park?')
 assert 'get_location' in parking['structured']['tools_called'] and api.restaurant['parking'] in parking['reply']
 assert pending_state(b)==original
 assert 'Pets are not permitted' in say(client,sid,'Can I bring my dog?')['reply']
 returned=say(client,sid,phrase)
 assert returned['reply']==original_readback and returned['structured']['tools_called']==[]
 assert pending_state(b)==original and b._information_readback is None
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()
 b.tools.book_table=Mock(return_value={'booked':True,'id':'private-test','date':'2027-02-17','time':'19:00'})
 confirmed=say(client,sid,'Yes please')
 assert confirmed['structured']['tools_called']==['book_table']
 b.tools.book_table.assert_called_once_with(date='2027-02-17',time='19:00',party_size=2,name='Private Guest',phone='+12025550173')

@pytest.mark.parametrize('text',['Go back to my reservation but make it four people.','Resume my booking at eight thirty instead.','Change my reservation to tomorrow.','Resume my booking 830.','Return to my reservation 2027-02-18.'])
def test_resume_control_does_not_swallow_explicit_corrections(text):
 from information_requests import resume_reservation
 assert not resume_reservation(text)


def test_stale_readback_is_not_restored_by_resume(client):
 from information_requests import information_turn
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b.awaiting_confirmation=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2,'name':'Private Guest','phone':'+12025550173'}
 b._last_agent_reply='Original booking at seven. Is that correct?';say(client,sid,'I have additional questions')
 b.awaiting_confirmation=False;b.slots.pop('time');b._awaiting_slot='time'
 assert information_turn(b,'Go back to my reservation') is None and b._information_readback is None
 assert 'time' not in b.slots
