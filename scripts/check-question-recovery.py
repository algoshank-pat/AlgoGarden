"""Replay the live question-navigation failure privately with the default model."""
import os,sys,json,sqlite3,hashlib,urllib.request
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/question-recovery-20261101'/str(uuid4());(OUT/'private').mkdir(parents=True)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'private'),LANGSMITH_TRACING='false')

def snapshots():
    result={}
    for label,path in [('production',ROOT/'reservations.sqlite3'),('live',ROOT/'results/voice-turn-ownership-20261029/private/reservations.sqlite3')]:
        with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('select rowid as _rowid,* from reservations order by rowid')]
        baseline=[r for r in rows if str(r.get('request_key','')).startswith('baseline:2026-q4:v1:')]
        digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        result[label]={'rows':len(rows),'sha256':digest(rows),'baseline_rows':len(baseline),'baseline_sha256':digest(baseline)}
    return result

report={'before':snapshots(),'method':'Current API with actual default Ollama model, private seeded drafts and reservation writes; chat and voice-text, no microphone/audio playback.','turns':[]}
import chat_api as api
from fastapi.testclient import TestClient
from customer_identity import local_today
from customer_intent import ADDITIONAL_QUESTIONS_REPLY
from knowledge import FALLBACK
try:
    model=next(m for m in json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags'))['models'] if m['name']==api.MODEL)
    report['model']={k:model[k] for k in ('name','digest')}
    assert model['name']=='gemma4:e2b-it-qat' and model['digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
    with TestClient(api.app) as client:
        for channel in ['chat','voice']:
            sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
            b.reserving=True;b.awaiting_confirmation=True
            b.slots={'name':'Private Guest','phone':'2025550173' if channel=='chat' else '2025550174','date':(local_today(b.restaurant)+timedelta(days=1)).isoformat(),'time':'20:00','party_size':5}
            expected=dict(b.slots);b._last_agent_reply=f"Table for 5 on {expected['date']} at 20:00, under Private Guest. Is that correct?"
            prompts=["Yeah, but where's your restaurant?",'No more questions.','Yes.',
                'Mm can do you have a high chair? My sister needs one.','Nos','No.',
                'No, I have a question.','No, I have a question.','Where can I park?',
                'No more questions, thank you.','No thanks.']
            previous=None
            for step,text in enumerate(prompts,1):
                response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});response.raise_for_status();r=response.json()
                calls=[c for c in r['structured']['calls'] if c['component']=='ollama.chat']
                rows=[row for row in b.tools.booking_store().list(b.tools.business_id) if row['session_id']==sid]
                checks={'one_call_budget':len(calls)<=1,'no_early_duplicate_write':len(rows)==(0 if step<3 else 1),'no_failure':not r.get('failed')}
                for call in calls:
                    x=call['inputs']['kwargs'];checks['settings_unchanged']=x['model']==api.MODEL and x['think'] is False and x['keep_alive']=='30m' and x['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
                if step==1:checks['address_answered']='get_location' in r['structured']['tools_called']
                if step==2:checks['readback_preserved']='Is that correct?' in r['reply'] and b.slots==expected
                if step==3:checks['correct_booking']=all(rows[0][k]==v for k,v in expected.items())
                if step==4:checks['grounded_high_chair']='high chairs are available' in r['reply'].lower() and bool(r['sources'])
                if step==6 and previous['reply']==FALLBACK:checks['staff_decline_recovers']=r['reply']==ADDITIONAL_QUESTIONS_REPLY and not r['structured']['calls']
                if step in {7,8}:checks['invitation']=r['reply']==ADDITIONAL_QUESTIONS_REPLY and not r['structured']['calls'] and not r['structured']['tools_called']
                if step==9:checks['parking_answered']='get_location' in r['structured']['tools_called']
                if step==10:checks['survey_offered']=r['survey']['status']=='offered'
                if step==11:checks['ended']=r['ended'] and r['survey']['status']=='declined'
                report['turns'].append({'channel':channel,'step':step,'customer':text,'reply':r['reply'],'spoken_reply':r.get('spoken_reply'),'tools':r['structured']['tools_called'],'latency_ms':r['latency_ms'],'checks':checks,'pass':all(checks.values())})
                previous=r
                print(channel,step,all(checks.values()),r['latency_ms'],r['reply'],flush=True)
    report['after']=snapshots();report['reservations_unchanged']=report['before']==report['after']
    report['pass']=report['reservations_unchanged'] and all(t['pass'] for t in report['turns'])
finally:
    report['source_hashes']={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['chat_api.py','customer_intent.py','conversation_followup.py','conversation_controls.py','information_requests.py']}
    (OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('REPORT',OUT/'report.json',flush=True)
if not report['pass']:raise SystemExit(1)
