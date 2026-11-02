"""Private full bookings interrupted at multiple stages; actual default model."""
import os,sys,json,sqlite3,hashlib,urllib.request
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/booking-return-20261101'/str(uuid4());(OUT/'private').mkdir(parents=True)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env');os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'private'),LANGSMITH_TRACING='false')
def snapshots():
 result={}
 for label,path in [('production',ROOT/'reservations.sqlite3'),('live',ROOT/'results/voice-turn-ownership-20261029/private/reservations.sqlite3')]:
  with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('select rowid as _rowid,* from reservations order by rowid')]
  baseline=[r for r in rows if str(r.get('request_key','')).startswith('baseline:2026-q4:v1:')]
  digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
  result[label]={'rows':len(rows),'sha256':digest(rows),'baseline_rows':len(baseline),'baseline_sha256':digest(baseline)}
 return result
report={'before':snapshots(),'method':'Actual default model; private chat and voice-text booking flows. No physical microphone/playback.','turns':[]}
import chat_api as api
from fastapi.testclient import TestClient
from customer_identity import local_today
from receptionist import parse_time,parse_date
try:
 model=next(m for m in json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags'))['models'] if m['name']==api.MODEL)
 report['model']={k:model[k] for k in ['name','digest']}
 assert model['name']=='gemma4:e2b-it-qat' and model['digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
 with TestClient(api.app) as client:
  for channel in ['chat','voice']:
   sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
   day=(local_today(b.restaurant)+timedelta(days=5)).isoformat();phone='2025550189' if channel=='chat' else '2025550190'
   steps=[(f'I would like to book a table for two on {day} at seven in the evening.','gate' if channel=='voice' else 'phone')]
   if channel=='voice':steps.extend([('Where can I park?','info'),('No more questions.','gate'),('No.','phone')])
   steps.extend([('Can I bring a birthday cake from outside?','info'),('No more questions.','phone'),
    (f'My phone number is {phone}.','name'),('Do you have gluten-free options?','info'),('No more questions.','name'),
    ('My name is Alex Morgan.','name_check'),('Do you have high chairs?','info'),('No more questions.','name_check'),
    ('Yes, that name is correct.','readback'),('Who is Albert Einstein?','refusal'),('No more questions.','readback'),
    ('What kind of food do you have?','info'),('No more questions.','readback'),
    ('Yes, please confirm the reservation.','confirmed'),('Can I bring my dog inside?','info'),
    ('No more questions, thank you.','survey'),('No thanks.','ended')])
   committed=False
   for text,expect in steps:
    if channel=='chat' and text=='Yes, that name is correct.':continue
    if channel=='chat' and expect=='name_check':expect='readback'
    r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();r=r.json()
    committed=committed or expect=='confirmed'
    rows=[row for row in b.tools.booking_store().list(b.tools.business_id) if row['session_id']==sid]
    calls=[c for c in r['structured']['calls'] if c['component']=='ollama.chat'];lower=r['reply'].lower()
    checks={'one_call_budget':len(calls)<=1,'no_early_or_duplicate_write':len(rows)==int(committed),'no_failure':not r.get('failed')}
    for call in calls:
     x=call['inputs']['kwargs'];checks['settings_unchanged']=x['model']==api.MODEL and x['think'] is False and x['keep_alive']=='30m' and x['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
    if expect in {'phone','name'}:
     checks['next_field']=expect in lower
     if text.startswith('No more questions'):checks['navigation_is_read_only']=not r['structured']['tools_called']
    elif expect=='gate':checks['next_field']='have a reservation' in lower
    elif expect=='name_check':checks['name_preserved']='alex morgan' in lower and 'correct' in lower
    elif expect=='readback':checks['details_preserved']=parse_date(b.slots['date'],today=local_today(b.restaurant)).isoformat()==day and parse_time(b.slots['time'])=='19:00' and b.slots['party_size']==2 and b.slots['name']=='Alex Morgan' and 'is that correct' in lower
    elif expect=='info':checks['answered']=bool(set(r['structured']['tools_called'])&{'get_menu','get_location','search_knowledge','check_dish'}) and lower.endswith('do you have any other questions?')
    elif expect=='refusal':checks['refused_without_losing_draft']=lower.startswith("i can't help you with this question") and parse_time(b.slots.get('time',''))=='19:00'
    elif expect=='confirmed':checks['correct_booking']=bool(rows) and all(rows[0][k]==v for k,v in {'date':day,'time':'19:00','party_size':2,'name':'Alex Morgan','phone':phone}.items())
    elif expect=='survey':checks['survey']=r['survey']['status']=='offered' and not api.sessions[sid].awaiting_reservation_return
    elif expect=='ended':checks['ended']=r['ended'] and r['survey']['status']=='declined'
    report['turns'].append({'channel':channel,'customer':text,'expected':expect,'reply':r['reply'],'spoken_reply':r.get('spoken_reply'),'latency_ms':r['latency_ms'],'tools':r['structured']['tools_called'],'slots':dict(b.slots),'rows':rows,'checks':checks,'pass':all(checks.values())})
    print(channel,expect,all(checks.values()),r['reply'],flush=True)
 report['after']=snapshots();report['reservations_unchanged']=report['before']==report['after'];report['pass']=report['reservations_unchanged'] and all(t['pass'] for t in report['turns'])
finally:
 report['source_hashes']={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['conversation_followup.py','conversation_controls.py','conversation_waits.py','chat_api.py']}
 (OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print('REPORT',OUT/'report.json',flush=True)
if not report['pass']:raise SystemExit(1)
