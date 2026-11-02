"""Same-state private benchmark for the polite booking request / No gate."""
import hashlib, importlib.util, json, math, os, sqlite3, statistics, sys, time, urllib.request
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/cached-cue-gate-20261101';OUT.mkdir(parents=True,exist_ok=True)
phase=sys.argv[1]
(OUT/phase/'private').mkdir(parents=True,exist_ok=True)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/phase/'private'),LANGSMITH_TRACING='false')
if phase=='before':
    spec=importlib.util.spec_from_file_location('booking_fastpath',OUT/'before-sources/booking_fastpath.py')
    module=importlib.util.module_from_spec(spec);sys.modules['booking_fastpath']=module;spec.loader.exec_module(module)
from fastapi.testclient import TestClient
import chat_api as api

def snapshots():
    result={}
    for label,path in [('production',ROOT/'reservations.sqlite3'),('live_private',ROOT/'results/voice-turn-ownership-20261029/private/reservations.sqlite3')]:
        with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('select rowid as _rowid,* from reservations order by rowid')]
        baseline=[r for r in rows if str(r.get('request_key','')).startswith('baseline:2026-q4:v1:')]
        digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        result[label]={'count':len(rows),'sha256':digest(rows),'baseline_count':len(baseline),'baseline_sha256':digest(baseline)}
    return result

def model():
    models=json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags'))['models']
    m=next(m for m in models if m['name']==api.MODEL)
    assert m['name']=='gemma4:e2b-it-qat' and m['digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
    return {'tag':m['name'],'digest':m['digest']}

cases=[
    ("Thanks. I'd like to book a table for two on November 10th, 2026, at 7 in the evening.","No, I don't have a reservation."),
    ("Thank you, we'd like to book a table for two on November tenth, twenty twenty-six, at seven in the evening.","No, I do not have a reservation."),
    ("Okay, I would like to book a table for two on November 10, 2026 at seven pm.",'No.'),
]
report={'phase':phase,'model_before':model(),'reservations_before':snapshots(),'rows':[],
    'conditions':'Same private voice workflow and recent pets context per case. First request is application-cold; existing Ollama service untouched. Subsequent requests application-warm. Model load times recorded, no forced unload.'}
def save(): (OUT/f'{phase}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
try:
    with TestClient(api.app) as client:
        for repeat in range(2):
            for index,(original,answer) in enumerate(cases):
                sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];session=api.sessions[sid];b=session.bot
                b._last_user_text='Can I bring my dog inside?';b._last_agent_reply='Pets are not permitted in the indoor dining area.'
                from routing import Decision
                b.last_decision=Decision('policy','rule')
                def turn(text):
                    r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();return r.json()
                gate=turn(original);assert session.pending_reservation_request and 'reservation' in gate['reply']
                start=time.perf_counter();r=turn(answer);elapsed=(time.perf_counter()-start)*1000
                calls=[c for c in r['structured']['calls'] if c['component']=='ollama.chat']
                for call in calls:
                    cfg=call['inputs']['kwargs']
                    assert cfg['model']=='gemma4:e2b-it-qat' and cfg['think'] is False and cfg['keep_alive']=='30m' and cfg['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
                checks={'phone_requested':'phone' in r['reply'].lower(),'details_preserved':b.slots.get('party_size')==2 and str(api.engine.parse_date(b.slots.get('date','')))=='2026-11-10' and api.engine.parse_time(b.slots.get('time',''))=='19:00','no_write':not b.tools.booking_store().list(b.tools.business_id),'no_confirmation_bypass':not b.awaiting_confirmation,'one_generation_budget':len(calls)<=1}
                row={'case':index+1,'repeat':repeat,'original':original,'answer':answer,'reply':r['reply'],'backend_ms':r['latency_ms'],'roundtrip_ms':elapsed,'model_calls':len(calls),'tools':r['structured']['tools_called'],'checks':checks,'pass':all(checks.values()),'model':[ {k:c.get('output',{}).get(k) for k in ['load_duration','prompt_eval_duration','eval_duration','total_duration','prompt_eval_count','eval_count']} for c in calls]}
                report['rows'].append(row);save();print(json.dumps(row),flush=True)
                client.delete('/api/sessions/'+sid)
finally:
    report['model_after']=model();report['reservations_after']=snapshots()
    report['reservations_unchanged']=report['reservations_before']==report['reservations_after']
    values=sorted(r['backend_ms'] for r in report['rows'])
    report['summary']={'n':len(values),'mean_ms':statistics.mean(values),'p50_ms':statistics.median(values),'p95_ms':values[math.ceil(len(values)*.95)-1],'passed':sum(r['pass'] for r in report['rows'])} if values else {}
    save();print(json.dumps(report['summary']),flush=True)
