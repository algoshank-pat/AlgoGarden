from unittest.mock import Mock
import pytest
from uuid import uuid4
import chat_api as api
from tests.test_customer_feedback import client,say
from voice.phrases import cacheable

def draft(c,channel='voice'):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Private Guest','phone':'2025550173','party_size':2,'date':'2027-02-20','time':'20:30'}
 return sid,b

def test_voice_success_is_exact_short_confirmation_and_receipt_is_retained(client):
 sid,b=draft(client)
 b._paused=True
 readback=say(client,sid,'Continue.')
 assert '2027-02-20' in readback['reply'] and '20:30' in readback['reply'] and 'Private Guest' in readback['reply']
 assert not b.tools.booking_store().list(b.tools.business_id)
 payload={'request_id':str(uuid4()),'text':'Yes'}
 r=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
 assert r['reply']=='Your reservation is confirmed. Do you have any questions?'
 assert client.post(f'/api/sessions/{sid}/turns',json=payload).json()==r
 assert len(b.tools.booking_store().list(b.tools.business_id))==1
 assert len(api.sessions[sid].reservation_receipts)==1
 assert cacheable(r['reply'])
 b.chat.assert_not_called()


@pytest.mark.parametrize('answer',['No, thank you.','No more questions.'])
def test_questions_after_confirmation_keep_booking_and_allow_feedback(client,answer):
 sid,b=draft(client)
 say(client,sid,'Yes')
 saved=b.tools.booking_store().list(b.tools.business_id)
 assert len(saved)==1 and api.sessions[sid].awaiting_more_help
 r=say(client,sid,'Yes')
 assert r['reply']=='Go ahead. What else would you like to ask?'
 assert not r['structured']['tools_called'] and not r['ended']
 r=say(client,sid,'What are your opening hours?')
 assert 'get_hours' in r['structured']['tools_called']
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 r=say(client,sid,answer)
 assert r['survey']['status']=='offered'
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()


@pytest.mark.parametrize('answer',['No, thank you.','No more questions.','No other questions.','No further questions.'])
def test_no_questions_after_confirmation_offers_feedback_without_extra_write(client,answer):
 sid,b=draft(client)
 say(client,sid,'Yes')
 saved=b.tools.booking_store().list(b.tools.business_id)
 r=say(client,sid,answer)
 assert r['survey']['status']=='offered'
 assert b.tools.booking_store().list(b.tools.business_id)==saved
 b.chat.assert_not_called()

def test_failure_never_uses_success_acknowledgement(client):
 sid,b=draft(client)
 b.tools.book_table=Mock(return_value={'booked':False,'reason':'That time is fully booked.','alternatives':[]})
 r=say(client,sid,'Yes')
 assert r['reply']!='Your reservation is confirmed.' and 'confirmed' not in r['reply'].lower()
 assert not api.sessions[sid].reservation_receipts and not b.tools.booking_store().list(b.tools.business_id)

def test_chat_keeps_its_existing_confirmation_details(client):
 sid,b=draft(client,'chat')
 r=say(client,sid,'Yes')
 assert 'Your table is booked' in r['reply'] and '2027-02-20' in r['reply'] and '20:30' in r['reply']
