"""Private, same-state before/after voice-gate benchmark with actual Ollama."""
import hashlib,importlib.util,json,math,os,pathlib,statistics,sys,time
from uuid import uuid4
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
OUT=ROOT/'results/voice-turn-ownership-20261029'
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'private'),LANGSMITH_TRACING='false')
phase=sys.argv[1]
if phase=='before':
 spec=importlib.util.spec_from_file_location('booking_fastpath',OUT/'before-sources/booking_fastpath.py');mod=importlib.util.module_from_spec(spec);sys.modules['booking_fastpath']=mod;spec.loader.exec_module(mod)
from fastapi.testclient import TestClient
import chat_api as api
rows=[]
original='I would like to do a reservation tomorrow.'
with TestClient(api.app) as client:
 for repeat in range(3):
  for answer in ['No.','No, no.',"No, I do not have a reservation."]:
   sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];session=api.sessions[sid]
   session.pending_reservation_request=original
   start=time.perf_counter();response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':answer});elapsed=(time.perf_counter()-start)*1000;response.raise_for_status();r=response.json()
   calls=[x for x in r['structured']['calls'] if x['component']=='ollama.chat'];model=[]
   for call in calls:
    data=call.get('output') or {}; kw=call.get('inputs',{}).get('kwargs',{})
    model.append({k:data.get(k) for k in ['load_duration','prompt_eval_duration','eval_duration','total_duration','prompt_eval_count','eval_count']})
    model[-1]['settings']={k:kw.get(k) for k in ['model','think','keep_alive','options']}
   row={'phase':phase,'answer':answer,'repeat':repeat,'application_condition':'fresh-process-first-turn' if not rows else 'warm-process','model_condition':'existing-local-service; see measured load_duration','elapsed_ms':elapsed,'reply':r['reply'],'slots':session.bot.slots,'model_calls':len(calls),'model':model,'tools':r['structured']['tools_called']}
   assert 'phone' in r['reply'].lower() and session.bot.slots.get('date')=='tomorrow' and not session.bot.awaiting_confirmation
   assert not set(row['tools'])&{'book_table','update_reservation','cancel_reservation'}
   rows.append(row);(OUT/f'gate-{phase}.json').write_text(json.dumps(rows,indent=2));print(phase,answer,round(elapsed,2),'ms',len(calls),'generations',flush=True)
   client.delete('/api/sessions/'+sid)
print('COMPLETE',phase,flush=True)
