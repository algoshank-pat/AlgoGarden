"""Side answers must return to the correct unfinished step, never write."""
import copy
from uuid import uuid4
import pytest
import chat_api as api
from conversation_followup import QUESTION,INVITE
from information_requests import pending_state
from tests.test_customer_feedback import client,say
from tests.test_answer_followup import begin

QUESTIONS=['What is your address?','Where can I park?','Can I bring my dog inside?',
 'Do you have gluten-free options?','What are your opening hours?',
 'Can I bring an outside birthday cake?','Do you have high chairs?',
 'What do I pay you?','Can I pay by credit card?']
STAGES=['gate','phone','name','name_check','party_size','date','time','confirmation',
 'rejected_confirmation','new_alternatives','existing_choice','availability_date',
 'availability_time','availability_party_size','lookup_phone','lookup_selection',
 'update_fields','update_alternatives','update_confirmation','cancel_confirmation','completed','idle']

@pytest.fixture(autouse=True)
def private(monkeypatch,tmp_path):
 monkeypatch.setenv('RECEPTIONIST_FIXTURE_DIR',str(tmp_path))
 monkeypatch.setenv('LANGSMITH_TRACING','false')

def setup_stage(client,channel,stage):
 existing=stage.startswith(('lookup_','update_','cancel_')) or stage=='completed'
 sid,s,b=begin(client,channel,'completed' if existing else 'confirmation')
 original={'name':'Private Guest','phone':'2025550186','date':'2026-11-09','time':'19:00','party_size':2}
 if not existing:b.slots=copy.deepcopy(original);b._awaiting_slot=None
 if stage in {'phone','name','party_size','date','time'}:
  b.awaiting_confirmation=False;b.slots.pop(stage);b._awaiting_slot=stage
 elif stage=='name_check':
  b.awaiting_confirmation=False;b.slots.pop('name');b._awaiting_slot='name'
  b._name_candidate='Private Guest';b._name_readback='I heard Private Guest. Is that name correct?'
 elif stage=='gate':
  b.reserving=b.awaiting_confirmation=False;b.slots={};s.pending_reservation_request='Book a table for two tomorrow at seven.'
 elif stage.startswith('availability_'):
  b.reserving=b.awaiting_confirmation=False;b.slots={};field=stage.removeprefix('availability_')
  b._availability_enquiry={k:original[k] for k in ['date','time','party_size'] if k!=field};b._availability_expected=field
 elif stage in {'rejected_confirmation','new_alternatives','existing_choice'}:
  b.awaiting_confirmation=False
  if stage=='new_alternatives':b.slots.pop('time');b._awaiting_slot='time';b._offered_alternatives=[{'date':original['date'],'time':'20:00','party_size':2}]
  if stage=='existing_choice':b._existing_choice=True
 elif stage=='idle':b.reserving=b.awaiting_confirmation=False;b.slots={}
 elif existing and stage!='completed':
  row=b.tools.booking_store().list(b.tools.business_id)[0];c=b.reservation_changes
  c.begin('cancel' if stage=='cancel_confirmation' else 'lookup' if stage.startswith('lookup_') else 'update',None if stage=='lookup_phone' else row['phone'])
  if stage=='lookup_selection':c.matches=[row,{**row,'id':'another-private-id','time':'20:00'}];c.selection_offered=True
  elif stage!='lookup_phone':c.current=row
  if stage=='update_alternatives':c.offered_alternatives=[{'date':row['date'],'time':'20:30','party_size':2}]
  if stage in {'update_confirmation','cancel_confirmation'}:c.pending=True;c.request_key=str(uuid4());c.changes={'time':'20:00'} if stage=='update_confirmation' else {}
 return sid,s,b

def check_return(stage,reply):
 t=reply.lower()
 expected={'gate':'already have a reservation','phone':'phone','name':'name','name_check':'is that name correct',
 'party_size':'guests','date':'date','time':'time','confirmation':'is that correct',
 'rejected_confirmation':'change','new_alternatives':'8:00 pm','existing_choice':'existing reservation',
 'availability_date':'date','availability_time':'time','availability_party_size':'people',
 'lookup_phone':'phone','lookup_selection':'which reservation','update_fields':'change',
 'update_alternatives':'8:30 pm','update_confirmation':'is that correct','cancel_confirmation':'is that correct'}
 assert expected[stage] in t,(stage,reply)

def state(bot):return (pending_state(bot),copy.deepcopy(getattr(bot,'_availability_enquiry',None)))

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('stage',STAGES)
def test_all_side_answers_return_without_losing_details(client,channel,stage):
 sid,s,b=setup_stage(client,channel,stage);saved=state(b);rows=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 for question in QUESTIONS:
  answer=say(client,sid,question)
  assert answer['reply'].endswith(QUESTION),(stage,question,answer['reply'])
  assert state(b)==saved and b.tools.booking_store().list(b.tools.business_id)==rows
  assert say(client,sid,'Yes')['reply']==INVITE
  returned=say(client,sid,'No more questions, thank you.')
  if stage in {'idle','completed'}:
   assert returned['survey']['status']=='offered' and not s.awaiting_reservation_return
   # Resume asking without rating/closing this private scenario.
   say(client,sid,'I have another question.')
  else:
   check_return(stage,returned['reply'])
   assert returned['survey']['status']=='not_offered'
  assert state(b)==saved and b.tools.booking_store().list(b.tools.business_id)==rows
 b.chat.assert_not_called()

@pytest.mark.parametrize('stage',[s for s in STAGES if s not in {'idle','completed'}])
def test_silence_timeout_returns_to_same_pending_step(client,monkeypatch,stage):
 import conversation_waits as waits
 sid,s,b=setup_stage(client,'voice',stage);saved=state(b);rows=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
 r=say(client,sid,'Where can I park?');assert r['conversation_wait'] is not None
 token=r['conversation_wait']['token'];now=[100.];monkeypatch.setattr(waits,'clock',lambda:now[0])
 assert client.post(f'/api/sessions/{sid}/waits/{token}/arm').status_code==200
 now[0]=115.
 r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'[wait elapsed]','input_kind':'timeout','wait_token':token}).json()
 check_return(stage,r['reply']);assert state(b)==saved and b.tools.booking_store().list(b.tools.business_id)==rows
