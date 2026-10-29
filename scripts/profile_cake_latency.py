"""Private development profiling of identical semantic requests and workflow states."""
import os,sys,json,time,copy,hashlib,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
phase=sys.argv[1];out=ROOT/'results/cake-latency-20261028'/phase;out.mkdir(parents=True,exist_ok=False)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(out/'private'),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_PROTOCOL='baseline')
from freeze_customer100 import digest
def model_digest():
 with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as r:return next(m['digest'] for m in json.load(r)['models'] if m['name']=='gemma4:e2b-it-qat')
expected='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
assert model_digest()==expected
baseline=digest(ROOT/'reservations.sqlite3');assert baseline=={'count':1911,'raw_rows_sha256':'ff8283a4fd3bd9cfb6394d82c10119c012e54bfa5d710fbc40d817017d3f8141'}
import receptionist as app,local_audit
from decision_engine import decide,turn_context
# Keep the trusted clock identical between phases, supplied by the server rather than the customer.
import customer_identity
customer_identity.clock_context=lambda r:'Trusted server clock: 2026-10-28T14:00:00-05:00; restaurant timezone: America/Chicago; Today is 2026-10-28, Wednesday. Resolve relative dates using this trusted clock.'
manifest=[
 {'name':'create','text':'We would love dinner for three on February 17, 2027 around seven in the evening.','state':{},'fields':{'party_size':3,'date':'2027-02-17','time':'19:00'}},
 {'name':'mixed_phone','text':'My number is 2025550173, and actually make it four people.','state':{'reserving':True,'slots':{'date':'2027-02-17','time':'19:00','party_size':2},'_awaiting_slot':'phone'},'fields':{'phone':'2025550173','party_size':4}},
 {'name':'mixed_name','text':'Put it under Priya Shah, and make it three people instead.','state':{'reserving':True,'slots':{'date':'2027-02-17','time':'19:00','party_size':2,'phone':'2025550173'},'_awaiting_slot':'name'},'fields':{'name':'Priya Shah','party_size':3}},
 {'name':'update','text':'Please move our booking to February 18, 2027 at nine in the evening, for four people.','state':{'change':'update'},'fields':{'date':'2027-02-18','time':'21:00','party_size':4}},
]
report={'phase':phase,'manifest':manifest,'baseline_before':baseline,'model_digest_before':model_digest(),'rows':[],
 'method':'Actual unchanged default Ollama; identical trusted clock, messages and workflow state across phases. Fresh bot per request. first-context/repeated-context labels do not imply model unload; no inference settings modified.',
 'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}}
with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=15) as r:report['resident_before']=json.load(r)
def save():(out/'profile.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
try:
 for case in manifest:
  for repeat in range(2):
   b=app.Receptionist(app.load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'))
   for k,v in copy.deepcopy(case['state']).items():
    if k=='change':b.reservation_changes.begin(v,'2025550173')
    else:setattr(b,k,v)
   b._last_agent_reply='What phone number should I use?' if case['name']=='mixed_phone' else 'May I have your name?' if case['name']=='mixed_name' else None
   events=[];start=time.perf_counter()
   with local_audit.capture(events),turn_context(b,case['text']):value=decide(b,case['text'])
   elapsed=(time.perf_counter()-start)*1000
   raw=next(e['details']['output'] for e in events if e['name']=='ollama.response_received')
   request=next(e['details']['inputs']['kwargs'] for e in events if e['name']=='ollama.request_prepared')
   assert request['model']=='gemma4:e2b-it-qat' and request['think'] is False and request['keep_alive']=='30m'
   assert request['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
   from customer_identity import local_today,normalize_phone
   checks=[]
   for k,v in case['fields'].items():
    actual=value['fields'].get(k)
    if k=='date':checks.append(str(app.parse_date(actual or '',today=local_today(b.restaurant)))==v)
    elif k=='time':checks.append(app.parse_time(actual or '')==v)
    elif k=='phone':checks.append(normalize_phone(actual or '','US')==normalize_phone(v,'US'))
    else:checks.append(actual==v)
   correct=value['relevance']=='relevant' and value['intent']=='reservation' and all(checks)
   row={'case':case['name'],'context_run':'first' if repeat==0 else 'repeat','ms':elapsed,'correct':correct,'decision':value,
    'timings_ms':{k:raw.get(k,0)/1e6 for k in ['load_duration','prompt_eval_duration','eval_duration','total_duration']},
    'prompt_tokens':raw.get('prompt_eval_count'),'output_tokens':raw.get('eval_count'),'request':request,'events':events}
   row['http_validation_overhead_ms']=elapsed-row['timings_ms']['total_duration']
   report['rows'].append(row);save();print(case['name'],row['context_run'],round(elapsed),correct,flush=True)
finally:
 report['baseline_after']=digest(ROOT/'reservations.sqlite3');assert report['baseline_after']==baseline
 report['model_digest_after']=model_digest();assert report['model_digest_after']==expected;save()
