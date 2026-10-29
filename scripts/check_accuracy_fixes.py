"""Paired development checks; private data, actual default model, unchanged inference settings."""
import os,sys,json,time,hashlib,sqlite3,statistics,math,copy,urllib.request
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/accuracy-fixes-20261028'
phase=sys.argv[1];folder=OUT/phase;folder.mkdir(parents=True,exist_ok=False)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(folder/'private'),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline')
sys.path.insert(0,str(ROOT))
from freeze_customer100 import digest
def baseline():return digest(ROOT/'reservations.sqlite3')
def model():
 r=json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15))
 return next(m['digest'] for m in r['models'] if m['name']=='gemma4:e2b-it-qat')
expected='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
assert model()==expected
before=baseline();assert before=={'count':1911,'raw_rows_sha256':'ff8283a4fd3bd9cfb6394d82c10119c012e54bfa5d710fbc40d817017d3f8141'}
import chat_api as api,knowledge
from fastapi.testclient import TestClient
knowledge.ingest(api.restaurant.get('knowledge_id','malnad_mane'))
report={'phase':phase,'model':api.MODEL,'model_digest_before':model(),'baseline_before':before,'cases':[],
 'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [*ROOT.glob('*.py'),*ROOT.glob('restaurants/*.yaml')]},
 'method':'Identical authored text and explicit workflow states, actual default Ollama and tools, private SQLite/RAG/audit. Voice-text API; no microphone or audio. Cold first retrieval and subsequent warm requests included.'}
def save():(folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
def rows():
 store=api.engine.Tools(api.restaurant).booking_store()
 return store.list(api.restaurant['id'])
def stats(v):
 v=sorted(v);return {'n':len(v),'average_ms':statistics.mean(v),'p50_ms':statistics.median(v),'p95_ms':v[math.ceil(len(v)*.95)-1]}
manifest=[
 ('price_dosa','How much is Masala Dosa?',['12'],'check_dish'),
 ('price_coffee','What would a Filter Coffee cost me?',['4'],'check_dish'),
 ('dairy','Does Masala Dosa contain dairy?',['dairy'],'check_dish'),
 ('allergens','What allergens are in Bisi Bele Bath?',['dairy','peanuts'],'check_dish'),
 ('dinner','When does dinner service start?',['18:00'],'get_hours'),
 ('lunch','At what time do you finish serving lunch?',['15:00'],'get_hours'),
 ('dinner_paraphrase','From when can we eat dinner?',['18:00'],'get_hours'),
 ('terrier','Is a well-behaved terrier allowed in the dining room?',['not permitted'],'search_knowledge'),
 ('pet_paraphrase','Would my little dachshund be welcome inside?',['not permitted'],'search_knowledge'),
 ('cake','May we bring a cake from our own bakery?',['cake'],'search_knowledge'),
 ('location','What is your street address?',['not configured'],'get_location'),
 ('unrelated','Who was Isaac Newton?',["can’t help","can't help"],None),
]
with TestClient(api.app) as c:
 def new(channel):return c.post('/api/sessions',json={'channel':channel}).json()['session_id']
 def turn(sid,text,label):
  b=api.sessions[sid].bot;old=copy.deepcopy(b.slots);count=len(rows());started=time.perf_counter()
  res=c.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});res.raise_for_status();r=res.json()
  calls=r['structured']['calls'];n=sum(x['component']=='ollama.chat' for x in calls)
  row={'label':label,'channel':api.sessions[sid].channel,'input':text,'reply':r['reply'],'ms':(time.perf_counter()-started)*1000,'generations':n,'calls':calls,'tools':r['structured']['tools_called'],'sources':r['sources'],'slots_before':old,'slots_after':copy.deepcopy(b.slots),'rows_before':count,'rows_after':len(rows()),'confirming':b.awaiting_confirmation}
  assert n<=1;return row
 try:
  for channel in ['chat','voice']:
   for state in ['idle','phone']:
    for label,text,words,tool in manifest:
     sid=new(channel);b=api.sessions[sid].bot
     if state=='phone':b.reserving=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':2};b._awaiting_slot='phone'
     row=turn(sid,text,f'{label}-{state}')
     answer=row['reply'].lower();matched=(any(w in answer for w in words) if label=='unrelated' else all(w in answer for w in words))
     row['passed']=matched and (tool is None or tool in row['tools']) and row['rows_before']==row['rows_after'] and row['slots_before']==row['slots_after'] and not any(t in row['tools'] for t in ['book_table','change_reservation','recognize_customer','lookup_reservations_by_phone'])
     report['cases'].append(row);print(channel,row['label'],row['passed'],flush=True);save();c.post(f'/api/sessions/{sid}/stop')
  for channel in ['chat','voice']:
   for i,request in enumerate(['Any chance you can fit four of us in on February 17, 2027 at seven in the evening?','I am hoping to book for three, February 18, 2027, half past eight in the evening.']):
    sid=new(channel);b=api.sessions[sid].bot;group=[]
    def step(t):
     r=turn(sid,t,f'booking-{i}');report['cases'].append(r);group.append(r);save();return r
    r=step(request)
    if api.sessions[sid].pending_reservation_request is not None:step('No')
    step('My phone number is two zero two five five five zero one seven three.')
    step('My name is Sam Miller.')
    expected_date=f'2027-02-{17+i}';expected_time=['19:00','20:30'][i]
    from receptionist import parse_date,parse_time
    ready=b.awaiting_confirmation and str(parse_date(b.slots.get('date','')))==expected_date and parse_time(b.slots.get('time',''))==expected_time and b.slots.get('party_size')==[4,3][i] and not rows()
    # Read-only interruption must preserve all details and require readback again.
    draft=copy.deepcopy(b.slots);step('When does dinner service start?');preserved=b.slots==draft and not rows()
    step('Let us go back to my reservation.')
    # Change only time. Date and party size must survive this correction.
    step('No, change the time to nine in the evening instead.')
    corrected=b.awaiting_confirmation and str(parse_date(b.slots.get('date','')))==expected_date and parse_time(b.slots.get('time',''))=='21:00' and b.slots.get('party_size')==[4,3][i] and not rows()
    step('Yes please')
    saved=rows();ok=ready and preserved and corrected and len(saved)==1 and saved[0]['date']==expected_date and saved[0]['time']=='21:00' and saved[0]['name']=='Sam Miller'
    for r in group:r['passed']=bool(ok)
    report.setdefault('booking_checks',[]).append({'channel':channel,'input':request,'readback_ready':ready,'interruption_preserved':preserved,'time_only_correction_preserved_date':corrected,'passed':ok});save()
    # Delete only this private test's rows between identical cases.
    with sqlite3.connect(api.engine.booking_db.DB_PATH) as db:db.execute('DELETE FROM reservations')
    c.post(f'/api/sessions/{sid}/stop')
 finally:
  from reservation_mcp_client import close_mcp_connections
  close_mcp_connections();report['baseline_after']=baseline();assert report['baseline_after']==before
  report['model_digest_after']=model();assert report['model_digest_after']==expected
  report['latency']=stats([r['ms'] for r in report['cases']]);report['correct']=sum(r.get('passed',False) for r in report['cases']);report['total']=len(report['cases']);save()
print(json.dumps({k:report[k] for k in ['correct','total','latency','booking_checks']},indent=2),flush=True)
