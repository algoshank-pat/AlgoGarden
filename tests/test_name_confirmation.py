from unittest.mock import Mock
import pytest
import chat_api as api
from conversation_controls import name_assent
from tests.test_customer_feedback import client,say

APPROVALS=['Yes, that name is correct.','That name is correct.',"Yes, that's my name.",'You got my name right.','Yes, you have my name correct.','The name is right.','Yes, that name is correct, thank you.']

@pytest.mark.parametrize('text',APPROVALS)
def test_complete_name_approval(text):assert name_assent(text)

@pytest.mark.parametrize('text',["No, that name is not correct.",'Yes, that name is correct, but change the time.','Is that name correct?','Yes, that name is correct. Can I bring a dog?','Yes, my name is a different name.'])
def test_negative_questions_and_mixed_requests_are_not_name_approval(text):assert not name_assent(text)

def prepare(c,channel,complete):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b.slots={'phone':'12025550173','party_size':2};b._awaiting_slot='name'
 if complete:b.slots.update(date='2027-02-20',time='20:30')
 b._name_candidate='A B Lamb';b._name_readback='I heard A B Lamb. Is that name correct?'
 b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-20','time':'20:30'})
 return sid,b

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('text',APPROVALS)
def test_name_approval_advances_to_missing_date_without_inference(client,channel,text):
 sid,b=prepare(client,channel,False)
 r=say(client,sid,text)
 assert b.slots=={'phone':'12025550173','party_size':2,'name':'A B Lamb'}
 assert b._name_candidate is None and 'date' in r['reply'].lower() and 'name correct' not in r['reply'].lower()
 assert not b.awaiting_confirmation;b.tools.book_table.assert_not_called();b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_name_approval_cannot_book_before_separate_full_confirmation(client,channel):
 sid,b=prepare(client,channel,True)
 r=say(client,sid,'Yes, that name is correct.')
 assert b.awaiting_confirmation and 'A B Lamb' in r['reply'] and 'correct' in r['reply'].lower()
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()
 say(client,sid,'Yes');b.tools.book_table.assert_called_once()

def test_name_approval_during_full_booking_confirmation_is_not_general_yes(client):
 sid,b=prepare(client,'voice',True);b.slots['name']='A B Lamb';b._name_candidate=None;b.awaiting_confirmation=True
 result=say(client,sid,'Yes, that name is correct.')
 assert b.awaiting_confirmation and 'not booked' in result['reply'].lower()
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()

def test_information_interruption_still_repeats_name_before_accepting_yes(client):
 sid,b=prepare(client,'voice',False)
 b.menu_agent.turn=Mock(return_value=('Pets are not permitted indoors.',['search_knowledge']))
 say(client,sid,'Can I bring a dog?')
 result=say(client,sid,'Yes, that name is correct.')
 assert 'Is that name correct?' in result['reply'] and 'name' not in b.slots
 say(client,sid,'Yes, that name is correct.')
 assert b.slots['name']=='A B Lamb'
 b.tools.book_table.assert_not_called()

def test_name_approval_after_pause_follows_same_resume_guard_as_yes(client):
 sid,b=prepare(client,'voice',False)
 say(client,sid,'Wait a moment.')
 result=say(client,sid,'Yes, that name is correct.')
 assert 'Is that name correct?' in result['reply'] and 'name' not in b.slots
 say(client,sid,'Yes, that name is correct.')
 assert b.slots['name']=='A B Lamb'
 b.tools.book_table.assert_not_called()


def test_spoken_name_needs_no_spelling_and_correction_waits_for_booking_confirmation(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b._awaiting_slot='phone'
 b.slots={'party_size':2,'date':'2027-02-20','time':'20:30'}
 r=say(client,sid,'2025550173')
 assert r['reply']=='May I have your name for the reservation?'
 r=say(client,sid,'My name is Sam David.')
 assert r['reply']=='I heard Sam David. Is that name correct?'
 assert 'name' not in b.slots and not b.tools.booking_store().list(b.tools.business_id)
 r=say(client,sid,'No')
 assert r['reply']=='Could you say your name again?'
 r=say(client,sid,'Sam Davis')
 assert r['reply']=='I heard Sam Davis. Is that name correct?'
 r=say(client,sid,'Yes, that name is correct.')
 assert b.slots['name']=='Sam Davis' and b.awaiting_confirmation
 assert 'Sam Davis' in r['reply'] and not b.tools.booking_store().list(b.tools.business_id)
 r=say(client,sid,'Yes, please book it.')
 assert r['reply']=='Your reservation is confirmed. Do you have any questions?'
 assert len(b.tools.booking_store().list(b.tools.business_id))==1
 b.chat.assert_not_called()
