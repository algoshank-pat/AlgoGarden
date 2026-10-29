"""Actual-model/RAG policy interruption, confirmation and restaurant-isolation checks."""
import os,json,sqlite3,shutil,time,re,hashlib
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).parent

def main():
 out=ROOT/'results/policy-routing';fixture=out/'workflow-private';fixture.mkdir(exist_ok=True)
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(fixture/'reservations.sqlite3') as b:a.backup(b)
 if not (fixture/'chroma').exists():shutil.copytree(ROOT/'data/chroma',fixture/'chroma')
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(fixture),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline')
 import chat_api as api,knowledge,receptionist as app
 from fastapi.testclient import TestClient
 from information_requests import pending_state
 from run_private_decision_acceptance import production_baseline
 from datetime import timedelta
 from customer_identity import local_today,normalize_phone
 api.MODEL=app.DEFAULT_MODEL
 before=production_baseline();assert before['count']==1911
 def rows():
  with sqlite3.connect(fixture/'reservations.sqlite3') as db:return db.execute('select * from reservations order by id').fetchall()
 def sha(rows):return hashlib.sha256(json.dumps(rows,default=str).encode()).hexdigest()
 output={'model':api.MODEL,'baseline_before':before,'turns':[],'checks':[]}
 def check(name,passed):output['checks'].append({'name':name,'passed':bool(passed)});print(name,'PASS' if passed else 'FAIL',flush=True)
 with TestClient(api.app) as client:
  def start(channel):return client.post('/api/sessions',json={'channel':channel}).json()['session_id']
  def say(sid,text):
   started=time.perf_counter();r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();result=r.json()
   output['turns'].append({'session_id':sid,'caller':text,'latency_ms':round(1000*(time.perf_counter()-started),2),'result':result})
   check('one-generation-budget',sum(c['component']=='ollama.chat' for c in result['structured']['calls'])<=1)
   return result
  for channel in ['chat','voice']:
   sid=start(channel);b=api.sessions[sid].bot
   day=local_today(b.restaurant)+timedelta(days=45)
   # This is a deliberately prepared, private draft with all fields collected.
   b.reserving=True;b.awaiting_confirmation=True
   b.slots={'date':day.isoformat(),'time':'19:00','party_size':2,'name':'Private Policy '+channel,'phone':'+12025550151' if channel=='chat' else '+12025550152'}
   b._last_agent_reply='Please confirm this reservation. Is that correct?'
   original=pending_state(b);old=rows()
   answer=say(sid,'May I bring a cake that I bought at a bakery?')
   check(channel+'-policy-answer', 'welcome' in answer['reply'] and 'advance' in answer['reply'] and 'search_knowledge' in answer['structured']['tools_called'])
   check(channel+'-draft-and-db-preserved',pending_state(b)==original and rows()==old)
   resumed=say(sid,'Yes')
   check(channel+'-resume-without-implicit-commit','confirm' in resumed['reply'].lower() and rows()==old)
   # A second explicit confirmation is required to save the unchanged draft.
   confirmed=say(sid,'Yes, please')
   new=rows();check(channel+'-explicit-confirmation-saves-once',len(new)==len(old)+1 and 'book_table' in confirmed['structured']['tools_called'])
   client.delete(f'/api/sessions/{sid}')
  for channel in ['chat','voice']:
   sid=start(channel);b=api.sessions[sid].bot
   b.reserving=True;b._awaiting_slot='phone'
   b.slots={'date':(local_today(b.restaurant)+timedelta(days=46)).isoformat(),'time':'19:00','party_size':2}
   original=pending_state(b);old=rows()
   answer=say(sid,'What is your policy on dogs in the dining room?')
   check(channel+'-phone-policy-preserves-draft',pending_state(b)==original and 'not permitted' in answer['reply'])
   phone='+12025550161' if channel=='chat' else '+12025550162'
   number=say(sid,phone)
   check(channel+'-resume-phone-collection',normalize_phone(b.slots.get('phone'),b.restaurant.get('phone_region','US'))==phone and 'name' in number['reply'].lower())
   name=say(sid,'My name is Private Guest')
   check(channel+'-resume-name-readback',b.slots.get('name')=='Private Guest' and b.awaiting_confirmation and rows()==old)
   client.delete(f'/api/sessions/{sid}')
  # Two interleaved sessions ask the same question against different scoped corpora.
  original_restaurant=api.restaurant
  scoped=[]
  for business in ['malnad_mane','maison_olive']:
   try:knowledge.chroma_client().get_collection(knowledge.collection_name(business),embedding_function=None)
   except Exception:knowledge.ingest(business)
   api.restaurant={**original_restaurant,'knowledge_id':business,'id':'private-'+business,'name':business}
   scoped.append((business,start('chat' if business=='malnad_mane' else 'voice')))
  api.restaurant=original_restaurant
  old=rows()
  for business,sid in scoped:
   first=say(sid,'May I bring an outside birthday cake?')
   second=say(sid,'How long do you hold a table if we arrive late?')
   check(business+'-cake-scope',bool(re.search('welcome.*advance' if business=='malnad_mane' else 'not accepted',first['reply'],re.I)))
   check(business+'-late-scope',('15 minutes' if business=='malnad_mane' else '10 minutes') in second['reply'])
   check(business+'-retrieval',all('search_knowledge' in r['structured']['tools_called'] for r in [first,second]))
   client.delete(f'/api/sessions/{sid}')
  check('scoped-enquiries-no-writes',rows()==old)
  # A policy outside the known topic vocabulary must use the actual one-call model backstop.
  sid=start('chat');old=rows()
  unknown=say(sid,'Do you offer gift vouchers?')
  check('unfamiliar-policy-model-route',unknown.get('routing',{}).get('intent')=='policy' and 'search_knowledge' in unknown['structured']['tools_called'])
  check('unsupported-policy-no-fabrication',not unknown['sources'] and rows()==old)
  client.delete(f'/api/sessions/{sid}')
  sid=start('voice');s=api.sessions[sid];s.pending_reservation_request='Book a table for two next Saturday at eight'
  old=rows();unknown=say(sid,'Do you offer gift vouchers?')
  check('unfamiliar-policy-escapes-voice-gate',unknown.get('routing',{}).get('intent')=='policy' and 'search_knowledge' in unknown['structured']['tools_called'] and s.pending_reservation_request is not None and not s.bot.reserving and rows()==old)
  client.delete(f'/api/sessions/{sid}')
 output['baseline_after']=production_baseline();check('production-baseline-unchanged',output['baseline_after']==before)
 output['passed']=all(c['passed'] for c in output['checks']);(out/'workflow-validation.json').write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf-8')
 print('WORKFLOWS',output['passed'],flush=True)
if __name__=='__main__':main()
