"""Paired live-source policy evaluation. All mutable resources are private."""
import os,json,shutil,sqlite3,hashlib,time,argparse,re,math
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parent
FILES=['receptionist.py','routing.py','decision_engine.py','chat_api.py','information_requests.py','knowledge.py']

def baseline():
 from run_private_decision_acceptance import production_baseline
 return production_baseline()

def main():
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['before','after']);args=p.parse_args()
 out=ROOT/'results/policy-routing';out.mkdir(parents=True,exist_ok=True)
 fixture=out/(args.phase+'-private');fixture.mkdir(exist_ok=True)
 before=baseline();assert before['count']==1911
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(fixture/'reservations.sqlite3') as b:a.backup(b)
 if not (fixture/'chroma').exists():shutil.copytree(ROOT/'data/chroma',fixture/'chroma')
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(fixture),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline',RECEPTIONIST_DECISION_FASTPATHS='safe')
 import chat_api as api, receptionist as app, knowledge
 from fastapi.testclient import TestClient
 from information_requests import pending_state
 api.MODEL=app.DEFAULT_MODEL
 frozen=ROOT/'results/customer100/frozen-20261027T214340Z'
 evidence=json.loads((frozen/'run/report.json').read_text(encoding='utf-8'))
 gold=json.loads((frozen/'scoring-rules.json').read_text(encoding='utf-8'))['policy_gold']
 cases=[]
 fresh=["If we're stuck in traffic, how long before our seats are released?",'How far ahead should we let you know we cannot make dinner?','Would a puppy be allowed into the dining room?','Do guests get to supply the champagne themselves?',"We bought a celebration cake at a bakery. May we bring it along?",'Could our toddler get a highchair?',"Does your till take UPI payments?",'Is exclusive use of a separate dining room something you can promise?',"Could each of us settle our own part of the check?",'Is seating for someone using a wheelchair something you can assure us of?']
 for record in evidence['results']:
  n=record['case']['id']
  if not 86<=n<=95:continue
  prompts=[t['caller'] for t in record['turns'] if t['caller'].lower()!='hello']
  for channel in ['chat','voice']:cases.append({'id':f'original-{n}-{channel}','kind':'policy','channel':channel,'state':'idle','text':prompts[0],'gold':gold[str(n)]})
  cases.append({'id':f'recovery-{n}','kind':'recovery','channel':'chat' if n%2 else 'voice','state':'phone','text':prompts[1],'gold':gold[str(n)]})
  cases.append({'id':f'fresh-{n}','kind':'fresh','channel':'voice' if n%2 else 'chat','state':'phone','text':fresh[n-86],'gold':gold[str(n)]})
 contrasts=[('Please book a table for two on November 18 at seven in the evening.','reservation'),('Cancel my reservation.','reservation'),('What desserts are on the menu?','menu'),('What wines do you serve?','menu')]
 for i,(text,intent) in enumerate(contrasts):
  for channel in ['chat','voice']:cases.append({'id':f'contrast-{i}-{channel}','kind':'contrast','channel':channel,'state':'idle','text':text,'intent':intent})
 manifest=out/'manifest.json'
 if args.phase=='before':manifest.write_text(json.dumps(cases,indent=2),encoding='utf-8')
 else:assert cases==json.loads(manifest.read_text(encoding='utf-8'))
 report={'phase':args.phase,'model':api.MODEL,'fixture':str(fixture),'baseline_before':before,'source_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in FILES},'cases':[],'method':'Actual default model and scoped RAG via FastAPI TestClient. Voice-text only, not microphone. Identical manifest and workflow state per phase; no mocked inference or retrieval.'}
 snapshot=out/(args.phase+'-sources');snapshot.mkdir(exist_ok=True)
 for f in FILES:shutil.copy2(ROOT/f,snapshot/f)
 with TestClient(api.app) as client:
  for case in cases:
   sid=client.post('/api/sessions',json={'channel':case['channel']}).json()['session_id'];session=api.sessions[sid];bot=session.bot
   if case['state']=='phone':
    bot.reserving=True;bot.slots={'date':'2026-11-18','time':'19:00','party_size':2};bot._awaiting_slot='phone'
    bot._last_agent_reply='What phone number can I use for your reservation?';bot._last_topic='reservation'
   state=pending_state(bot)
   with sqlite3.connect(fixture/'reservations.sqlite3') as db:prior=db.execute('select count(*) from reservations').fetchone()[0]
   started=time.perf_counter();response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':case['text']});elapsed=(time.perf_counter()-started)*1000
   response.raise_for_status();result=response.json();tools=result['structured']['tools_called'];calls=result['structured']['calls'];generations=[c for c in calls if c['component']=='ollama.chat']
   with sqlite3.connect(fixture/'reservations.sqlite3') as db:following=db.execute('select count(*) from reservations').fetchone()[0]
   preserved=pending_state(bot)==state
   if case['kind']=='contrast':success=(result.get('routing') or {}).get('intent')==case['intent'] and 'search_knowledge' not in tools
   else:success=bool(re.search(case['gold']['pattern'],result['reply'],re.I)) and case['gold']['source'] in result['sources'] and 'search_knowledge' in tools and preserved and prior==following and 'phone number' not in result['reply'].lower() and len(generations)<=1
   row={'case':case,'passed':success,'latency_ms':round(elapsed,2),'draft_preserved':preserved,'db_writes':following-prior,'generations':len(generations),'result':result}
   report['cases'].append(row)
   (out/(args.phase+'.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
   print(args.phase,case['id'],'PASS' if success else 'FAIL',round(elapsed),'ms',tools,flush=True)
   client.delete(f'/api/sessions/{sid}')
 values=sorted(x['latency_ms'] for x in report['cases']);policies=[x for x in report['cases'] if x['case']['kind']!='contrast'];recoveries=[x for x in policies if x['case']['kind']=='recovery']
 report['summary']={'passed':sum(x['passed'] for x in report['cases']),'total':len(cases),'policy_request_accuracy':sum(x['passed'] for x in policies)/len(policies),'recovery_success':sum(x['passed'] for x in recoveries)/len(recoveries),'average_ms':sum(values)/len(values),'p95_ms':values[math.ceil(.95*len(values))-1],'search_knowledge_turns':sum('search_knowledge' in x['result']['structured']['tools_called'] for x in policies),'max_generations':max(x['generations'] for x in report['cases'])}
 report['source_hashes']['policy_requests.py']=hashlib.sha256((ROOT/'policy_requests.py').read_bytes()).hexdigest() if (ROOT/'policy_requests.py').exists() else None
 report['baseline_after']=baseline();assert report['baseline_after']==before
 (out/(args.phase+'.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(report['summary']),flush=True)
if __name__=='__main__':main()
