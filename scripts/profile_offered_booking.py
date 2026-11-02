"""Same-input gate timings using normal API turns and fresh private databases."""
import os,sys,json,time,pathlib,statistics,math
from uuid import uuid4
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,os.getenv('PROFILE_APPLICATION_ROOT',str(ROOT)))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
label=sys.argv[1];out=ROOT/'results/spoken-booking-fixes-20261030'/label;out.mkdir(parents=True,exist_ok=False)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(out),LANGSMITH_TRACING='false')
import chat_api as api
from fastapi.testclient import TestClient
result={'label':label,'runs':[],'conditions':'Fresh application; model residency not forced. First gate and subsequent warm gates reported separately. Identical API inputs and offered state per run.'}
with TestClient(api.app) as client:
 for i in range(3):
  sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];turns=[]
  for text in ['Are any tables available for two on November 9, 2026 at seven in the evening?','I would like to book that table.','No, no.']:
   start=time.perf_counter();r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();data=r.json()
   turns.append({'text':text,'elapsed_ms':(time.perf_counter()-start)*1000,'response':data})
  result['runs'].append(turns);print(i,turns[-1]['elapsed_ms'],turns[-1]['response']['reply'],flush=True)
  (out/'profile.json').write_text(json.dumps(result,indent=2))
values=sorted(t[-1]['elapsed_ms'] for t in result['runs']);result['gate_stats']={'n':len(values),'average_ms':statistics.mean(values),'p50_ms':statistics.median(values),'p95_ms':values[math.ceil(.95*len(values))-1],'first_gate_ms':result['runs'][0][-1]['elapsed_ms'],'warm_average_ms':statistics.mean(t[-1]['elapsed_ms'] for t in result['runs'][1:])}
(out/'profile.json').write_text(json.dumps(result,indent=2));print(result['gate_stats'],flush=True)
