import copy
from datetime import timedelta
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from conversation_memory import acknowledgement,end_request,status_question
from customer_identity import local_today

def draft(c,channel):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Private Guest','phone':'2025550173','party_size':1,'date':(local_today(b.restaurant)+timedelta(days=5)).isoformat(),'time':'19:00'}
 return sid,b

@pytest.mark.parametrize('channel',['chat','voice'])
def test_completed_booking_survives_information_questions_and_status_does_not_rebook(client,channel):
 sid,b=draft(client,channel)
 done=say(client,sid,'Yes');assert done['structured']['tools_called'].count('book_table')==1
 receipts=copy.deepcopy(api.sessions[sid].reservation_receipts);assert len(receipts)==1
 before=b.tools.booking_store().list(b.tools.business_id);assert len(before)==1
 say(client,sid,'Can I bring my dog?')
 say(client,sid,'Do you have gluten-free options?')
 r=say(client,sid,'Uh sure.')
 assert r['reply']=='Go ahead. What else would you like to ask?' and not r['structured']['tools_called']
 r=say(client,sid,'Sure, did you reserve the d did you reserve the seat for me?')
 assert 'confirmed' in r['reply'] and before[0]['date'] in r['reply'] and '19:00' in r['reply'] and 'Private Guest' in r['reply']
 assert r['structured']['tools_called']==['lookup_reservation']
 assert b.tools.booking_store().list(b.tools.business_id)==before
 assert not api.sessions[sid].pending_reservation_request and api.sessions[sid].reservation_receipts==receipts
 b.chat.assert_not_called()

@pytest.mark.parametrize('resume',['Uh sure.','Sure.','No more questions.',"Let's return to my reservation."])
def test_pending_booking_returns_to_original_details_then_waits_for_final_yes(client,resume):
 sid,b=draft(client,'voice');before=copy.deepcopy(b.slots)
 say(client,sid,'Can I bring my dog?');say(client,sid,'Do you have gluten-free options?')
 r=say(client,sid,resume)
 if resume in {'Uh sure.','Sure.'}:
  assert r['reply']=='Go ahead. What else would you like to ask?'
  assert b.slots==before and not b.tools.booking_store().list(b.tools.business_id)
  r=say(client,sid,'No more questions.')
 assert 'Private Guest' in r['reply'] and before['date'] in r['reply'] and 'correct' in r['reply'].lower()
 assert b.slots==before and not b.tools.booking_store().list(b.tools.business_id)
 say(client,sid,'Yes')
 assert len(b.tools.booking_store().list(b.tools.business_id))==1

def test_pending_status_does_not_discard_fields_or_book(client):
 sid,b=draft(client,'voice');before=copy.deepcopy(b.slots)
 r=say(client,sid,'Did you book my seat?')
 assert 'Nothing has been saved' in r['reply'] and 'Private Guest' in r['reply']
 assert b.slots==before and b.awaiting_confirmation and not b.tools.booking_store().list(b.tools.business_id)
 say(client,sid,'Yes');assert len(b.tools.booking_store().list(b.tools.business_id))==1

@pytest.mark.parametrize('where',['gate','draft','booked'])
def test_end_call_has_priority_and_never_changes_saved_reservations(client,where):
 sid,b=draft(client,'voice')
 if where=='booked':say(client,sid,'Yes')
 if where=='gate':b.reserving=b.awaiting_confirmation=False;api.sessions[sid].pending_reservation_request='Book a table tomorrow'
 before=b.tools.booking_store().list(b.tools.business_id)
 r=say(client,sid,'Okay, end call.')
 assert r['ended'] and not r['structured']['tools_called'] and 'repeat' not in r['reply'].lower()
 assert b.tools.booking_store().list(b.tools.business_id)==before
 b.chat.assert_not_called()

def test_receipts_are_isolated_and_database_status_is_fresh(client):
 sid,b=draft(client,'voice');say(client,sid,'Yes');receipt=api.sessions[sid].reservation_receipts[0]
 other=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 assert not api.sessions[other].reservation_receipts
 stranger=say(client,other,'Did you reserve a seat for me?')
 assert 'Private Guest' not in stranger['reply'] and 'confirmed' not in stranger['reply']
 store=b.tools.booking_store()
 with store.connection() as db:db.execute("UPDATE reservations SET status='cancelled' WHERE id=?",(receipt['id'],));db.commit()
 r=say(client,sid,'Is my reservation confirmed?')
 assert 'cancelled' in r['reply'] and 'is confirmed' not in r['reply']

def test_missing_record_never_claims_success_or_rebooks(client):
 sid,b=draft(client,'voice');say(client,sid,'Yes');before=b.tools.booking_store().list(b.tools.business_id)
 b.tools.lookup_reservation=Mock(return_value={'found':False})
 r=say(client,sid,'Did you book it?')
 assert "couldn't verify" in r['reply'] and b.tools.booking_store().list(b.tools.business_id)==before

def test_returning_after_booking_reads_receipt_instead_of_restarting(client):
 sid,b=draft(client,'voice');say(client,sid,'Yes');before=b.tools.booking_store().list(b.tools.business_id)
 say(client,sid,'Can I bring my dog?')
 r=say(client,sid,"Let's return to my reservation.")
 assert 'is confirmed' in r['reply'] and 'Private Guest' in r['reply']
 assert not api.sessions[sid].pending_reservation_request and b.tools.booking_store().list(b.tools.business_id)==before

def test_receipt_without_success_or_wrong_business_cannot_establish_memory(client):
 from conversation_memory import remember_committed
 sid,b=draft(client,'voice');s=api.sessions[sid]
 for output in [{'booked':False,'id':'fake','phone':'2025550173','business_id':b.tools.business_id},{'booked':True,'id':'fake','phone':'2025550173','business_id':'other-business'}]:
  remember_committed(s,[{'component':'book_table','output':output}])
 assert not s.reservation_receipts

@pytest.mark.parametrize('question',['Is my booking all set?','Have you got my reservation?','Can you confirm my reservation?','Did it go through?','Do I have a table booked?','Are we all set?','What time am I booked for?'])
def test_status_paraphrases_use_existing_receipt(client,question):
 sid,b=draft(client,'voice');say(client,sid,'Yes');before=b.tools.booking_store().list(b.tools.business_id)
 r=say(client,sid,question)
 assert 'is confirmed' in r['reply'] and 'Private Guest' in r['reply']
 assert r['structured']['tools_called']==['lookup_reservation'] and b.tools.booking_store().list(b.tools.business_id)==before

@pytest.mark.parametrize('question',['Did you allow outside cakes with a reservation?','Can you confirm your cancellation policy?','Did you make gluten-free options?'])
def test_status_never_steals_policy_or_dietary_questions(question):assert not status_question(question)

@pytest.mark.parametrize('text',['Uh sure.','A sure.','Yeah, sure.','Of course.','Yes please.'])
def test_acknowledgement_is_complete_not_a_new_topic(text):assert acknowledgement(text)

@pytest.mark.parametrize('text',['Yeah sure, can I bring my dog?','Yes but change the time','No thank you','Surely there is parking'])
def test_mixed_acknowledgements_are_not_consumed(text):assert not acknowledgement(text)

@pytest.mark.parametrize('text',["Don't end the call.",'Can I book a table at the end of the month?','When do you stop serving?'])
def test_non_end_commands_do_not_end_call(text):assert not end_request(text)

@pytest.mark.parametrize('text',['Book another table','Did you book it and cancel the other one?','Can I reserve a table?'])
def test_status_does_not_swallow_commands(text):assert not status_question(text)
