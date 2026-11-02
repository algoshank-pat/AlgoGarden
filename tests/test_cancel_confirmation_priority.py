from datetime import timedelta
from uuid import uuid4
from types import SimpleNamespace
import json
import pytest
import chat_api as api
from customer_identity import local_today
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('approve',[True,False])
def test_phone_cancel_has_one_question_and_requires_fresh_confirmation(client,channel,approve):
 day=(local_today(api.restaurant)+timedelta(days=14)).isoformat()
 tools=api.engine.Tools(api.restaurant)
 booked=tools.book_table('Private Cancel Guest','2025550183',2,day,'8 pm',request_key=str(uuid4()))
 assert booked['booked']
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 # Exercise literal phone lookup/confirmation independently of LLM routing.
 api.sessions[sid].bot.unified_decisions=False
 api.sessions[sid].bot.chat.side_effect=None
 api.sessions[sid].bot.chat.return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({'phone':'2025550183'})))
 readback=say(client,sid,'Cancel my reservation. My phone number is 2025550183.')
 assert 'Please say yes to confirm.' in readback['reply'], (readback['routing'],api.sessions[sid].bot.chat.call_args)
 assert 'other questions' not in readback['reply'].lower()
 assert api.sessions[sid].bot.reservation_changes.pending
 assert not api.sessions[sid].awaiting_more_help
 assert tools.booking_store().list(tools.business_id)[0]['status']=='active'
 request={'request_id':str(uuid4()),'text':'Yes please' if approve else 'No'}
 first=client.post(f'/api/sessions/{sid}/turns',json=request).json()
 second=client.post(f'/api/sessions/{sid}/turns',json=request).json()
 assert first==second
 assert tools.booking_store().list(tools.business_id)[0]['status']==('cancelled' if approve else 'active')
 if approve:
  assert 'cancelled' in first['reply']
  assert [e['kind'] for e in tools.booking_store().history(tools.business_id)]==['created','cancelled']
 else:assert 'No changes were made' in first['reply']
