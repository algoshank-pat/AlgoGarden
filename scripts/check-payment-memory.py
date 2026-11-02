"""Actual API/model configuration, isolated booking records, payment interruptions."""
import hashlib,json,os,sys,urllib.request
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/payment-memory-greeting-20261101'/str(uuid4());(OUT/'private').mkdir(parents=True)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env');os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'private'),LANGSMITH_TRACING='false')
import chat_api as api
from fastapi.testclient import TestClient
from customer_identity import local_today
from receptionist import parse_date,parse_time
report={'method':'Actual default model configuration; voice-text and chat, no physical microphone','turns':[]}
model=next(m for m in json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags'))['models'] if m['name']==api.MODEL)
assert model['digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
report['model']={k:model[k] for k in ['name','digest']}
try:
 with TestClient(api.app) as client:
  for channel in ['voice','chat']:
   sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
   day=(local_today(b.restaurant)+timedelta(days=6)).isoformat();phone='2025550192' if channel=='voice' else '2025550193'
   def turn(text,expected,count=0,kind='text',wait_token=None):
    payload={'request_id':str(uuid4()),'text':text,'input_kind':kind}
    if wait_token:payload['wait_token']=wait_token
    response=client.post(f'/api/sessions/{sid}/turns',json=payload);response.raise_for_status();r=response.json()
    rows=[x for x in b.tools.booking_store().list(b.tools.business_id) if x['session_id']==sid]
    checks={'response':expected.lower() in r['reply'].lower(),'writes':len(rows)==count,'not_failed':not r.get('failed')}
    calls=[c for c in r['structured']['calls'] if c['component']=='ollama.chat'];checks['call_budget']=len(calls)<=1
    for call in calls:
     k=call['inputs']['kwargs'];checks['settings']=k['model']==api.MODEL and k['think'] is False and k['keep_alive']=='30m' and k['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
    report['turns'].append({'channel':channel,'customer':text,'reply':r['reply'],'tools':r['structured']['tools_called'],'latency_ms':r['latency_ms'],'slots':dict(b.slots),'checks':checks,'pass':all(checks.values())})
    assert all(checks.values()),(text,r['reply'],checks)
    return r
   r=turn('What do I pay you?','credit cards')
   token=r['conversation_wait']['token'];client.post(f'/api/sessions/{sid}/waits/{token}/arm').raise_for_status()
   # Advance only this private test's deadline, not runtime timers or user settings.
   api.sessions[sid].conversation_wait['deadline']=0
   turn('[Conversation wait elapsed]','Would you like help with a reservation?',kind='timeout',wait_token=token)
   turn('Sure.','have a reservation' if channel=='voice' else 'phone')
   if channel=='voice':turn('No.','phone')
   # Supply booking details once, then interrupt at identity/readback stages.
   turn(f'Book a table for two on {day} at seven in the evening.','phone')
   turn('Can I pay cash?','cash');turn('No more questions.','phone')
   turn(f'My phone number is {phone}.','name')
   turn('Do you accept Apple Pay?','Apple Pay');turn('No more questions.','name')
   turn('My name is Alex Morgan.','Alex Morgan')
   if channel=='voice':turn('Yes, that name is correct.','Is that correct?')
   turn('Can I use a credit card?','credit cards')
   turn('No more questions.','Is that correct?')
   assert b.slots['name']=='Alex Morgan' and b.slots['party_size']==2 and parse_time(b.slots['time'])=='19:00' and parse_date(b.slots['date'],today=local_today(b.restaurant)).isoformat()==day
   turn('Yes, please confirm the reservation.','Your reservation is confirmed.' if channel=='voice' else 'Your table is booked',1)
   turn('Can I pay by Google Pay?','Google Pay',1)
   turn('Is my reservation confirmed?','is confirmed',1)
   turn('No more questions.','optional',1)
   turn('No thanks.','calling',1)
  report['pass']=True
finally:
 report['source_hashes']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['conversation_followup.py','conversation_waits.py','chat_api.py','booking_fastpath.py']}
 (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(OUT/'report.json',flush=True)
